"""MT5 error taxonomy and friendly message tests (SPEC C7)."""

from __future__ import annotations

import pytest

from app.mt5.errors import (
    RETCODE_DONE,
    RETCODE_DONE_PARTIAL,
    RETCODE_NO_MONEY,
    RETCODE_PLACED,
    RETCODE_TIMEOUT,
    TERMINAL_CODE_AUTH_FAILED,
    AuthError,
    ConnectionLostError,
    InvalidSymbolError,
    MT5Error,
    TerminalError,
    TradeError,
    friendly_message,
    raise_for_order_result,
)


class _ModuleWithLastError:
    def __init__(self, code: int, description: str) -> None:
        self._pair = (code, description)

    def last_error(self) -> tuple[int, str]:
        return self._pair


class _Result:
    def __init__(self, retcode: int, comment: str = "") -> None:
        self.retcode = retcode
        self.comment = comment


class TestFriendlyMessages:
    def test_auth_message_en(self) -> None:
        text = friendly_message(TERMINAL_CODE_AUTH_FAILED)
        assert "Login failed" in text

    def test_auth_message_fa(self) -> None:
        text = friendly_message(TERMINAL_CODE_AUTH_FAILED, lang="fa")
        assert "ورود ناموفق" in text

    def test_no_money_message(self) -> None:
        text = friendly_message(RETCODE_NO_MONEY)
        assert "margin" in text.lower()

    def test_unknown_code_with_description(self) -> None:
        text = friendly_message(12345, "weird failure")
        assert text == "MT5 error 12345: weird failure"

    def test_unknown_code_without_description(self) -> None:
        assert "12345" in friendly_message(12345)

    def test_trade_code_preferred_for_positive_codes(self) -> None:
        text = friendly_message(10018)
        assert "closed" in text.lower()


class TestExceptions:
    def test_hierarchy(self) -> None:
        assert issubclass(AuthError, TerminalError)
        assert issubclass(TerminalError, MT5Error)
        assert issubclass(TradeError, MT5Error)
        assert issubclass(ConnectionLostError, MT5Error)

    def test_auth_error_message(self) -> None:
        error = AuthError(TERMINAL_CODE_AUTH_FAILED, "Terminal: authorization failed")
        assert "Login failed" in str(error)
        assert error.code == TERMINAL_CODE_AUTH_FAILED

    def test_trade_error_ambiguity_flag(self) -> None:
        ambiguous = TradeError(RETCODE_TIMEOUT, "timeout", ambiguous=True)
        definite = TradeError(RETCODE_NO_MONEY, "no money")
        assert ambiguous.ambiguous is True
        assert definite.ambiguous is False

    def test_invalid_symbol_error(self) -> None:
        error = InvalidSymbolError("EURUSD")
        assert error.symbol == "EURUSD"
        assert "EURUSD" in str(error)


class TestRaiseForOrderResult:
    def test_done_is_success(self) -> None:
        raise_for_order_result(_Result(RETCODE_DONE), _ModuleWithLastError(0, ""))

    def test_placed_is_success(self) -> None:
        raise_for_order_result(_Result(RETCODE_PLACED), _ModuleWithLastError(0, ""))

    def test_partial_is_success(self) -> None:
        raise_for_order_result(_Result(RETCODE_DONE_PARTIAL), _ModuleWithLastError(0, ""))

    def test_rejection_raises_with_comment(self) -> None:
        with pytest.raises(TradeError) as excinfo:
            raise_for_order_result(
                _Result(RETCODE_NO_MONEY, "no money"), _ModuleWithLastError(0, "")
            )
        assert excinfo.value.code == RETCODE_NO_MONEY
        assert excinfo.value.description == "no money"

    def test_rejection_falls_back_to_last_error(self) -> None:
        with pytest.raises(TradeError) as excinfo:
            raise_for_order_result(
                _Result(RETCODE_NO_MONEY), _ModuleWithLastError(10031, "no connection")
            )
        assert "no connection" in excinfo.value.description

    def test_timeout_marked_ambiguous(self) -> None:
        with pytest.raises(TradeError) as excinfo:
            raise_for_order_result(_Result(RETCODE_TIMEOUT, "timeout"), _ModuleWithLastError(0, ""))
        assert excinfo.value.ambiguous is True
