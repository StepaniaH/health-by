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
from devtools.gen_demo_data import DemoProvider
from health_by.util import iso_today, shift_iso

DAY = "2026-10-15"


@pytest.fixture
def engine(tmp_cfg, conn):
    eng = Engine(tmp_cfg, conn)
    yield eng
    eng.shutdown()
    eng.close()


def test_sync_demo_day_stores_everything(engine, conn):
    snapshot = engine.sync_day(DAY)
    assert snapshot.date == DAY
    assert storage.get_day(conn, DAY) is not None
    runs = storage.list_sync_runs(conn)
    assert runs[0]["status"] == "ok"
    assert runs[0]["total"] == 31


def test_sync_days_continues_after_single_failure(engine, conn, monkeypatch):
    class FlakyDemo(DemoProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            if date == shift_iso(DAY, -1):
                raise ProviderError("temporary outage", status=503)
            return super().fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", FlakyDemo())
    results = engine.sync_days([shift_iso(DAY, -1), DAY])
    assert [r["status"] for r in results] == ["error", "ok"]
    assert storage.get_day(conn, DAY) is not None


def test_sync_days_aborts_on_auth_error(engine, monkeypatch):
    class Deauthorized(DemoProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raise ProviderAuthError("token expired")

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", Deauthorized())
    with pytest.raises(ProviderAuthError):
        engine.sync_days([DAY])


def test_second_request_queues_instead_of_rejecting(engine, conn, monkeypatch):
    class SlowDemo(DemoProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            time.sleep(0.3)
            return super().fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", SlowDemo())

    first = engine.start_background([DAY])
    assert first["outstanding"] == 1
    second = engine.start_background([shift_iso(DAY, -1)])
    # 运行中的请求继续排队，不再 409。`outstanding` 是「在飞总数」：第一个日期还在抓
    # （SlowDemo 睡 0.3s），所以此时是 2。这里原本写死 1，但那个值取决于协调线程
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


def test_auth_status_for_demo(engine):
    status = engine.auth_status()
    assert status["provider"] == "demo"
    assert status["auth_kind"] == "none"
    assert status["configured"] is True


def _seed_old_sync(conn, date: str, *, status: str = "ok", when: str = "2000-01-01T00:00:00+00:00"):
    storage.record_sync_run(
        conn,
        provider="demo",
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
    provider = DemoProvider()

    class PartlyBroken(DemoProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            raw = provider.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)
            raw.errors = [{"key": "sleep", "message": "boom", "status": 500}]
            total = int(raw.stats.get("total") or 1)
            raw.stats = {"total": total, "succeeded": total - 1}
            return raw

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", PartlyBroken())

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

    class Unavailable(DemoProvider):
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

    monkeypatch.setitem(PROVIDERS, "demo", Unavailable())
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
    base = DemoProvider()

    class SlowDemo(DemoProvider):
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
    monkeypatch.setitem(PROVIDERS, "demo", SlowDemo())

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
    base = DemoProvider()

    class SlowDemo(DemoProvider):
        def fetch_raw(self, date, *, credentials=None, http=None, on_progress=None):
            time.sleep(0.01)
            return base.fetch_raw(date, credentials=credentials, http=http, on_progress=on_progress)

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", SlowDemo())

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
    base = DemoProvider()
    engine.sync_day(DAY)
    baseline = storage.last_field_count(conn, DAY)
    assert baseline is not None and baseline >= 4
    full = storage.get_day(conn, DAY)

    class RenamedField(DemoProvider):
        def translate(self, raw):
            snapshot = base.translate(raw)
            # 模拟上游把「睡眠」相关字段改名：解析不出来，静默变成 None
            snapshot.sleep = None
            snapshot.heart.hrv_ms = None
            snapshot.heart.resting_hr = None
            return snapshot

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", RenamedField())

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
    base = DemoProvider()
    engine.sync_day(DAY)

    class BrokenJob(DemoProvider):
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
    monkeypatch.setitem(PROVIDERS, "demo", BrokenJob())

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
    base = DemoProvider()

    class SlowDemo(DemoProvider):
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
    monkeypatch.setitem(PROVIDERS, "demo", SlowDemo())

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
    base = DemoProvider()
    engine.sync_day(DAY)

    class FieldsGone(DemoProvider):
        def translate(self, raw):
            snapshot = base.translate(raw)
            snapshot.sleep = None
            snapshot.heart.hrv_ms = None
            snapshot.heart.resting_hr = None
            return snapshot

    ensure_builtin_providers()
    monkeypatch.setitem(PROVIDERS, "demo", FieldsGone())

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
