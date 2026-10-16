from zoneinfo import ZoneInfo

import pytest

from ebb import config
from ebb.config import ConfigError, resolve_timezone


def test_resolve_timezone_accepts_a_name():
    assert resolve_timezone("Asia/Dhaka") == ZoneInfo("Asia/Dhaka")


def test_resolve_timezone_strips_spaces():
    assert resolve_timezone("  Asia/Dhaka ") == ZoneInfo("Asia/Dhaka")


def test_resolve_timezone_rejects_unknown_names():
    with pytest.raises(ConfigError, match="EBB_TIMEZONE 'Mars/Olympus' is not a known time zone"):
        resolve_timezone("Mars/Olympus")


@pytest.mark.parametrize("unset", [None, "", "   "])
def test_resolve_timezone_unset_uses_the_computer(monkeypatch, unset):
    monkeypatch.setattr(config.tzlocal, "get_localzone_name", lambda: "Asia/Dhaka")
    assert resolve_timezone(unset) == ZoneInfo("Asia/Dhaka")


def test_resolve_timezone_falls_back_to_utc(monkeypatch):
    def boom():
        raise RuntimeError("no zone")

    monkeypatch.setattr(config.tzlocal, "get_localzone_name", boom)
    assert resolve_timezone(None) == ZoneInfo("UTC")


def test_resolve_timezone_falls_back_to_utc_when_the_computer_gives_none(monkeypatch):
    monkeypatch.setattr(config.tzlocal, "get_localzone_name", lambda: None)
    assert resolve_timezone(None) == ZoneInfo("UTC")
