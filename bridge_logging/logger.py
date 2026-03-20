from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_dir: Path, log_level: str) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file: Path = log_dir / "bridge-app.log"

    logger: logging.Logger = logging.getLogger("bridge_app")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter: logging.Formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler: logging.FileHandler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logger.level)

    console_handler: logging.StreamHandler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logger.level)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
