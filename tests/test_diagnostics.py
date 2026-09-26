"""Diagnostics tests: smoke test report + UI ConnectionProbe."""

from __future__ import annotations

import time

from app.mt5.diagnostics import ConnectionProbe, run_smoke_test
from app.mt5.models import ConnectRequest
from tests.fakes.fake_mt5 import (
    ACCOUNT_TRADE_MODE_REAL,
    FakeAccountConfig,
    FakeMetaTrader5,
    FakeSymbolConfig,
)


def make_request(**overrides: object) -> ConnectRequest:
    values: dict[str, object] = {
        "login": 12345678,
        "password": "secret",
        "server": "FakeServer-Demo",
        "terminal_path": "",
    }
    values.update(overrides)
    return ConnectRequest(**values)  # type: ignore[arg-type]


def make_fake() -> FakeMetaTrader5:
    return FakeMetaTrader5(
        symbols=[
            FakeSymbolConfig("EURUSD.m"),
            FakeSymbolConfig("GBPUSD"),
            FakeSymbolConfig("XAUUSD.pro"),
        ]
    )


class TestSmokeTest:
    def test_happy_path(self) -> None:
        report = run_smoke_test(make_fake, make_request(), symbols=("EURUSD", "GBPUSD"))
        assert report.ok
        names = [step.name for step in report.steps]
        assert names == [
            "package",
            "connect",
            "account",
            "terminal",
            "symbols",
            "quotes",
            "latency",
            "disconnect",
        ]
        connect_step = report.steps[1]
        assert "***678" in connect_step.detail
        assert "secret" not in connect_step.detail

    def test_suffix_mapping_reported(self) -> None:
        report = run_smoke_test(make_fake, make_request(), symbols=("EURUSD",))
        symbols_step = next(step for step in report.steps if step.name == "symbols")
        assert "EURUSD->EURUSD.m" in symbols_step.detail

    def test_summary_contains_verdict(self) -> None:
        report = run_smoke_test(make_fake, make_request())
        assert any("PASSED" in line for line in report.summary_lines())
        assert any("انجام شد" in line for line in report.summary_lines(lang="fa"))

    def test_json_dict_serializable(self) -> None:
        import json

        report = run_smoke_test(make_fake, make_request())
        payload = report.to_dict()
        assert json.loads(json.dumps(payload))["ok"] is True

    def test_connect_failure_skips_rest(self) -> None:
        def failing_factory() -> FakeMetaTrader5:
            fake = make_fake()
            fake.fail_initialize = (-6, "Terminal: authorization failed")
            return fake

        report = run_smoke_test(failing_factory, make_request())
        assert not report.ok
        by_name = {step.name: step for step in report.steps}
        assert not by_name["connect"].ok
        assert by_name["account"].skipped
        assert by_name["quotes"].skipped
        assert (
            "ورود ناموفق" in by_name["connect"].detail
            or "Login failed" in by_name["connect"].detail
        )

    def test_real_account_still_smoke_ok(self) -> None:
        # smoke test is read-only; a real account is fine here
        def factory() -> FakeMetaTrader5:
            return FakeMetaTrader5(account=FakeAccountConfig(trade_mode=ACCOUNT_TRADE_MODE_REAL))

        report = run_smoke_test(factory, make_request())
        assert report.ok

    def test_missing_symbols_fail_symbols_step(self) -> None:
        fake = FakeMetaTrader5(symbols=[FakeSymbolConfig("EURUSD.m")])
        report = run_smoke_test(lambda: fake, make_request(), symbols=("GBPUSD",))
        assert not report.ok
        symbols_step = next(step for step in report.steps if step.name == "symbols")
        assert not symbols_step.ok


class TestConnectionProbe:
    def poll_until_finished(self, probe: ConnectionProbe, timeout_s: float = 10.0):
        deadline = time.monotonic() + timeout_s
        state = probe.poll()
        while not state.finished and time.monotonic() < deadline:
            time.sleep(0.02)
            state = probe.poll()
        return state

    def test_full_probe(self) -> None:
        probe = ConnectionProbe(make_request(), make_fake)
        probe.start()
        state = self.poll_until_finished(probe)
        assert state.finished
        assert state.ok
        assert "***678" in state.detail
        assert "EURUSD.m" in state.detail
        assert "session closed" in state.detail

    def test_probe_reports_auth_failure(self) -> None:
        def failing() -> FakeMetaTrader5:
            fake = make_fake()
            fake.fail_initialize = (-6, "Terminal: authorization failed")
            return fake

        probe = ConnectionProbe(make_request(), failing)
        probe.start()
        state = self.poll_until_finished(probe)
        assert state.finished
        assert not state.ok
        assert "Login failed" in state.detail or "ورود ناموفق" in state.detail

    def test_probe_progress(self) -> None:
        probe = ConnectionProbe(make_request(), make_fake)
        probe.start()
        state = probe.poll()
        assert 0 < state.progress <= 1.0
        self.poll_until_finished(probe)
        assert probe.poll().progress == 1.0

    def test_probe_cancel(self) -> None:
        probe = ConnectionProbe(make_request(), make_fake)
        probe.start()
        probe.cancel()
        assert probe.poll().finished
        assert not probe.poll().ok
