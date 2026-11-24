"""同步引擎测试：入库、部分失败、授权语义、令牌轮换、后台任务。"""

from __future__ import annotations

import threading
import time

import pytest

from health_by import storage
from health_by.engine import Engine
from health_by.models import DaySnapshot
from health_by.providers import PROVIDERS, ensure_builtin_providers
from health_by.providers.base import Provider, ProviderAuthError, ProviderError
from devtools.gen_demo_data import SyntheticProvider
from health_by.util import iso_today, shift_iso

DAY = "2026-10-15"


@pytest.fixture
def engine(tmp_cfg, conn):
    eng = Engine(tmp_cfg, conn)
    yield eng
    eng.shutdown()
    eng.close()


def test_sync_synthetic_day_stores_everything(engine, conn):
    snapshot = engine.sync_day(DAY)
    assert snapshot.date == DAY
    assert storage.get_day(conn, DAY) is not None
    runs = storage.list_sync_runs(conn)
    assert runs[0]["status"] == "ok"
    assert runs[0]["total"] == 30


def test_sync_days_continues_after_single_failure(engine, conn, monkeypatch):
    class FlakySynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            if date == shift_iso(DAY, -1):
                raise ProviderError("temporary outage", status=503)
            return super().fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", FlakySynthetic())
    results = engine.sync_days([shift_iso(DAY, -1), DAY])
    assert [r["status"] for r in results] == ["error", "ok"]
    assert storage.get_day(conn, DAY) is not None


