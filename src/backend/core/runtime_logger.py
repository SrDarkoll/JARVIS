"""Runtime logger wrapper with levels and structured context."""

from __future__ import annotations

import logging

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


def log_info(message: str, **ctx) -> None:
    logger = get_runtime_logger()
    if ctx:
        logger.info("%s | %s", message, ctx)
    else:
        logger.info("%s", message)


def log_warning(message: str, **ctx) -> None:
    logger = get_runtime_logger()
    if ctx:
        logger.warning("%s | %s", message, ctx)
    else:
        logger.warning("%s", message)


def log_error(message: str, **ctx) -> None:
    logger = get_runtime_logger()
    if ctx:
        logger.error("%s | %s", message, ctx)
    else:
        logger.error("%s", message)
