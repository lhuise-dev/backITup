import logging
from datetime import datetime
from pathlib import Path

_logger = None


class _Formatter(logging.Formatter):
    def format(self, record):
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        return f"[{ts}] {record.levelname} — {record.getMessage()}"


def get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("backITup")
    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(_Formatter())

    fh = logging.FileHandler(log_dir / "backITup.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_Formatter())

    logger.addHandler(console)
    logger.addHandler(fh)

    _logger = logger
    return _logger