def test_sync_days_aborts_on_auth_error(engine, monkeypatch):
    class Deauthorized(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raise ProviderAuthError("token expired")

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", Deauthorized())
    with pytest.raises(ProviderAuthError):
        engine.sync_days([DAY])


def test_second_request_queues_instead_of_rejecting(engine, conn, monkeypatch):
    class SlowSynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            time.sleep(0.3)
            return super().fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", SlowSynthetic())

    first = engine.start_background([DAY])
    assert first["outstanding"] == 1
    second = engine.start_background([shift_iso(DAY, -1)])
    # 运行中的请求继续排队，不再 409。`outstanding` 是「在飞总数」：第一个日期还在抓
    # （SlowSynthetic 睡 0.3s），所以此时是 2。这里原本写死 1，但那个值取决于协调线程
    # 是否已经把第一项取走 —— 时序一变就红，属于不稳定的断言。
    assert second["outstanding"] == 2
    assert second["added"] == 1
    # status() 里的 `queued` 仍表示「还在排队」，与 outstanding 不是同一个含义
    assert "queued" in engine.status()

    deadline = time.time() + 15
    while time.time() < deadline:
        if (
            not engine.is_running()
            and storage.get_day(conn, DAY) is not None
            and storage.get_day(conn, shift_iso(DAY, -1)) is not None
        ):
            break
        time.sleep(0.05)
    assert not engine.is_running()
    assert storage.get_day(conn, DAY) is not None
    assert storage.get_day(conn, shift_iso(DAY, -1)) is not None


def test_background_run_completes(engine, conn):
    job = engine.start_background([DAY])
    assert job["outstanding"] == 1
    deadline = time.time() + 10
    while engine.is_running() and time.time() < deadline:
        time.sleep(0.05)
    assert not engine.is_running()
    assert engine.last_result is not None
    assert engine.last_result["status"] == "ok"
    assert storage.get_day(conn, DAY) is not None


# ---------- 令牌刷新 ----------


class FakeOAuthProvider(Provider):
    name = "fake-oauth"
    display_name = "Fake OAuth"
    auth_kind = "oauth"

    def __init__(self):
        self.refresh_calls = 0
        self.fail_refresh = False
        self.seen_tokens: list[str | None] = []

    def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
        from health_by.providers.base import RawDay

        self.seen_tokens.append(((credentials or {}).get("tokens") or {}).get("access_token"))
        return RawDay(date=date, source=self.name, generated_at="now", payload={}, errors=[], stats={"total": 0, "succeeded": 0})

    def translate(self, raw):
        return DaySnapshot(date=raw.date, source=raw.source, generated_at=raw.generated_at)

    def refresh(self, *, credentials, http):
        self.refresh_calls += 1
        if self.fail_refresh:
            raise ProviderAuthError("invalid_grant: refresh token expired")
        return {"access_token": "new-token", "expires_at": time.time() + 3600, "refresh_token": "rotated"}

    def auth_status(self, credentials):
        return {
            "configured": bool((credentials or {}).get("client_id")),
            "connected": bool(((credentials or {}).get("tokens") or {}).get("access_token")),
            "needs_reconnect": bool((credentials or {}).get("needs_reconnect")),
        }


@pytest.fixture
def fake_oauth(engine, conn, monkeypatch):
    ensure_builtin_providers()
    provider = FakeOAuthProvider()
    monkeypatch.setitem(PROVIDERS, "fake-oauth", provider)
    storage.set_setting(conn, "provider", "fake-oauth")
    engine.save_credentials_for(
        "fake-oauth",
        {
            "client_id": "cid",
            "client_secret": "secret",
            "tokens": {"access_token": "old", "refresh_token": "r1", "expires_at": time.time() - 10},
        },
    )
    return provider


def test_expired_token_is_refreshed_and_rotated(fake_oauth, engine, conn):
    engine.sync_day(DAY)
    assert fake_oauth.refresh_calls == 1
    assert fake_oauth.seen_tokens[0] == "new-token"
    stored = engine.credentials_for("fake-oauth")
    assert stored["tokens"]["refresh_token"] == "rotated"
    assert stored["needs_reconnect"] is False


def test_valid_token_not_refreshed(fake_oauth, engine):
    creds = engine.credentials_for("fake-oauth")
    creds["tokens"]["expires_at"] = time.time() + 3600
    engine.save_credentials_for("fake-oauth", creds)
    engine.sync_day(DAY)
    assert fake_oauth.refresh_calls == 0


def test_refresh_failure_marks_needs_reconnect(fake_oauth, engine):
    fake_oauth.fail_refresh = True
    with pytest.raises(ProviderAuthError):
        engine.sync_day(DAY)
    stored = engine.credentials_for("fake-oauth")
    assert stored["needs_reconnect"] is True
    status = engine.auth_status()
    assert status["needs_reconnect"] is True


# ---------- 自动同步 ----------


def test_auto_sync_disabled_when_interval_zero(engine, conn):
    storage.set_setting(conn, "sync_interval_minutes", 0)
    assert engine.auto_sync_tick() is None


def test_auto_sync_runs_when_due(engine, conn):
    storage.set_setting(conn, "sync_interval_minutes", 60)
    results = engine.auto_sync_tick()
    assert results is not None and len(results) == 2
    assert storage.get_day(conn, iso_today()) is not None
    # 刚同步过 → 不再重复
    assert engine.auto_sync_tick() is None


def test_auth_status_for_synthetic(engine):
    status = engine.auth_status()
    assert status["provider"] == "synthetic"
    assert status["auth_kind"] == "none"
    assert status["configured"] is True


def _seed_old_sync(conn, date: str, *, status: str = "ok", when: str = "2000-01-01T00:00:00+00:00"):
    storage.record_sync_run(
        conn,
        provider="synthetic",
        started_at=when,
        finished_at=when,
        date=date,
        total=1,
        succeeded=1 if status == "ok" else 0,
        status=status,
    )


def test_auto_sync_fills_gap_after_downtime(engine, conn):
    """停机几天后重启，要补齐中间空缺，而不是只抓最近两天。"""
    storage.set_setting(conn, "sync_interval_minutes", 60)
    old = shift_iso(iso_today(), -4)
    engine.sync_day(old)
    conn.execute("UPDATE sync_runs SET finished_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.commit()

    results = engine.auto_sync_tick()
    dates = [item["date"] for item in results]
    assert dates == [shift_iso(iso_today(), offset) for offset in (-3, -2, -1, 0)]


def test_auto_sync_backfill_is_capped(engine, conn):
    """缺口过大时不能无上限回填，只补最近一段并给出可见提示。"""
    storage.set_setting(conn, "sync_interval_minutes", 60)
    engine.sync_day(shift_iso(iso_today(), -90))
    conn.execute("UPDATE sync_runs SET finished_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.commit()

    results = engine.auto_sync_tick()
    assert len(results) == 30
    assert results[-1]["date"] == iso_today()
    assert engine.last_result and engine.last_result.get("notice")


def test_empty_day_still_advances_auto_sync_cursor(engine, conn):
    """某天同步成功但没有数据（empty）也算走过，不能因此反复回填。"""
    storage.set_setting(conn, "sync_interval_minutes", 60)
    engine.sync_day(shift_iso(iso_today(), -10))
    _seed_old_sync(conn, shift_iso(iso_today(), -2), status="empty")
    # 所有历史跑动都推远，否则 tick 会认为「刚同步过」而直接返回
    conn.execute("UPDATE sync_runs SET finished_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.commit()

    results = engine.auto_sync_tick()
    assert [item["date"] for item in results] == [shift_iso(iso_today(), -1), iso_today()]


def test_partial_day_is_reported_in_batch_results(engine, conn, monkeypatch):
    """一天里部分上游任务失败时，批次结果必须能看出来，不能一律报 ok。"""
    provider = SyntheticProvider()

    class PartlyBroken(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raw = provider.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)
            raw.errors = [{"key": "sleep", "message": "boom", "status": 500}]
            total = int(raw.stats.get("total") or 1)
            raw.stats = {"total": total, "succeeded": total - 1}
            return raw

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", PartlyBroken())

    results = engine.sync_days([DAY])
    assert results[0]["status"] == "partial"
    assert results[0]["partial"] is True
    assert results[0]["failed_count"] == 1
    assert results[0]["failed_jobs"] == ["sleep"]
    stats = results[0]["stats"]
    assert stats["succeeded"] == stats["total"] - 1
    # 失败细节仍然落在 sync_runs 里，可查「这天哪几个 job 挂了」
    run = storage.list_sync_runs(conn, limit=1)[0]
    assert run["date"] == DAY and run["status"] == "partial"
    assert [item["key"] for item in run["errors"]] == ["sleep"]


@pytest.mark.parametrize("succeeded", [0, 5])
@pytest.mark.parametrize("background", [False, True])
def test_failed_fetch_without_data_is_not_empty_or_an_autosync_anchor(
    engine, conn, monkeypatch, succeeded, background
):
    """请求失败而没有可用数据不能冒充成功空日，前后台状态必须一致。"""
    anchor = shift_iso(iso_today(), -3)
    failed_date = shift_iso(iso_today(), -1)
    engine.sync_day(anchor)
    previous_success = storage.last_successful_sync(conn)

    class Unavailable(SyntheticProvider):
        def fetch_raw(self, date, **kwargs):
            from health_by.providers.base import RawDay

            return RawDay(
                date=date,
                source=self.name,
                generated_at="2026-10-30T00:00:00Z",
                errors=[{"key": f"job-{i}", "message": "timeout"} for i in range(31 - succeeded)],
                stats={"total": 31, "succeeded": succeeded},
            )

        def translate(self, raw):
            return DaySnapshot(raw.date, raw.source, raw.generated_at)

    monkeypatch.setitem(PROVIDERS, "synthetic", Unavailable())
    if background:
        engine.start_background([failed_date])
        deadline = time.monotonic() + 10
        while engine.is_running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not engine.is_running()
        assert engine.last_result["failed_days"] == 1
        result = engine.last_result["results"][0]
    else:
        result = engine.sync_days([failed_date])[0]

    assert result["status"] == "error"
    assert result["error"]
    assert result["stats"] == {"total": 31, "succeeded": succeeded}
    run = storage.list_sync_runs(conn, limit=1)[0]
    assert run["status"] == "error"
    assert run["succeeded"] == succeeded
    assert len(run["errors"]) == 31 - succeeded
    assert storage.get_day(conn, failed_date) is None
    assert storage.last_synced_date(conn) == anchor
    assert storage.last_successful_sync(conn) == previous_success
    assert failed_date in engine._autosync_dates()[0]


def test_same_day_sync_is_serialised_across_threads(engine, conn, monkeypatch):
    """后台 worker 调 _sync_day 时不走引擎级锁，同一天可能被两个线程同时写入。

    这里直接观察同一天的最大并发进入次数：加了按日期锁之后必须是 1。
    """
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}
    base = SyntheticProvider()

    class SlowSynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.15)
            try:
                return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)
            finally:
                with lock:
                    state["now"] -= 1

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", SlowSynthetic())

    threads = [
        threading.Thread(target=engine._sync_day, args=(DAY,))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert state["peak"] == 1
    # 且没有写出重复的指标行
    rows = conn.execute("SELECT COUNT(*) AS n FROM daily_metrics WHERE date = ?", (DAY,)).fetchone()["n"]
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT metric) AS n FROM daily_metrics WHERE date = ?", (DAY,)
    ).fetchone()["n"]
    assert rows == distinct


