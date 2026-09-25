"""i18n tests: key parity, translation, fallback, persistence, RTL."""

from __future__ import annotations

import pytest

from app.core.settings import UiSettings
from app.ui.i18n.strings import STRINGS
from app.ui.i18n.translator import Translator


def test_all_languages_share_the_same_keys() -> None:
    key_sets = {lang: set(table) for lang, table in STRINGS.items()}
    assert set(key_sets) == {"en", "fa"}
    reference = key_sets["en"]
    for lang, keys in key_sets.items():
        assert keys == reference, f"{lang} keys differ from en"


def test_english_translation(translator: Translator) -> None:
    assert translator.language == "en"
    assert translator.translate("nav.dashboard") == "Dashboard"


def test_persian_translation_and_rtl(translator: Translator, settings_path: str) -> None:
    translator.set_language("fa")
    assert translator.translate("nav.dashboard") == "داشبورد"
    assert translator.is_rtl is True
    assert translator.layout_direction().name == "RightToLeft"

    reloaded = UiSettings.load(settings_path)
    assert reloaded.language == "fa"


def test_missing_key_returns_key(translator: Translator) -> None:
    assert translator.translate("no.such.key") == "no.such.key"


def test_placeholder_formatting(translator: Translator) -> None:
    assert translator.translate("empty.phase", phase=5) == "This page is built in Phase 5."
    assert translator.translate("command.goto", page="Market") == "Go to Market"


def test_unknown_language_raises(translator: Translator) -> None:
    with pytest.raises(ValueError, match="unknown language"):
        translator.set_language("de")


def test_set_same_language_is_noop(translator: Translator) -> None:
    translator.set_language("en")
    assert translator.language == "en"
