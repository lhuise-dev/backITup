"""
Socket.IO event plumbing for the dashboard.

The SocketIO instance lives here so that both the Flask app factory and the
backup engine can reach it without a circular import. If Flask-SocketIO is not
installed (dashboard never launched), everything degrades to no-ops.
"""

from core.logger import get_logger

logger = get_logger()

# Valid events the rest of the system may emit.
BACKUP_EVENTS = (
    "backup_started",
    "backup_progress",
    "backup_complete",
    "backup_failed",
)

try:
    from flask_socketio import SocketIO

    _socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
except Exception as e:  # ImportError or any init failure
    _socketio = None
    logger.debug(f"SocketIO unavailable (dashboard optional): {e}")


def get_socketio():
    """Return the shared SocketIO instance (may be None if Flask not installed)."""
    return _socketio


def emit_event(event_name: str, data=None) -> None:
    """
    Broadcast an event to connected dashboard clients. Fail-silent: the core
    backup must never break because the dashboard is down.
    """
    try:
        if _socketio is not None:
            _socketio.emit(event_name, data or {})
    except Exception as e:
        logger.error(f"Failed to emit socket event '{event_name}': {e}")
