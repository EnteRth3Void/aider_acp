import logging
import os
import sys
from pathlib import Path

AIDER_ACP_LOG_LEVEL_ENV = "AIDER_ACP_LOG_LEVEL"
_VALID_LEVELS = frozenset({"off", "warning", "debug"})
_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def parse_log_level(env: str | None) -> int | None:
    """Map AIDER_ACP_LOG_LEVEL to a logging level.

    Returns None for ``off`` (logging disabled). Unset, empty, ``warning``,
    and invalid values map to WARNING.
    """
    if env is None:
        return logging.WARNING
    normalized = env.strip().lower()
    if not normalized or normalized == "warning":
        return logging.WARNING
    if normalized == "debug":
        return logging.DEBUG
    if normalized == "off":
        return None
    return logging.WARNING


def _is_invalid_log_level(env: str | None) -> bool:
    if env is None:
        return False
    normalized = env.strip().lower()
    if not normalized:
        return False
    return normalized not in _VALID_LEVELS


def configure_logging(log_path: Path) -> None:
    """Configure the root logger from AIDER_ACP_LOG_LEVEL.

    Replaces existing handlers. stdout is left alone so ACP JSON-RPC is not
    mixed with log lines.
    """
    raw = os.environ.get(AIDER_ACP_LOG_LEVEL_ENV)
    level = parse_log_level(raw)

    logging.disable(logging.NOTSET)

    if level is None:
        logging.basicConfig(handlers=[logging.NullHandler()], force=True)
        logging.getLogger().setLevel(logging.CRITICAL)
        logging.disable(logging.CRITICAL)
        return

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        handlers=[
            logging.FileHandler(log_path, mode="a"),
            logging.StreamHandler(sys.stderr),
        ],
        force=True,
    )

    if _is_invalid_log_level(raw):
        logging.getLogger(__name__).warning(
            "Invalid AIDER_ACP_LOG_LEVEL=%r; falling back to warning",
            raw,
        )
