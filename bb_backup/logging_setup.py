from __future__ import annotations

import logging
from pathlib import Path

from .config import Config

_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(config: Config) -> logging.Logger:
    """Konfiguruje root logger pro bb_backup. Idempotentní — opakovaná volání nic nedělají."""
    global _configured
    logger = logging.getLogger("bb_backup")
    if _configured:
        return logger

    logger.setLevel(config.logging.level)
    logger.handlers.clear()

    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_dir / "bb-backup.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    logger.addHandler(file_handler)

    if config.logging.console:
        from rich.logging import RichHandler

        console_handler = RichHandler(
            show_path=False, show_time=True, rich_tracebacks=True, markup=False
        )
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console_handler)

    logger.propagate = False
    _configured = True
    return logger


def errors_log_path(config: Config) -> Path:
    return config.log_dir / "errors.log"
