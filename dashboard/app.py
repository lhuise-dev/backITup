"""Flask application factory for the backITup dashboard."""

import os
from pathlib import Path

from flask import Flask
from flask_login import LoginManager, UserMixin

from core import auth
from core.logger import get_logger
from dashboard.socket_events import init_socketio, get_socketio

logger = get_logger()

_DATA_DIR = Path(__file__).parent.parent / "data"
_SECRET_FILE = _DATA_DIR / "dashboard_secret.key"


def _get_secret_key() -> bytes:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        if _SECRET_FILE.exists():
            return _SECRET_FILE.read_bytes()
        secret = os.urandom(24)
        _SECRET_FILE.write_bytes(secret)
        return secret
    except Exception as e:
        logger.error(f"Failed to load/create dashboard secret key: {e}")
        return os.urandom(24)


class DashboardUser(UserMixin):
    def __init__(self, username: str):
        self.id = username
        self.username = username
        self.role = auth.get_role(username)

    @property
    def is_admin(self) -> bool:
        return self.role == auth.ROLE_ADMIN


def create_app():
    """Build and return a configured Flask app."""
    # Initialize SocketIO first before anything else
    init_socketio()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = _get_secret_key()
    app.config["DEBUG"] = False

    login_manager = LoginManager()
    login_manager.login_view = "routes.login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            if auth.user_exists(user_id):
                return DashboardUser(user_id)
        except Exception as e:
            logger.error(f"user_loader failed for '{user_id}': {e}")
        return None

    from dashboard.routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    socketio = get_socketio()
    if socketio is not None:
        socketio.init_app(app)

    logger.info("Dashboard Flask app created.")
    return app
