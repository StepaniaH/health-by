"""同步引擎：单飞锁、令牌保鲜与轮换、错误语义、后台任务与进度。"""

from __future__ import annotations

from health_by.i18n import t

import logging
import sqlite3
import threading
import time
from contextlib import closing

import httpx

from . import db, storage
from .config import Config, env_value
from .models import DaySnapshot, metric_values
from .providers import get_provider
from .providers.base import ProviderAuthError, ProviderError
from .secrets import SecretError, SecretStore
from .util import iso_range, iso_today, parse_iso, parse_utc, shift_iso, utc_now_iso

TOKEN_REFRESH_MARGIN_SECONDS = 300  # 过期前 5 分钟主动刷新
AUTOSYNC_BACKFILL_LIMIT_DAYS = 30  # 自动同步一次最多回补多少天
MAX_REPORTED_FAILED_JOBS = 5  # 批次结果里最多列出几个失败任务名
DRIFT_BASELINE_MIN_FIELDS = 3  # 少于这个字段数不做漂移判断（样本太薄，没意义）
DRIFT_LOST_FIELDS = 2  # 同一日期比上次少这么多字段，且没有请求错误 → 判为疑似解析失败
LOG = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: Config, conn):
        self.cfg = cfg
        self.conn = conn
        self.secrets = SecretStore(cfg.secret_key_path)
        self._lock = threading.RLock()
        self._clear_lock = threading.RLock()
        self._clear_generation = 0
        self._queue_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._pending: list[tuple[str, tuple[int, int]]] = []
        self._active: set[str] = set()
        self._inflight: dict[str, tuple[int, int]] = {}  # 覆盖排队中 + 协调器已取走待分派 + 正在处理的日期及其入队代际
        self._outstanding = 0  # 已接受但尚未处理完的日期数（拿 _queue_lock 保护）
        # 按日期串行化写入：后台 worker 直接调 _sync_day 时不经过 _lock，
        # 同一天可能被前后台两个线程同时处理，各自用独立连接 DELETE+INSERT。
        # 值是 [锁, 引用计数]：只按「有人出入这一天」计数，不能简单 clear()——
        # 那样会把别的线程正要用的锁对象删掉，于是同一天出现两把不同的锁，
        # 互斥当场失效。计数归零才删，新来的调用者才会重新建。
        self._date_locks: dict[str, list] = {}
        self._date_locks_guard = threading.Lock()
        self._coordinator: threading.Thread | None = None
        self._shutdown = threading.Event()
        self._progress_lock = threading.Lock()
        self.workers = max(1, int(env_value("SYNC_WORKERS", "2")))
        self._http: httpx.Client | None = None
        self.progress: dict = {
            "running": False,
            "phase": "idle",  # idle | starting | fetching | syncing | done | error
            "days_total": 0,
            "days_completed": 0,
            "active": [],
            "day": None,
            "completed": 0,
            "total": 0,
            "current_key": None,
            "message": None,
        }
        self.last_result: dict | None = None

    # ---------- 基础设施 ----------

    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=15)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def shutdown(self, timeout: float = 5.0) -> None:
        """停止后台协调器与 worker，供服务退出/测试清理使用。"""
        self._shutdown.set()
        coordinator = self._coordinator
        if coordinator is not None and coordinator.is_alive():
            coordinator.join(timeout=timeout)
        with self._queue_lock:
            for date, gen in self._pending:
                if self._inflight.get(date) == gen:
                    del self._inflight[date]
            self._pending.clear()
            self._outstanding = len(self._inflight)

    def _current_clear_gen(self, conn: sqlite3.Connection | None = None) -> tuple[int, int]:
        """组合进程内代际与库内持久化代际，同时防同进程并发与跨进程同步回写。"""
        target = conn or self.conn
        return (self._clear_generation, storage.get_clear_generation(target))

    def clear_all(self, conn: sqlite3.Connection | None = None) -> None:
        """与在途后台同步协调清空数据库：递增代际、清理待执行队列与内存结果，阻止旧任务回写。"""
        with self._clear_lock:
            self._clear_generation += 1
            with self._queue_lock:
                for date, gen in self._pending:
                    if self._inflight.get(date) == gen:
                        del self._inflight[date]
                self._pending.clear()
                self._outstanding = len(self._inflight)
            with self._progress_lock:
                self.last_result = None
            storage.delete_all_data(conn or self.conn)

    def provider_name(self, conn: sqlite3.Connection | None = None) -> str:
        name = str(storage.get_setting(conn or self.conn, "provider", "google-health"))
        if name == "demo":
            LOG.warning("Legacy provider 'demo' was removed; falling back to 'google-health'.")
            return "google-health"
        return name

    # ---------- 凭据 ----------

    def credentials_for(self, name: str, conn: sqlite3.Connection | None = None) -> dict | None:
        raw = storage.load_credentials_raw(conn or self.conn, name)
        if raw is None:
            return None
        try:
            return self.secrets.decrypt_json(raw)
        except SecretError as exc:
            LOG.error("Failed to decrypt credentials for '%s': %s", name, exc)
            return None

    def save_credentials_for(self, name: str, credentials: dict, conn: sqlite3.Connection | None = None) -> None:
        storage.save_credentials_raw(conn or self.conn, name, self.secrets.encrypt_json(credentials))

    def auth_status(self) -> dict:
        name = self.provider_name()
        provider = get_provider(name)
        credentials = self.credentials_for(name)
        status = provider.auth_status(credentials)
        return {
            "provider": name,
            "display_name": provider.display_name,
            "auth_kind": provider.auth_kind,
            **status,
        }

    def _fresh_credentials(self, name: str, provider, conn: sqlite3.Connection | None = None) -> dict:
        """取凭据并在临期时刷新（轮换后原子写回）。"""
        credentials = self.credentials_for(name, conn)
        if not credentials:
            raise ProviderAuthError(t('server.engine._fresh_credentials.865508731'))
        if provider.auth_kind != "oauth":
            return credentials
        tokens = credentials.get("tokens") or {}
        expires_at = tokens.get("expires_at")
        needs_refresh = tokens.get("access_token") is None or (
            isinstance(expires_at, (int, float)) and expires_at - time.time() < TOKEN_REFRESH_MARGIN_SECONDS
        )
        if not needs_refresh:
            return credentials
        try:
            refreshed = provider.refresh(credentials=credentials, http=self.http())
        except ProviderAuthError:
            credentials["needs_reconnect"] = True
            self.save_credentials_for(name, credentials, conn)
            raise
        merged_tokens = {**tokens, **refreshed}
        # refresh token 轮换：未返回新值时保留旧值
        if not merged_tokens.get("refresh_token"):
            merged_tokens["refresh_token"] = tokens.get("refresh_token")
        credentials["tokens"] = merged_tokens
        credentials["needs_reconnect"] = False
        self.save_credentials_for(name, credentials, conn)
        return credentials

    # ---------- 同步（公开入口：单飞锁） ----------

    def sync_day(self, date: str) -> DaySnapshot:
        with self._lock:
            return self._sync_day(date)[0]

    def sync_days(self, dates: list[str]) -> list[dict]:
        with self._lock:
            return self._sync_days(dates)

    @staticmethod
    def _drift_warnings(date: str, new_fields: int, baseline: int | None, raw) -> list[dict]:
        """识别「请求成功但读不出东西」的静默失败。

        翻译层有 20 处静默 `return None`，上游一旦改字段名或改包装结构，
        这些 job 仍然返回 200（所以 `raw.errors` 是空的、状态是 ok），
        但解析结果是 None。叠加 store_day 的保守合并（None 保留旧值），
        后果是同步永远显示绿色、数据却冻结在改坏的那天。

        所以这里拿「同一日期上次入库的字段数」当基线：字段数明显变少、
        且没有任何请求级错误，就说明不是"上游没数据"，而是"我们读不出来"。
        """
        if raw.errors or baseline is None or baseline < DRIFT_BASELINE_MIN_FIELDS:
            return []  # baseline=None 表示没有可比基线（老库升级上来），跳过而不是猜
        lost = baseline - new_fields
        if lost < DRIFT_LOST_FIELDS:
            return []
        return [
            {
                "kind": "parse_drift",
                "message": (
                    t('server.engine._drift_warnings.a6f3f6f56', date=date, new_fields=new_fields, baseline=baseline)
                ),
                "date": date,
                "previous_fields": baseline,
                "current_fields": new_fields,
            }
        ]

    def _acquire_date_lock(self, date: str) -> threading.Lock:
        with self._date_locks_guard:
            entry = self._date_locks.get(date)
            if entry is None:
                entry = [threading.Lock(), 0]
                self._date_locks[date] = entry
            entry[1] += 1  # 持有者与等待者都算，保证归零前不会被删
            return entry[0]

    def _release_date_lock(self, date: str) -> None:
        with self._date_locks_guard:
            entry = self._date_locks.get(date)
            if entry is None:
                return
            entry[1] -= 1
            if entry[1] <= 0:
                del self._date_locks[date]

    def _sync_day(
        self,
        date: str,
        conn=None,
        *,
        expected_clear_gen: tuple[int, int] | None = None,
    ) -> tuple[DaySnapshot, bool, dict]:
        # 同一天同一时刻只允许一个线程在跑（前台手动同步 vs 后台 worker）。
        lock = self._acquire_date_lock(date)
        try:
            with lock:
                return self._sync_day_locked(date, conn, expected_clear_gen=expected_clear_gen)
        finally:
            self._release_date_lock(date)

    @staticmethod
    def _snapshot_has_data(snapshot: DaySnapshot, metrics: dict[str, float]) -> bool:
        """区分「值得留档落库」与「仅靠主标量判定」：含日内序列、心率极值、小睡或当日告警/ECG 的日子不可丢弃。"""
        if metrics or snapshot.sleep or snapshot.workouts:
            return True
        if snapshot.activity.steps_intraday or snapshot.heart.hr_intraday:
            return True
        if any(
            value is not None
            for value in (
                snapshot.heart.hr_min,
                snapshot.heart.hr_max,
                snapshot.heart.hr_avg,
                snapshot.heart.hrv_deep_rmssd_ms,
                snapshot.heart.hrv_entropy,
                snapshot.heart.non_rem_hr,
                snapshot.heart.spo2_min,
                snapshot.heart.spo2_max,
                snapshot.heart.skin_temp_nightly_c,
                snapshot.heart.skin_temp_baseline_c,
                snapshot.heart.skin_temp_stddev_30d_c,
            )
        ):
            return True
        if (snapshot.alerts.irregular_rhythm_alerts or 0) > 0:
            return True
        # ECG 查的是 90 天历史窗：仅当存在测量日期落在 snapshot.date 当天的 ECG 时才视作当日有数据，
        # 避免过去 90 天内的一条旧 ECG 让后续空白日期被误建档。
        if any(
            isinstance(reading, dict) and str(reading.get("start_time") or "")[:10] == snapshot.date
            for reading in (snapshot.alerts.ecg_readings or [])
        ):
            return True
        return bool(snapshot.naps_count or snapshot.naps_minutes)

    def _sync_day_locked(
        self,
        date: str,
        conn=None,
        *,
        expected_clear_gen: tuple[int, int] | None = None,
    ) -> tuple[DaySnapshot, bool, dict]:
        target = conn or self.conn
        with self._clear_lock:
            current_gen = self._current_clear_gen(target)
            clear_gen = current_gen if expected_clear_gen is None else expected_clear_gen
            if current_gen != clear_gen:
                return (
                    DaySnapshot(date=date, source=self.provider_name(target), generated_at=utc_now_iso()),
                    False,
                    {
                        "status": "cancelled",
                        "partial": False,
                        "failed_count": 0,
                        "failed_jobs": [],
                        "stats": {"total": 0, "succeeded": 0},
                        "warnings": [],
                    },
                )
        name = self.provider_name(target)
        provider = get_provider(name)
        started_at = utc_now_iso()
        credentials = None
        if provider.auth_kind == "oauth":
            with self._refresh_lock:  # 并发时只让一个线程触发刷新
                credentials = self._fresh_credentials(name, provider, target)
        raw = provider.fetch_raw(
            date,
            credentials=credentials,
            http=self.http(),
            on_progress=self._fetch_progress,
        )
        snapshot = provider.translate(raw)
        metrics = metric_values(snapshot)
        total = int(raw.stats.get("total") or 0)
        succeeded_value = raw.stats.get("succeeded")
        succeeded = total if succeeded_value is None else int(succeeded_value)
        stored = self._snapshot_has_data(snapshot, metrics)
        with self._clear_lock:
            managed_tx = not target.in_transaction
            if managed_tx:
                target.execute("BEGIN IMMEDIATE")
            try:
                # 拉取期间若发生清空数据（同进程或跨进程），放弃本次回写，避免已删除记录重新出现。
                if self._current_clear_gen(target) != clear_gen:
                    if managed_tx and target.in_transaction:
                        target.rollback()
                    return (
                        snapshot,
                        False,
                        {
                            "status": "cancelled",
                            "partial": False,
                            "failed_count": 0,
                            "failed_jobs": [],
                            "stats": {"total": total, "succeeded": succeeded},
                            "warnings": [],
                        },
                    )
                # 基线取「该日期上一次同步实际解析出多少字段」，而不是 daily_metrics 的
                # 累积行数（那个只增不减，会让同一次漂移被反复报告且文案失真）。
                baseline = storage.last_field_count(target, date)
                if stored:
                    storage.store_day(target, snapshot, raw, commit=False)
                warnings = self._drift_warnings(date, len(metrics), baseline, raw)
                # 「有值但解析失败」与「上游没数据」是两件事：前者记 warning，后者不记。
                if raw.parse_issues:
                    warnings = [
                        *warnings,
                        {
                            "kind": "parse_issue",
                            "message": (
                                t('server.engine._sync_day_locked.1eff58b7e', date=date, p2=len(raw.parse_issues))
                            ),
                            "date": date,
                            "sample": raw.parse_issues[:MAX_REPORTED_FAILED_JOBS],
                        },
                    ]
                # 只有请求成功且没有数据时才是 empty；失败不能推进自动补抓游标。
                has_failures = bool(raw.errors) or succeeded < total
                if has_failures:
                    status = "partial" if stored and succeeded > 0 else "error"
                else:
                    status = "ok" if stored else "empty"
                storage.record_sync_run(
                    target,
                    provider=name,
                    started_at=started_at,
                    finished_at=utc_now_iso(),
                    date=date,
                    total=total,
                    succeeded=succeeded,
                    status=status,
                    errors=raw.errors or None,
                    warnings=warnings or None,
                    fields=len(metrics),
                    commit=False,
                )
                if managed_tx:
                    target.commit()
            except BaseException:
                if managed_tx and target.in_transaction:
                    target.rollback()
                raise
        info = {
            "status": status,
            "partial": status == "partial",
            "failed_count": len(raw.errors),
            "failed_jobs": [str(item.get("key")) for item in raw.errors[:MAX_REPORTED_FAILED_JOBS]],
            "stats": {"total": total, "succeeded": succeeded},
            "warnings": warnings,
        }
        if status == "error":
            info["error"] = t('server.engine._sync_day_locked.269f45686', succeeded=succeeded, total=total)
        return snapshot, stored, info

    def _sync_days(
        self,
        dates: list[str],
        conn: sqlite3.Connection | None = None,
        *,
        expected_clear_gen: tuple[int, int] | None = None,
    ) -> list[dict]:
        target = conn or self.conn
        with self._clear_lock:
            batch_gen = self._current_clear_gen(target) if expected_clear_gen is None else expected_clear_gen
        results: list[dict] = []
        for index, date in enumerate(dates):
            self._progress(phase="syncing", day=date, days_completed=index, days_total=len(dates))
            try:
                snapshot, stored, info = self._sync_day(date, conn=conn, expected_clear_gen=batch_gen)
                results.append(
                    {
                        "date": date,
                        "steps": snapshot.activity.steps,
                        "sleep_minutes": snapshot.sleep.total_minutes if snapshot.sleep else None,
                        **info,
                    }
                )
            except ProviderAuthError:
                raise  # 授权问题：中止整个批次
            except ProviderError as exc:
                results.append({"date": date, "status": "error", "error": str(exc)})
        self._progress(phase="done", days_completed=len(dates), days_total=len(dates), day=None)
        return results

    # ---------- 后台任务（队列 + 并发） ----------

    def is_running(self) -> bool:
        with self._queue_lock:
            coordinator = self._coordinator
            return bool(self._inflight) or bool(self._pending) or bool(coordinator is not None and coordinator.is_alive())

    def start_background(self, dates: list[str]) -> dict:
        """把日期加入同步队列；已有任务在跑时继续排队，不再拒绝。

        返回的 `outstanding` 是**当前在飞的总数**（排队中 + 已出队待 worker 取走 + 正在处理）。
        """
        cleaned = [date for date in dict.fromkeys(dates) if date]
        with self._clear_lock:
            enqueue_gen = self._current_clear_gen()
            with self._queue_lock:
                added = 0
                for date in cleaned:
                    if self._inflight.get(date) != enqueue_gen:
                        self._inflight[date] = enqueue_gen
                        self._pending.append((date, enqueue_gen))
                        added += 1
                self._outstanding = len(self._inflight)
                outstanding = self._outstanding
                if self._coordinator is None:
                    self._coordinator = threading.Thread(target=self._coordinator_run, daemon=True)
                    self._coordinator.start()
        return {"outstanding": outstanding, "added": added, "workers": self.workers}

    def _coordinator_run(self) -> None:
        # 锁内检查并置空 _coordinator，避免退出临界区与新提交的竞态。
        while True:
            with self._queue_lock:
                if not self._pending:
                    self._coordinator = None
                    return
                batch = list(self._pending)
                self._pending.clear()
            self._run_batch(batch)

    def _run_batch(self, batch: list[tuple[str, tuple[int, int]]]) -> None:
        batch_max_gen = max((gen for _, gen in batch), default=(0, 0))
        self._progress(
            running=True,
            phase="syncing",
            days_total=len(batch),
            days_completed=0,
            active=[],
            day=None,
            message=t('server.engine._run_batch.ae736fc32', p1=len(batch), p2=self.workers),
        )
        results: list[dict] = []
        result_lock = threading.Lock()
        stop = threading.Event()
        auth_errors: list[ProviderAuthError] = []

        def worker() -> None:
            conn = db.connect(self.cfg.db_path)
            try:
                while not stop.is_set() and not self._shutdown.is_set():
                    with result_lock:
                        if not batch:
                            return
                        date, enqueue_gen = batch.pop(0)
                        self._active.add(date)
                        active_snapshot = sorted(self._active)
                    self._progress(active=active_snapshot, day=date)
                    try:
                        snapshot, stored, info = self._sync_day(date, conn, expected_clear_gen=enqueue_gen)
                        with result_lock:
                            results.append(
                                {
                                    "date": date,
                                    "steps": snapshot.activity.steps,
                                    "sleep_minutes": snapshot.sleep.total_minutes if snapshot.sleep else None,
                                    **info,
                                }
                            )
                    except ProviderAuthError as exc:
                        auth_errors.append(exc)
                        stop.set()
                    except ProviderError as exc:
                        with result_lock:
                            results.append({"date": date, "status": "error", "error": str(exc)})
                    except Exception as exc:  # noqa: BLE001 — 单日失败不拖垮整批
                        with result_lock:
                            results.append({"date": date, "status": "error", "error": str(exc)})
                    finally:
                        with result_lock:
                            self._active.discard(date)
                            active_snapshot = sorted(self._active)
                        with self._progress_lock:
                            self.progress["active"] = active_snapshot
                            self.progress["days_completed"] = self.progress.get("days_completed", 0) + 1
                        with self._queue_lock:
                            if self._inflight.get(date) == enqueue_gen:
                                del self._inflight[date]
                            self._outstanding = len(self._inflight)
            finally:
                conn.close()

        threads = [
            threading.Thread(target=worker, daemon=True)
            for _ in range(max(1, min(self.workers, len(batch))))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with self._clear_lock:
            with closing(db.connect(self.cfg.db_path)) as check_conn:
                cleared = self._current_clear_gen(check_conn) != batch_max_gen
            if cleared:
                with self._progress_lock:
                    self.last_result = None
                    self.progress.update(
                        running=False,
                        phase="idle",
                        active=[],
                        day=None,
                        days_total=0,
                        days_completed=0,
                        message=None,
                    )
            elif auth_errors:
                done = {item.get("date") for item in results}
                skipped = [date for date, _ in batch if date not in done]
                with self._progress_lock:
                    self.last_result = {
                        "status": "auth_error",
                        "error": str(auth_errors[0]),
                        "results": results,
                        "skipped": skipped,
                        "finished_at": utc_now_iso(),
                    }
                    self.progress.update(running=False, phase="error", active=[], day=None, message=str(auth_errors[0]))
            else:
                failed = [item for item in results if item.get("status") == "error"]
                partial_days = [item for item in results if item.get("status") == "partial"]
                with self._progress_lock:
                    self.last_result = {
                        "status": "ok" if not failed else "partial",
                        "results": results,
                        "partial_days": len(partial_days),
                        "failed_days": len(failed),
                        "finished_at": utc_now_iso(),
                    }
                    self.progress.update(
                        running=False,
                        phase="done",
                        active=[],
                        day=None,
                        message=t('server.engine._run_batch.d55127406', p1=len(results)),
                    )
        with self._queue_lock:
            for remaining_date, remaining_gen in batch:
                if self._inflight.get(remaining_date) == remaining_gen:
                    del self._inflight[remaining_date]
            self._outstanding = len(self._inflight)

    def status(self) -> dict:
        with self._progress_lock:
            progress = {**self.progress, "active": list(self.progress.get("active") or [])}
            last_result = self.last_result
        with self._queue_lock:
            queued = len(self._pending)
        return {
            "running": self.is_running(),
            "progress": progress,
            "queued": queued,
            "workers": self.workers,
            "last_result": last_result,
        }

    def _fetch_progress(self, completed: int, total: int, key: str | None = None) -> None:
        with self._progress_lock:
            self.progress.update(phase="fetching", completed=completed, total=total, current_key=key)

    def _progress(self, **fields) -> None:
        with self._progress_lock:
            self.progress.update(**fields)

    # ---------- 自动同步 ----------

    def auto_sync_loop(self, stop_event: threading.Event, check_seconds: int = 60) -> None:
        """后台自动同步循环（供 serve 使用；建议只用于常驻进程）。"""
        while not stop_event.wait(check_seconds):
            try:
                with closing(db.connect(self.cfg.db_path)) as conn:
                    self.auto_sync_tick(conn=conn)
            except Exception:  # noqa: BLE001 — 自动同步绝不能让进程崩溃，但必须记录异常供排查
                LOG.exception("Auto-sync tick failed")

    def auto_sync_tick(self, conn: sqlite3.Connection | None = None) -> list[dict] | None:
        target = conn or self.conn
        settings = storage.load_settings(target)
        interval = int(settings.get("sync_interval_minutes") or 0)
        if interval <= 0:
            return None
        name = self.provider_name(target)
        provider = get_provider(name)
        if provider.auth_kind == "oauth":
            credentials = self.credentials_for(name, target)
            if not credentials or credentials.get("needs_reconnect"):
                return None
        # 包含 empty，避免连续无数据的日期退化成每 60 秒轮询一次。
        last = storage.last_completed_sync(target)
        if last is not None:
            age_seconds = (parse_utc(utc_now_iso()) - parse_utc(last)).total_seconds()
            if age_seconds < interval * 60:
                return None
        if not self._lock.acquire(blocking=False):
            return None
        try:
            with self._clear_lock:
                start_gen = self._current_clear_gen(target)
            dates, notice = self._autosync_dates(target)
            results = self._sync_days(dates, conn=target, expected_clear_gen=start_gen)
            if notice:
                with self._clear_lock:
                    if self._current_clear_gen(target) == start_gen:
                        with self._progress_lock:
                            self.last_result = {**(self.last_result or {}), "notice": notice}
            return results
        finally:
            self._lock.release()

    def _autosync_dates(self, conn: sqlite3.Connection | None = None) -> tuple[list[str], str | None]:
        """算出自动同步要补哪些日期（含回补窗口内中间失败后尚未恢复的日期）。"""
        target = conn or self.conn
        today = iso_today()
        window_start = shift_iso(today, -(AUTOSYNC_BACKFILL_LIMIT_DAYS - 1))
        failed_dates = storage.failed_dates_in_window(target, window_start, today)
        anchor = storage.last_synced_date(target) or storage.latest_day_date(target)
        notice = None
        if anchor is None or anchor >= today:
            base_dates = [shift_iso(today, -1), today]
        else:
            gap_days = (parse_iso(today) - parse_iso(anchor)).days
            if gap_days > AUTOSYNC_BACKFILL_LIMIT_DAYS:
                notice = (
                    t('server.engine._autosync_dates.0c6a416ce', gap_days=gap_days, AUTOSYNC_BACKFILL_LIMIT_DAYS=AUTOSYNC_BACKFILL_LIMIT_DAYS, p3=AUTOSYNC_BACKFILL_LIMIT_DAYS)
                )
                gap_days = AUTOSYNC_BACKFILL_LIMIT_DAYS
            base_dates = iso_range(today, gap_days)
        merged = sorted({*base_dates, *failed_dates})
        if len(merged) > AUTOSYNC_BACKFILL_LIMIT_DAYS:
            merged = merged[-AUTOSYNC_BACKFILL_LIMIT_DAYS:]
        return merged, notice