def test_repeated_queueing_does_not_recurse(engine, conn, monkeypatch):
    """运行期间反复排入新日期时，协调器是用循环续跑而不是递归，不会堆栈溢出。"""
    base = SyntheticProvider()

    class SlowSynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            time.sleep(0.01)
            return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", SlowSynthetic())

    for offset in range(12):
        engine.start_background([shift_iso(DAY, -offset)])
        time.sleep(0.02)

    deadline = time.time() + 60
    while time.time() < deadline and engine.is_running():
        time.sleep(0.05)
    assert not engine.is_running()
    assert storage.count_days(conn) == 12


def test_parse_drift_is_flagged_when_fields_collapse_without_errors(engine, conn, monkeypatch):
    """上游改字段名时 job 仍返回 200，解析结果塌掉但 errors 为空 —— 必须留下痕迹。

    这正是 P0-1 保守合并的副作用：None 被当成「没拿到」保留旧值，
    同步会一直显示绿色，数据却冻结在改坏那天。
    """
    base = SyntheticProvider()
    engine.sync_day(DAY)
    baseline = storage.last_field_count(conn, DAY)
    assert baseline is not None and baseline >= 4
    full = storage.get_day(conn, DAY)

    class RenamedField(SyntheticProvider):
        def translate(self, raw):
            snapshot = base.translate(raw)
            # 模拟上游把「睡眠」相关字段改名：解析不出来，静默变成 None
            snapshot.sleep = None
            snapshot.heart.hrv_ms = None
            snapshot.heart.resting_hr = None
            return snapshot

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", RenamedField())

    results = engine.sync_days([DAY])
    warnings = results[0]["warnings"]
    assert warnings and warnings[0]["kind"] == "parse_drift"
    assert warnings[0]["previous_fields"] == baseline

    # 落库的 sync_runs 也要留下这条，事后可查
    run = storage.list_sync_runs(conn, limit=1)[0]
    assert run["warnings"] and run["warnings"][0]["kind"] == "parse_drift"
    # 且 requests 层没有错误（所以以前完全看不出来）
    assert run["errors"] == []

    # 旧值确实被保留了 —— 这就是为什么必须报警，否则用户看到的是"一切正常"
    assert storage.get_day(conn, DAY)["sleep"] == full["sleep"]


