"""Tests for secret masking (SPEC C-security, E3)."""

from __future__ import annotations

from app.observability.masking import REDACTED, mask_dict, mask_extras, mask_text


class TestMaskText:
    def test_kv_equals(self) -> None:
        assert mask_text("login password=hunter2 failed") == f"login password={REDACTED} failed"

    def test_kv_colon(self) -> None:
        assert mask_text("api_key: abcd1234") == f"api_key: {REDACTED}"

    def test_kv_json_style(self) -> None:
        assert mask_text('{"password": "hunter2", "user": "ali"}') == (
            f'{{"password": "{REDACTED}", "user": "ali"}}'
        )

    def test_bearer_header(self) -> None:
        out = mask_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
        assert "eyJ" not in out
        assert REDACTED in out

    def test_url_token(self) -> None:
        out = mask_text("https://example.com/rest/v1?apikey=secret123&limit=5")
        assert "secret123" not in out
        assert "limit=5" in out

    def test_multiple_secrets(self) -> None:
        out = mask_text("connect password=one and token=two now")
        assert "password=one" not in out
        assert "token=two" not in out
        assert out.count(REDACTED) == 2

    def test_quoted_value(self) -> None:
        assert mask_text("set password='my secret pw' ok") == f"set password={REDACTED} ok"

    def test_clean_text_untouched(self) -> None:
        text = "order filled: 0.10 EURUSD @ 1.08432, profit +12.30 USD"
        assert mask_text(text) == text

    def test_non_secret_keys_untouched(self) -> None:
        text = "symbol=XAUUSD timeframe=H1"
        assert mask_text(text) == text

    def test_empty(self) -> None:
        assert mask_text("") == ""

    def test_case_insensitive_keys(self) -> None:
        assert mask_text("PASSWORD=abc TOKEN=def") == f"PASSWORD={REDACTED} TOKEN={REDACTED}"

    def test_value_with_trailing_punctuation(self) -> None:
        out = mask_text("auth failed for password=abc123, retrying")
        assert "abc123" not in out


class TestMaskDict:
    def test_secret_keys_redacted(self) -> None:
        out = mask_dict({"login": 123, "password": "hunter2", "server": "Demo"})
        assert out["password"] == REDACTED
        assert out["login"] == 123
        assert out["server"] == "Demo"

    def test_recursive(self) -> None:
        payload = {"mt5": {"login_password": "s3cret", "host": "x"}, "list": [{"api_key": "k"}]}
        out = mask_dict(payload)
        assert out["mt5"]["login_password"] == REDACTED
        assert out["mt5"]["host"] == "x"
        assert out["list"][0]["api_key"] == REDACTED

    def test_values_also_masked_as_text(self) -> None:
        out = mask_dict({"note": "password=abc inside"})
        assert "abc" not in out["note"]

    def test_none(self) -> None:
        assert mask_dict(None) == {}

    def test_key_normalisation(self) -> None:
        out = mask_dict({"API-KEY": "k", "Api Key": "k2", "apikey2": "keep"})
        assert out["API-KEY"] == REDACTED
        assert out["Api Key"] == REDACTED
        assert out["apikey2"] == "keep"

    def test_original_untouched(self) -> None:
        payload = {"password": "x"}
        mask_dict(payload)
        assert payload["password"] == "x"


class TestMaskExtras:
    def test_strings_masked(self) -> None:
        extras = {"symbol": "EURUSD", "note": "token=abc123"}
        mask_extras(extras)
        assert extras["symbol"] == "EURUSD"
        assert "abc123" not in extras["note"]
