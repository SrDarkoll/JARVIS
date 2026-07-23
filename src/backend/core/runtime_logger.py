"""Runtime logger wrapper with levels and structured context."""

from __future__ import annotations

import logging

from core.unified_log import write_log

LOGGER_NAME = "JARVIS"


def get_runtime_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    return logger


def _log(
    level: int,
    message: str,
    *,
    journal_category: str,
    context: dict,
) -> None:
    logger = get_runtime_logger()
    if context:
        logger.log(level, "%s | %s", message, context)
    else:
        logger.log(level, "%s", message)
    write_log(journal_category, message, **context)


def log_info(message: str, *, journal_category: str = "INFO", **ctx) -> None:
    _log(
        logging.INFO,
        message,
        journal_category=journal_category,
        context=ctx,
    )


def log_warning(message: str, *, journal_category: str = "WARNING", **ctx) -> None:
    _log(
        logging.WARNING,
        message,
        journal_category=journal_category,
        context=ctx,
    )


def log_error(message: str, *, journal_category: str = "ERROR", **ctx) -> None:
    _log(
        logging.ERROR,
        message,
        journal_category=journal_category,
        context=ctx,
    )
