"""Console and file logging.

One shared Rich console so agents, the workflow and the CLI render consistently,
plus a per-run file log. Log records never contain secrets: everything that
touches a credential goes through :func:`orchestrator.core.credentials.redact`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

THEME = Theme(
    {
        "agent": "bold cyan",
        "gate": "bold yellow",
        "ok": "bold green",
        "fail": "bold red",
        "muted": "dim",
        "phase": "bold magenta",
    }
)

console = Console(theme=THEME, highlight=False, soft_wrap=False)
err_console = Console(theme=THEME, stderr=True, highlight=False)

_LOGGER_NAME = "orchestrator"


def configure_logging(level: str = "INFO", *, log_file: Path | None = None) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    handler = RichHandler(
        console=err_console,
        show_time=False,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    )
    handler.setLevel(logger.level)
    logger.addHandler(handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)

    return logger


def get_logger(suffix: str | None = None) -> logging.Logger:
    return logging.getLogger(f"{_LOGGER_NAME}.{suffix}" if suffix else _LOGGER_NAME)
