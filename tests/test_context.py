"""Tests for session/trace correlation context (SPEC E3.1, E3.5)."""

from __future__ import annotations

import threading

from app.observability.context import (
    get_session_id,
    get_trace_id,
    new_session_id,
    new_trace_id,
    set_session_id,
    trace,
)


class TestIds:
    def test_trace_id_shape_and_uniqueness(self) -> None:
        ids = {new_trace_id() for _ in range(100)}
        assert len(ids) == 100
        assert all(tid.startswith("t-") for tid in ids)

    def test_session_id_shape_and_uniqueness(self) -> None:
        ids = {new_session_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(sid.startswith("s-") for sid in ids)


class TestContext:
    def test_default_empty(self) -> None:
        token = set_session_id("")
        try:
            assert get_session_id() == ""
            assert get_trace_id() == ""
        finally:
            set_session_id(token)

    def test_trace_generates_and_restores(self) -> None:
        assert get_trace_id() == ""
        with trace() as tid:
            assert tid.startswith("t-")
            assert get_trace_id() == tid
        assert get_trace_id() == ""

    def test_trace_joins_existing_id(self) -> None:
        with trace("t-fixed") as tid:
            assert tid == "t-fixed"
            with trace() as inner:
                assert inner != "t-fixed"
                assert get_trace_id() == inner
            assert get_trace_id() == "t-fixed"

    def test_trace_restores_after_exception(self) -> None:
        outer = "t-outer"
        with trace(outer):
            try:
                with trace():
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            assert get_trace_id() == outer

    def test_thread_isolation(self) -> None:
        results: dict[str, str] = {}

        def worker() -> None:
            with trace() as tid:
                results["worker"] = tid

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        with trace() as main_tid:
            assert results["worker"] != main_tid
