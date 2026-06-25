"""Socket.IO event plumbing for the backITup dashboard."""

from core.logger import get_logger

logger = get_logger()

_socketio = None


def init_socketio():
    """Initialize the SocketIO instance. Must be called inside create_app()."""
    global _socketio
    try:
        from flask_socketio import SocketIO
        _socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")
        logger.info("SocketIO initialized.")
    except Exception as e:
        _socketio = None
        logger.error(f"SocketIO init failed: {e}")


def get_socketio():
    """Return the shared SocketIO instance (may be None if not initialized)."""
    return _socketio


def emit_event(event_name: str, data=None) -> None:
    """Broadcast an event to dashboard clients. Fail-silent."""
    try:
        if _socketio is not None:
            _socketio.emit(event_name, data or {})
    except Exception as e:
        logger.error(f"Failed to emit socket event '{event_name}': {e}")
