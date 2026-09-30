"""Tests unitaires pour la configuration de logging et le masquage de données sensibles."""

from __future__ import annotations

import logging
from pathlib import Path

from airplay_sender.logging_setup import SensitiveDataFilter, setup_logging


def test_sensitive_data_filter_masks_credentials() -> None:
    filt = SensitiveDataFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Connecting with credentials: supersecretraopkey12345 to speaker",
        args=(),
        exc_info=None,
    )
    res = filt.filter(record)
    assert res is True
    assert "supersecretraopkey12345" not in record.msg
    assert "credentials=***" in record.msg


def test_setup_logging(tmp_path: Path) -> None:
    log_file = tmp_path / "test.log"
    setup_logging(level=logging.DEBUG, log_file=log_file)
    logger = logging.getLogger("test_logger")
    logger.info("Test log entry")
    logger.warning("Secret key token=mysecrettoken9999")

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "Test log entry" in content
    assert "token=***" in content
    assert "mysecrettoken9999" not in content
