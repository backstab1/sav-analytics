"""Замена project.json и истории, пока файл читает другой запрос."""

import threading
from pathlib import Path

import pytest

from sav_analytics import atomic_file
from sav_analytics.atomic_file import read_text, replace_file


def test_replace_waits_for_a_reader_holding_the_target(tmp_path: Path) -> None:
    target = tmp_path / "project.json"
    target.write_text("старое", encoding="utf-8")
    temporary = tmp_path / ".project.json.tmp"
    temporary.write_text("новое", encoding="utf-8")

    # В Windows открытый на чтение файл нельзя заменить, пока его не закроют.
    reader = target.open(encoding="utf-8")
    closer = threading.Timer(0.05, reader.close)
    closer.start()
    try:
        replace_file(temporary, target)
    finally:
        closer.join()
        reader.close()

    assert target.read_text(encoding="utf-8") == "новое"
    assert not temporary.exists()


def test_replace_gives_up_on_a_file_that_stays_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = []

    def locked(source: Path, target: Path) -> None:
        attempts.append(target)
        raise PermissionError(5, "Отказано в доступе")

    monkeypatch.setattr(atomic_file.os, "replace", locked)
    monkeypatch.setattr(atomic_file.time, "sleep", lambda seconds: None)

    with pytest.raises(PermissionError):
        replace_file(tmp_path / "a", tmp_path / "b")
    assert len(attempts) == atomic_file._ATTEMPTS


def test_read_waits_out_a_replace_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Открыть файл, поверх которого идёт замена, Windows не даёт (Errno 13)."""
    target = tmp_path / "project.json"
    target.write_text("новое", encoding="utf-8")
    original = Path.read_text
    refusals = [PermissionError(13, "Permission denied")] * 2

    def busy(path: Path, *args: object, **kwargs: object) -> str:
        if refusals:
            raise refusals.pop()
        return original(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", busy)
    monkeypatch.setattr(atomic_file.time, "sleep", lambda seconds: None)

    assert read_text(target) == "новое"
    assert not refusals