def test_no_drift_warning_when_request_itself_failed(engine, conn, monkeypatch):
    """请求级失败已经记在 errors 里，不该重复报成解析漂移。"""
    base = SyntheticProvider()
    engine.sync_day(DAY)

    class BrokenJob(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raw = base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)
            raw.errors = [{"key": "sleep", "message": "boom", "status": 500}]
            return raw

        def translate(self, raw):
            snapshot = base.translate(raw)
            snapshot.sleep = None
            snapshot.heart.hrv_ms = None
            snapshot.heart.resting_hr = None
            return snapshot

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", BrokenJob())

    results = engine.sync_days([DAY])
    assert results[0]["warnings"] == []
    assert results[0]["failed_jobs"] == ["sleep"]


def test_unparseable_values_are_distinguished_from_absent_data(engine, conn, monkeypatch):
    """「拿到了值但读不出来」必须留下痕迹；「上游本来就没这个字段」不算问题。"""
    from health_by.providers.base import RawDay
    from health_by.providers.google_health.translate import translate_payload

    # 上游真的没数据：字段整块缺失 → 不该产生解析失败
    empty = RawDay(date="2026-05-01", source="google-health", generated_at="2026-05-01T00:00:00+00:00")
    translate_payload(empty)
    assert empty.parse_issues == []

    # 上游给了值但类型变了（步数变成本地化字符串）→ 必须记录
    broken = RawDay(
        date="2026-05-01",
        source="google-health",
        generated_at="2026-05-01T00:00:00+00:00",
        payload={"stepsDaily": {"rollupDataPoints": [
            {"civilStartTime": {"date": {"year": 2026, "month": 5, "day": 1}},
             "steps": {"countSum": "8,123"}}
        ]}},
    )
    translate_payload(broken)
    assert broken.parse_issues, "带千分位的字符串应当被记为解析失败"
    assert broken.parse_issues[0]["helper"] == "_float"
    assert "8,123" in broken.parse_issues[0]["value"]


def test_date_locks_do_not_grow_without_bound(engine, conn):
    """日期锁必须在没人用的时候回收，否则长期运行会按日期数无限增长。"""
    for offset in range(6):
        engine.sync_day(shift_iso(DAY, -offset))
    assert engine._date_locks == {}


