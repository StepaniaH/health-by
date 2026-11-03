"""同步引擎：单飞锁、令牌保鲜与轮换、错误语义、后台任务与进度。"""

from __future__ import annotations

from health_by.i18n import t

import sqlite3
import threading
import time

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


class Engine:
    def __init__(self, cfg: Config, conn):
        self.cfg = cfg
        self.conn = conn
        self.secrets = SecretStore(cfg.secret_key_path)
        self._lock = threading.RLock()
        self._queue_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._pending: list[str] = []
        self._active: set[str] = set()
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
            self._pending.clear()

    def provider_name(self, conn: sqlite3.Connection | None = None) -> str:
        return str(storage.get_setting(conn or self.conn, "provider", "google-health"))

    # ---------- 凭据 ----------

    def credentials_for(self, name: str, conn: sqlite3.Connection | None = None) -> dict | None:
        raw = storage.load_credentials_raw(conn or self.conn, name)
        if raw is None:
            return None
        try:
            return self.secrets.decrypt_json(raw)
        except SecretError:
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

    def _sync_day(self, date: str, conn=None) -> tuple[DaySnapshot, bool, dict]:
        # 同一天同一时刻只允许一个线程在跑（前台手动同步 vs 后台 worker）。
        lock = self._acquire_date_lock(date)
        try:
            with lock:
                return self._sync_day_locked(date, conn)
        finally:
            self._release_date_lock(date)

    def _sync_day_locked(self, date: str, conn=None) -> tuple[DaySnapshot, bool, dict]:
        target = conn or self.conn
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
        # 基线取「该日期上一次同步实际解析出多少字段」，而不是 daily_metrics 的
        # 累积行数（那个只增不减，会让同一次漂移被反复报告且文案失真）。
        # 没有历史字段数（v3 之前写入的老库）时**直接跳过检测**：那时唯一的
        # 替代基线就是那个累积行数，宁可这一轮不判，也不要拿错数据报假警告。
        # 下一次同步就会记下 fields，检测随即恢复。
        baseline = storage.last_field_count(target, date)
        # 无任何可展示数据的日期不落库，避免回填时污染日期选择与趋势
        stored = bool(metrics or snapshot.sleep or snapshot.workouts)
        if stored:
            storage.store_day(target, snapshot, raw)
        warnings = self._drift_warnings(date, len(metrics), baseline, raw)
        # 「有值但解析失败」与「上游没数据」是两件事：前者记 warning，后者不记。
        if raw.parse_issues:
            warnings = t('server.engine._sync_day_locked.8d13781d4', p1=warnings, p2=[
                {
                    "kind": "parse_issue",
                    "message": (
                        t('server.engine._sync_day_locked.1eff58b7e', date=date, p2=len(raw.parse_issues))
                    ),
                    "date": date,
                    "sample": raw.parse_issues[:MAX_REPORTED_FAILED_JOBS],
                }
            ])
        total = int(raw.stats.get("total") or 0)
        succeeded_value = raw.stats.get("succeeded")
        succeeded = total if succeeded_value is None else int(succeeded_value)
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
        )
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

    def _sync_days(self, dates: list[str]) -> list[dict]:
        results: list[dict] = []
        for index, date in enumerate(dates):
            self._progress(phase="syncing", day=date, days_completed=index, days_total=len(dates))
            try:
                snapshot, stored, info = self._sync_day(date)
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
        coordinator = self._coordinator
        return self._outstanding > 0 or bool(self._pending) or bool(coordinator is not None and coordinator.is_alive())

    def start_background(self, dates: list[str]) -> dict:
        """把日期加入同步队列；已有任务在跑时继续排队，不再拒绝。

        返回的 `queued` 是**当前在飞的总数**（排队中 + 正在处理），而不是仅排队数：
        协调线程什么时候取走待处理项是非确定的，用纯 pending 长度会让调用方
        （和测试）看到时而 1 时而 0 的数字。
        """
        cleaned = [date for date in dict.fromkeys(dates) if date]
        with self._queue_lock:
            added = 0
            for date in cleaned:
                if date not in self._pending and date not in self._active:
                    self._pending.append(date)
                    added += 1
            self._outstanding += added
            outstanding = self._outstanding
            if self._coordinator is None or not self._coordinator.is_alive():
                self._coordinator = threading.Thread(target=self._coordinator_run, daemon=True)
                self._coordinator.start()
        # 键名刻意不叫 `queued`：那个词在 status() 里指"还在排队"，这里指"在飞总数"。
        # 同名不同义会让调用方（和前端）莫名跳变。
        return {"outstanding": outstanding, "added": added, "workers": self.workers}

    def _coordinator_run(self) -> None:
        # 用循环处理「跑完一批又来了新日期」，不要递归调用自己：
        # 连续排队很多轮会把调用栈越堆越深，且异常时的栈难读。
        while True:
            with self._queue_lock:
                batch = list(self._pending)
                self._pending.clear()
            if not batch:
                return
            self._run_batch(batch)

    def _run_batch(self, batch: list[str]) -> None:
        self.progress.update(
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
                        date = batch.pop(0)
                        self._active.add(date)
                        self.progress["active"] = sorted(self._active)
                        self.progress["day"] = date
                    try:
                        snapshot, stored, info = self._sync_day(date, conn)
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
                            self.progress["active"] = sorted(self._active)
                            self.progress["days_completed"] = self.progress.get("days_completed", 0) + 1
                        with self._queue_lock:
                            self._outstanding = max(0, self._outstanding - 1)
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

        if auth_errors:
            # 授权失效会中止整批，被放弃的日期必须留下痕迹：
            # 否则用户只看到一条错误，不知道哪些天没同步。
            done = {item.get("date") for item in results}
            skipped = [date for date in batch if date not in done]
            self.last_result = {
                "status": "auth_error",
                "error": str(auth_errors[0]),
                "results": results,
                "skipped": skipped,
                "finished_at": utc_now_iso(),
            }
            self.progress.update(phase="error", message=str(auth_errors[0]))
        else:
            failed = [item for item in results if item.get("status") == "error"]
            partial_days = [item for item in results if item.get("status") == "partial"]
            self.last_result = {
                "status": "ok" if not failed else "partial",
                "results": results,
                "partial_days": len(partial_days),
                "failed_days": len(failed),
                "finished_at": utc_now_iso(),
            }
            self.progress.update(phase="done", message=t('server.engine._run_batch.d55127406', p1=len(results)))
        self.progress.update(running=False, active=[], day=None)
        with self._queue_lock:
            # 授权错误会提前停止整批，被放弃的日期不会再走 finally 里的减计数，
            # 所以这里按「仍在排队的日期数」重新对齐，避免 is_running 永远为真。
            self._outstanding = len(self._pending)

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "progress": dict(self.progress),
            "queued": len(self._pending),
            "workers": self.workers,
            "last_result": self.last_result,
        }

    def _fetch_progress(self, completed: int, total: int, key: str | None = None) -> None:
        self.progress.update(phase="fetching", completed=completed, total=total, current_key=key)

    def _progress(self, **fields) -> None:
        self.progress.update(**fields)

    # ---------- 自动同步 ----------

    def auto_sync_loop(self, stop_event: threading.Event, check_seconds: int = 60) -> None:
        """后台自动同步循环（供 serve 使用；建议只用于常驻进程）。"""
        while not stop_event.wait(check_seconds):
            try:
                self.auto_sync_tick()
            except Exception:  # noqa: BLE001 — 自动同步绝不能让进程崩溃
                pass

    def auto_sync_tick(self) -> list[dict] | None:
        settings = storage.load_settings(self.conn)
        interval = int(settings.get("sync_interval_minutes") or 0)
        if interval <= 0:
            return None
        name = str(settings.get("provider") or "google-health")
        provider = get_provider(name)
        if provider.auth_kind == "oauth":
            credentials = self.credentials_for(name)
            if not credentials or credentials.get("needs_reconnect"):
                return None
        last = storage.last_successful_sync(self.conn)
        if last is not None:
            age_seconds = (parse_utc(utc_now_iso()) - parse_utc(last)).total_seconds()
            if age_seconds < interval * 60:
                return None
        if not self._lock.acquire(blocking=False):
            return None
        try:
            dates, notice = self._autosync_dates()
            results = self._sync_days(dates)
            if notice:
                self.last_result = {**(self.last_result or {}), "notice": notice}
            return results
        finally:
            self._lock.release()

    def _autosync_dates(self) -> tuple[list[str], str | None]:
        """算出自动同步要补哪些日期。

        只抓 [昨天, 今天] 会有洞：服务停几天再起来，中间那几天永远不会被补，
        而 `last_successful_sync` 会立刻更新，于是「数据缺口」被悄悄跳过。
        所以从「已覆盖到哪天」往后补齐，但设上限，避免首次部署或长期停机后
        一次抓住几十天。返回 (日期列表, 提示文案或 None)。
        """
        today = iso_today()
        anchor = storage.last_synced_date(self.conn) or storage.latest_day_date(self.conn)
        if anchor is None or anchor >= today:
            # 没同步过，或已经覆盖到今天：维持原有行为（昨天 + 今天，今天要重抓以拿增量）。
            #
            # 注意一个看似回归、实则收敛的行为：锚点=昨天时，下面按 gap 算只返回
            # [今天]，比原来的固定 [昨天, 今天] 少抓一天。**不要"修"成 gap+1**：
            # 下一轮 tick 锚点变成今天，又会命中本分支返回 [昨天, 今天]，昨天随即补齐。
            # 也就是说这是"延迟一个 tick"（≤ 同步间隔），不是漏抓；改成 gap+1 反而
            # 会把锚点那天重复抓一遍。
            return [shift_iso(today, -1), today], None
        gap_days = (parse_iso(today) - parse_iso(anchor)).days
        notice = None
        if gap_days > AUTOSYNC_BACKFILL_LIMIT_DAYS:
            notice = (
                t('server.engine._autosync_dates.0c6a416ce', gap_days=gap_days, AUTOSYNC_BACKFILL_LIMIT_DAYS=AUTOSYNC_BACKFILL_LIMIT_DAYS, p3=AUTOSYNC_BACKFILL_LIMIT_DAYS)
            )
            gap_days = AUTOSYNC_BACKFILL_LIMIT_DAYS
        return iso_range(today, gap_days), notice
