"""Unit tests for the daily log archive sink and retention cleanup."""

from __future__ import annotations

import logging
from datetime import date, datetime

from aistudio_api.infrastructure.utils.log_broadcast import (
    DailyLogFileHandler,
    cleanup_old_logs,
    daily_log_path,
)


class _Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def date(self) -> date:
        return self.current.date()


def _emit(
    handler: DailyLogFileHandler, message: str, level: int = logging.INFO
) -> None:
    record = logging.LogRecord(
        name="aistudio.test.archive",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    handler.emit(record)


def test_daily_log_path_uses_compact_date(tmp_path):
    assert daily_log_path(date(2026, 10, 20), log_dir=tmp_path).name == (
        "aistudio-20261020.log"
    )


def test_daily_handler_writes_sanitized_line(tmp_path):
    clock = _Clock(datetime(2026, 10, 20, 12, 34, 56))
    handler = DailyLogFileHandler(
        log_dir=tmp_path, date_factory=clock.date, clock=clock
    )
    try:
        _emit(handler, "Authorization: Bearer secret-token")
    finally:
        handler.close()

    target = tmp_path / "aistudio-20261020.log"
    text = target.read_text(encoding="utf-8")
    assert "2026-10-20 12:34:56 INFO aistudio.test.archive" in text
    assert "secret-token" not in text
    assert "Bearer ***" in text


def test_daily_handler_switches_file_on_day_change(tmp_path):
    clock = _Clock(datetime(2026, 10, 20, 23, 59, 0))
    handler = DailyLogFileHandler(
        log_dir=tmp_path, date_factory=clock.date, clock=clock
    )
    try:
        _emit(handler, "day-one")
        clock.current = datetime(2026, 10, 21, 0, 1, 0)
        _emit(handler, "day-two")
    finally:
        handler.close()

    assert "day-one" in (tmp_path / "aistudio-20261020.log").read_text(encoding="utf-8")
    assert "day-two" in (tmp_path / "aistudio-20261021.log").read_text(encoding="utf-8")


def test_cleanup_keeps_seven_calendar_days_and_ignores_other_files(tmp_path):
    today = date(2026, 10, 20)
    for offset in range(10):
        current = date.fromordinal(today.toordinal() - offset)
        (tmp_path / f"aistudio-{current.strftime('%Y%m%d')}.log").write_text(
            "content", encoding="utf-8"
        )
    (tmp_path / "notes.txt").write_text("keep", encoding="utf-8")

    removed = cleanup_old_logs(tmp_path, retention_days=7, today=today)

    remaining = sorted(path.name for path in tmp_path.glob("aistudio-*.log"))
    assert remaining == [
        "aistudio-20261014.log",
        "aistudio-20261015.log",
        "aistudio-20261016.log",
        "aistudio-20261017.log",
        "aistudio-20261018.log",
        "aistudio-20261019.log",
        "aistudio-20261020.log",
    ]
    assert len(removed) == 3
    assert (tmp_path / "notes.txt").exists()