def test_date_locks_still_exclusive_while_cleanup_happens(engine, monkeypatch):
    """回收不能破坏互斥：并发同步同一天时，峰值并发仍必须是 1。"""
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}
    base = SyntheticProvider()

    class SlowSynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            with lock:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.1)
            try:
                return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)
            finally:
                with lock:
                    state["now"] -= 1

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", SlowSynthetic())

    threads = [threading.Thread(target=engine._sync_day, args=(DAY,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert state["peak"] == 1
    assert engine._date_locks == {}


def test_drift_warning_fires_once_not_on_every_resync(engine, conn, monkeypatch):
    """上游永久去掉字段后，漂移警告只报第一次。

    基线取「上次实际解析出的字段数」而不是 daily_metrics 的累积行数——
    累积行数只增不减，会让警告永远重复，而且「上次是 N 项」这句话会变成事实错误。
    """
    base = SyntheticProvider()
    engine.sync_day(DAY)

    class FieldsGone(SyntheticProvider):
        def translate(self, raw):
            snapshot = base.translate(raw)
            snapshot.sleep = None
            snapshot.heart.hrv_ms = None
            snapshot.heart.resting_hr = None
            return snapshot

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", FieldsGone())

    warned = []
    for _ in range(3):
        results = engine.sync_days([DAY])
        warned.append(bool(results[0]["warnings"]))

    assert warned == [True, False, False], f"应当只报第一次，实际 {warned}"

    # 第一次的文案里，「上次」必须是真的上次字段数，而不是陈旧累积值
    runs = storage.list_sync_runs(conn, limit=10)
    drift = [run for run in runs if run["warnings"]]
    assert len(drift) == 1
    message = drift[0]["warnings"][0]["message"]
    previous = drift[0]["warnings"][0]["previous_fields"]
    current = drift[0]["warnings"][0]["current_fields"]
    assert f"上次是 {previous} 项" in message
    assert previous > current
    # 每次同步都记录了本次字段数，供下一轮当基线
    assert all(run["fields"] is not None for run in runs)


def test_drift_detection_skips_rows_without_baseline(conn, tmp_cfg):
    """v3 之前写入的旧记录没有 fields 列值，不能拿它当基线报假警告。"""
    from health_by.engine import Engine

    engine = Engine(tmp_cfg, conn)
    try:
        engine.sync_day(DAY)
        # 抹掉字段数，模拟老库升级上来的记录
        conn.execute("UPDATE sync_runs SET fields = NULL")
        conn.commit()
        engine.sync_day(DAY)  # 不该因为"没有基线"而报漂移
        run = storage.list_sync_runs(conn, limit=1)[0]
        assert run["warnings"] == []
    finally:
        engine.shutdown()
        engine.close()


def test_parse_issues_are_recorded_as_warning_list_not_string(engine, conn, monkeypatch):
    base = SyntheticProvider()

    class WithParseIssue(SyntheticProvider):
        def translate(self, raw):
            snapshot = base.translate(raw)
            raw.parse_issues.append({"key": "stepsDaily", "helper": "_float", "value": "8,123"})
            return snapshot

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", WithParseIssue())

    results = engine.sync_days([DAY])
    warnings = results[0]["warnings"]
    assert isinstance(warnings, list) and len(warnings) == 1
    assert warnings[0]["kind"] == "parse_issue"

    run = storage.list_sync_runs(conn, limit=1)[0]
    assert isinstance(run["warnings"], list) and len(run["warnings"]) == 1
    assert run["warnings"][0]["kind"] == "parse_issue"


def test_clear_all_prevents_inflight_worker_writeback(engine, conn, monkeypatch):
    """在途后台同步抓数期间触发 clear_all 后，返回的结果不得写回数据库。"""
    base = SyntheticProvider()
    entered = threading.Event()
    release = threading.Event()

    class BlockingSynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            entered.set()
            release.wait(timeout=10)
            return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", BlockingSynthetic())

    engine.start_background([DAY])
    assert entered.wait(timeout=5)
    engine.clear_all(conn)
    release.set()

    deadline = time.monotonic() + 10
    while engine.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not engine.is_running()
    assert storage.count_days(conn) == 0
    assert storage.list_sync_runs(conn) == []


def test_intraday_hr_only_day_is_stored_not_dropped_as_empty(engine, conn, monkeypatch):
    """只有分时心率/极值的日期必须落库，不能误判为 empty 丢弃。"""
    from health_by.models import ActivityDay, BodyDay, HealthAlerts, HeartDay, TimePoint

    class IntradayOnly(SyntheticProvider):
        def translate(self, raw):
            return DaySnapshot(
                date=raw.date,
                source=raw.source,
                generated_at=raw.generated_at,
                activity=ActivityDay(),
                heart=HeartDay(hr_min=65.0, hr_max=82.0, hr_avg=71.0, hr_intraday=[TimePoint("09:00", 71.0)]),
                body=BodyDay(),
                alerts=HealthAlerts(),
            )

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", IntradayOnly())

    results = engine.sync_days([DAY])
    assert results[0]["status"] == "ok"
    saved = storage.get_day(conn, DAY)
    assert saved is not None
    assert saved["heart"]["hr_avg"] == 71.0
    assert len(saved["heart"]["hr_intraday"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM raw_days WHERE date = ?", (DAY,)).fetchone()[0] == 1


def test_single_worker_queue_dedupes_already_queued_date(engine, conn, monkeypatch):
    """单工作线程模式下，正在队列中等待的日期 B 不得被重复排队。"""
    base = SyntheticProvider()
    day_a = shift_iso(DAY, -1)
    day_b = DAY
    entered_a = threading.Event()
    release_a = threading.Event()
    fetched: list[str] = []

    class ControlledSynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            fetched.append(date)
            if date == day_a:
                entered_a.set()
                release_a.wait(timeout=10)
            return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", ControlledSynthetic())

    engine.start_background([day_a])
    assert entered_a.wait(timeout=5)
    first = engine.start_background([day_b])
    second = engine.start_background([day_b])
    assert first["added"] == 1
    assert second["added"] == 0
    release_a.set()

    deadline = time.monotonic() + 10
    while engine.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fetched == [day_a, day_b]


def test_autosync_retries_failed_middle_day_and_honors_empty_interval(engine, conn):
    """中间某天失败即使新一天成功也应重试；连续 empty 日必须受同步间隔节流。"""
    storage.set_setting(conn, "sync_interval_minutes", 60)
    today = iso_today()
    d_old = shift_iso(today, -3)
    d_failed = shift_iso(today, -2)
    d_newer = shift_iso(today, -1)

    engine.sync_day(d_old)
    _seed_old_sync(conn, d_failed, status="error", when="2020-01-01T00:00:00+00:00")
    engine.sync_day(d_newer)
    conn.execute("UPDATE sync_runs SET finished_at = ?", ("2000-01-01T00:00:00+00:00",))
    conn.commit()

    dates, _ = engine._autosync_dates()
    assert d_failed in dates

    # 若刚刚完成过一次 empty 同步，auto_sync_tick 必须尊重 sync_interval_minutes 不空转
    from health_by.util import utc_now_iso

    _seed_old_sync(conn, today, status="empty", when=utc_now_iso())
    assert engine.auto_sync_tick() is None


def test_clear_all_cancels_queued_batch_dates_and_clears_last_result(engine, conn, monkeypatch):
    """3 天批次在 2 个 worker 均进入抓数后清空：已开始 2 天与待领取的第 3 天都不得落库，last_result 亦清空。"""
    base = SyntheticProvider()
    d1, d2, d3 = shift_iso(DAY, -2), shift_iso(DAY, -1), DAY
    started_lock = threading.Lock()
    started: list[str] = []
    two_running = threading.Event()
    release = threading.Event()

    class BlockingThree(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            with started_lock:
                started.append(date)
                if len(started) >= 2:
                    two_running.set()
            if date in (d1, d2):
                release.wait(timeout=10)
            return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", BlockingThree())
    engine.workers = 2

    engine.start_background([d1, d2, d3])
    assert two_running.wait(timeout=5)
    engine.clear_all(conn)
    release.set()

    deadline = time.monotonic() + 10
    while engine.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not engine.is_running()
    assert storage.count_days(conn) == 0
    assert storage.list_sync_runs(conn) == []
    assert d3 not in started, "清空前排队的第 3 天不应在清空后被启动抓数"
    assert engine.status()["last_result"] is None

    # 清空后立即在旧批次未退出前重新提交同一个日期 d3：新代际的 d3 必须正常入库，旧代际的 d1/d2 仍被抛弃
    started.clear()
    two_running.clear()
    release.clear()
    monkeypatch.setitem(PROVIDERS, "synthetic", BlockingThree())
    engine.start_background([d1, d2, d3])
    assert two_running.wait(timeout=5)
    engine.clear_all(conn)
    resubmit = engine.start_background([d3])
    assert resubmit["added"] == 1
    release.set()

    deadline = time.monotonic() + 10
    while engine.is_running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert storage.available_dates(conn) == [d3]
    assert engine.status()["last_result"] is not None

    # 正常同步完成后再清空：status().last_result 也必须置空，不残留旧日步数与睡眠
    engine.clear_all(conn)
    assert storage.count_days(conn) == 0
    assert engine.status()["last_result"] is None


def test_negative_timezone_ecg_archived_on_local_day_only_not_empty_future_days(tmp_cfg, conn, monkeypatch):
    """UTC-08:00 21:30（UTC 次日 05:30）的 ECG 应落到测量当天；请求只发 `>=` 符合 Google 契约，且不误建未来空日。"""
    import json
    import httpx
    from health_by.engine import Engine
    from health_by.providers.google_health.provider import GoogleHealthProvider

    ecg_points = [
        {
            "electrocardiogram": {
                "interval": {"startTime": "2026-03-02T05:30:00Z", "startUtcOffset": "-28800s"},  # 2026-03-01 21:30
                "classification": "SINUS_RHYTHM",
                "averageHeartRateBpm": 68,
            }
        },
        {
            "electrocardiogram": {
                "interval": {"startTime": "2026-03-10T05:30:00Z", "startUtcOffset": "-28800s"},  # 2026-03-09 21:30（未来记录）
                "classification": "ATRIAL_FIBRILLATION",
                "averageHeartRateBpm": 110,
            }
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2/v3/userinfo" in url:
            return httpx.Response(200, json={"name": "T"})
        if "dailyRollUp" in url:
            # 仅给 2026-03-05 返回步数，证明有真实数据时仍保留 90 天内 ECG 历史
            body = json.loads(request.content.decode())
            end_d = body["range"]["end"]["date"]
            end_iso = f"{end_d['year']:04d}-{end_d['month']:02d}-{end_d['day']:02d}"
            if end_iso == "2026-03-05" and "/dataTypes/steps/" in url:
                return httpx.Response(
                    200,
                    json={
                        "rollupDataPoints": [
                            {
                                "civilStartTime": {"date": {"year": 2026, "month": 3, "day": 5}},
                                "steps": {"countSum": 5000},
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"rollupDataPoints": []})
        if "pairedDevices" in url:
            return httpx.Response(200, json={"pairedDevices": []})
        if "/dataTypes/electrocardiogram/" in url:
            filt = request.url.params.get("filter", "")
            # 严格遵循 Google 官方契约：ECG start_time 仅允许 >= 下界，出现 < 或 AND 视为 400 非法过滤
            if "<" in filt or " AND " in filt or ">=" not in filt:
                return httpx.Response(400, json={"error": {"message": "unsupported filter operator for electrocardiogram"}})
            lo = filt.split('>= "')[1].split('"')[0]
            pts = [p for p in ecg_points if p["electrocardiogram"]["interval"]["startTime"] >= lo]
            return httpx.Response(200, json={"dataPoints": pts})
        return httpx.Response(200, json={"dataPoints": []})

    storage.set_setting(conn, "provider", "google-health")
    gh_engine = Engine(tmp_cfg, conn)
    gh_engine.save_credentials_for(
        "google-health",
        {"tokens": {"access_token": "tok", "expires_at": time.time() + 3600}},
        conn,
    )
    gh_engine._http = httpx.Client(transport=httpx.MockTransport(handler))
    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "google-health", GoogleHealthProvider(pace_ms=0))
    try:
        # 1. 测量当天（2026-03-01）必须命中并入库，且不得混入未来的 2026-03-09 记录
        r_day = gh_engine.sync_days(["2026-03-01"])[0]
        assert r_day["status"] == "ok"
        saved_mar1 = storage.get_day(conn, "2026-03-01")
        assert saved_mar1 is not None
        assert len(saved_mar1["alerts"]["ecg_readings"]) == 1
        assert saved_mar1["alerts"]["ecg_readings"][0]["start_time"] == "2026-03-01T21:30:00-08:00"

        # 2. 后续空白日（3 月 2、3、8 日）不得因 90 天窗口里带出旧 ECG 或未来 ECG 而误判 ok 建档
        empty_results = gh_engine.sync_days(["2026-03-02", "2026-03-03", "2026-03-08"])
        assert [item["status"] for item in empty_results] == ["empty", "empty", "empty"]
        assert storage.count_days(conn) == 1

        # 3. 有真实步数的 3 月 5 日正常建档，并附带 90 天内的 3 月 1 日 ECG 历史（仍过滤掉 3 月 9 日未来记录）
        r_mar5 = gh_engine.sync_days(["2026-03-05"])[0]
        assert r_mar5["status"] == "ok"
        saved_mar5 = storage.get_day(conn, "2026-03-05")
        assert saved_mar5["activity"]["steps"] == 5000
        assert len(saved_mar5["alerts"]["ecg_readings"]) == 1
        assert saved_mar5["alerts"]["ecg_readings"][0]["start_time"] == "2026-03-01T21:30:00-08:00"
    finally:
        gh_engine.shutdown()
        gh_engine.close()


def test_auto_sync_tick_cancelled_when_cleared_after_scheduling_dates(engine, conn, monkeypatch):
    """auto_sync_tick 排好回补日期后、执行抓数前若触发 clear_all，不得发起抓数或写入数据/notice。"""
    storage.set_setting(conn, "sync_interval_minutes", 60)
    today = iso_today()
    scheduled = [shift_iso(today, -i) for i in range(4, -1, -1)]
    fetched: list[str] = []

    class SpySynthetic(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            fetched.append(date)
            return super().fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", SpySynthetic())

    def fake_autosync_dates(target=None):
        engine.clear_all(target or conn)
        return scheduled, "backfill capped"

    monkeypatch.setattr(engine, "_autosync_dates", fake_autosync_dates)
    results = engine.auto_sync_tick(conn=conn)
    assert all(item["status"] == "cancelled" for item in results)
    assert fetched == []
    assert storage.count_days(conn) == 0
    assert storage.list_sync_runs(conn) == []
    assert engine.status()["last_result"] is None


def test_cross_process_clear_blocks_second_engine_writeback(tmp_cfg, conn, monkeypatch):
    """独立连接（如 CLI `health-by sync`）同步途中，另一连接（如 Web `clear_all`）清空后不得写回或继续抓下一天。"""
    from health_by import db as db_module
    from health_by.engine import Engine

    base = SyntheticProvider()
    d1, d2 = shift_iso(DAY, -1), DAY
    fetched: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    class BlockingCLI(SyntheticProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            fetched.append(date)
            if date == d1:
                entered.set()
                release.wait(timeout=10)
            return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "synthetic", BlockingCLI())

    engine_cli = Engine(tmp_cfg, conn)
    conn_web = db_module.connect(tmp_cfg.db_path)
    engine_web = Engine(tmp_cfg, conn_web)
    outcome: dict[str, list[dict]] = {}
    worker_err: list[BaseException] = []

    def run_cli_sync():
        try:
            outcome["results"] = engine_cli.sync_days([d1, d2])
        except BaseException as exc:  # noqa: BLE001
            worker_err.append(exc)

    thread = threading.Thread(target=run_cli_sync, daemon=True)
    try:
        thread.start()
        assert entered.wait(timeout=5)
        engine_web.clear_all(conn_web)
        release.set()
        thread.join(timeout=10)
        assert not worker_err
        assert [item["status"] for item in outcome["results"]] == ["cancelled", "cancelled"]
        assert fetched == [d1]
        assert storage.count_days(conn) == 0
        assert storage.list_sync_runs(conn) == []
    finally:
        engine_cli.shutdown()
        engine_cli.close()
        engine_web.shutdown()
        engine_web.close()
        conn_web.close()


def test_cross_process_clear_atomic_with_store_day_and_sync_run(tmp_cfg, conn, monkeypatch):
    """最终代际校验、store_day 与 record_sync_run 同在一个 BEGIN IMMEDIATE 事务内，消除跨进程微窗口。"""
    from health_by import db as db_module

    ensure_builtin_providers()
    engine_cli = Engine(tmp_cfg, conn)
    conn_web = db_module.connect(tmp_cfg.db_path)
    engine_web = Engine(tmp_cfg, conn_web)

    real_store_day = storage.store_day
    real_record_sync_run = storage.record_sync_run

    def _assert_all_empty():
        for table in ("days", "raw_days", "daily_metrics", "sync_runs"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0

    try:
        # 1. 在 get_clear_generation 校验之后、store_day 执行前暂停：跨进程 clear_all 必须等待事务提交，不能插队留下孤立日数据
        entered_store = threading.Event()
        release_store = threading.Event()
        errors_1: list[BaseException] = []

        def paused_store_day(*args, **kwargs):
            entered_store.set()
            assert release_store.wait(timeout=10)
            return real_store_day(*args, **kwargs)

        monkeypatch.setattr(storage, "store_day", paused_store_day)
        t_cli_1 = threading.Thread(target=lambda: engine_cli.sync_days([DAY]), daemon=True)

        def run_clear_1():
            try:
                engine_web.clear_all(conn_web)
            except BaseException as exc:  # noqa: BLE001
                errors_1.append(exc)

        t_web_1 = threading.Thread(target=run_clear_1, daemon=True)
        t_cli_1.start()
        assert entered_store.wait(timeout=5)
        t_web_1.start()
        time.sleep(0.05)
        assert t_web_1.is_alive(), "clear_all 应被 BEGIN IMMEDIATE 写锁阻塞直到同步写回事务完成"
        release_store.set()
        t_cli_1.join(timeout=10)
        t_web_1.join(timeout=10)
        assert not errors_1
        assert not t_cli_1.is_alive() and not t_web_1.is_alive()
        _assert_all_empty()

        # 2. 在 store_day(..., commit=False) 之后、record_sync_run 前暂停：外部连接尚不可见，clear_all 不得插队并留下游离 sync_runs 行
        monkeypatch.setattr(storage, "store_day", real_store_day)
        entered_run = threading.Event()
        release_run = threading.Event()
        errors_2: list[BaseException] = []

        def paused_record_sync_run(*args, **kwargs):
            entered_run.set()
            assert release_run.wait(timeout=10)
            return real_record_sync_run(*args, **kwargs)

        monkeypatch.setattr(storage, "record_sync_run", paused_record_sync_run)
        t_cli_2 = threading.Thread(target=lambda: engine_cli.sync_days([DAY]), daemon=True)

        def run_clear_2():
            try:
                engine_web.clear_all(conn_web)
            except BaseException as exc:  # noqa: BLE001
                errors_2.append(exc)

        t_web_2 = threading.Thread(target=run_clear_2, daemon=True)
        t_cli_2.start()
        assert entered_run.wait(timeout=5)
        assert storage.count_days(conn_web) == 0, "record_sync_run 之前事务尚未提交，外部连接不应读到半截落库结果"
        t_web_2.start()
        time.sleep(0.05)
        assert t_web_2.is_alive(), "clear_all 应等待 store_day + record_sync_run 事务一并提交"
        release_run.set()
        t_cli_2.join(timeout=10)
        t_web_2.join(timeout=10)
        assert not errors_2
        assert not t_cli_2.is_alive() and not t_web_2.is_alive()
        _assert_all_empty()

        # 3. 写回失败回滚原子性：record_sync_run 抛错时 store_day 一并回滚
        def boom_record_sync_run(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(storage, "record_sync_run", boom_record_sync_run)
        with pytest.raises(RuntimeError, match="boom"):
            engine_cli.sync_day(DAY)
        _assert_all_empty()
        assert conn.execute("SELECT COUNT(*) FROM raw_measure_dates").fetchone()[0] == 0
    finally:
        engine_cli.shutdown()
        engine_cli.close()
        engine_web.shutdown()
        engine_web.close()
        conn_web.close()
