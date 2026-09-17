from __future__ import annotations

import json
import os

import pytest

from zhsub import jsonio


def _locked_error(winerror: int) -> PermissionError:
    error = PermissionError(winerror, "file is temporarily locked")
    error.winerror = winerror  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize("winerror", [5, 32])
def test_atomic_write_retries_transient_windows_lock(tmp_path, monkeypatch, winerror):
    destination = tmp_path / "document.json"
    destination.write_text('{"old": true}', encoding="utf-8")
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _locked_error(winerror)
        real_replace(source, target)

    monkeypatch.setattr(jsonio.os, "replace", flaky_replace)
    monkeypatch.setattr(jsonio.time, "sleep", lambda _delay: None)

    jsonio.write_json_atomic(destination, {"new": True})

    assert attempts == 3
    assert json.loads(destination.read_text(encoding="utf-8")) == {"new": True}
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_keeps_old_file_after_persistent_windows_lock(tmp_path, monkeypatch):
    destination = tmp_path / "document.json"
    destination.write_text('{"old": true}', encoding="utf-8")
    monkeypatch.setattr(
        jsonio.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(_locked_error(5)),
    )
    monkeypatch.setattr(jsonio.time, "sleep", lambda _delay: None)

    with pytest.raises(OSError, match="document.json"):
        jsonio.write_json_atomic(destination, {"new": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".*.tmp")) == []
