import json
from datetime import datetime
from pathlib import Path

import bcrypt

from core.logger import get_logger

logger = get_logger()

_USERS_FILE = Path(__file__).parent.parent / "data" / "users.json"

ROLE_ADMIN = "admin"
ROLE_USER = "user"
VALID_ROLES = (ROLE_ADMIN, ROLE_USER)


def _load_users() -> list:
    try:
        if not _USERS_FILE.exists():
            return []
        return json.loads(_USERS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to load users: {e}")
        return []


def _save_users(users: list) -> None:
    try:
        _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USERS_FILE.write_text(json.dumps(users, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save users: {e}")


def users_exist() -> bool:
    """True if at least one user account is registered."""
    return bool(_load_users())


def user_exists(username: str) -> bool:
    return any(u["username"] == username for u in _load_users())


def register_user(username: str, password: str, role: str = ROLE_USER) -> bool:
    """Hash the password with bcrypt and persist a new user. Returns success."""
    try:
        username = (username or "").strip()
        if not username or not password:
            logger.error("register_user: username and password are required.")
            return False
        if role not in VALID_ROLES:
            logger.error(f"register_user: invalid role '{role}'.")
            return False

        users = _load_users()
        if any(u["username"] == username for u in users):
            logger.error(f"register_user: user '{username}' already exists.")
            return False

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        users.append({
            "username": username,
            "password_hash": password_hash.decode("utf-8"),
            "role": role,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save_users(users)
        logger.info(f"Registered user '{username}' with role '{role}'.")
        return True
    except Exception as e:
        logger.error(f"Failed to register user '{username}': {e}")
        return False


def login(username: str, password: str) -> bool:
    """Verify a username/password pair against the stored bcrypt hash."""
    try:
        users = _load_users()
        user = next((u for u in users if u["username"] == username), None)
        if not user:
            return False
        return bcrypt.checkpw(
            password.encode("utf-8"),
            user["password_hash"].encode("utf-8"),
        )
    except Exception as e:
        logger.error(f"Login failed for '{username}': {e}")
        return False


def get_role(username: str) -> str:
    """Return the role for a user, or empty string if unknown."""
    try:
        user = next((u for u in _load_users() if u["username"] == username), None)
        return user["role"] if user else ""
    except Exception as e:
        logger.error(f"Failed to get role for '{username}': {e}")
        return ""


def list_users() -> list:
    """Return all users without their password hashes (admin only)."""
    try:
        return [
            {"username": u["username"], "role": u["role"], "created_at": u["created_at"]}
            for u in _load_users()
        ]
    except Exception as e:
        logger.error(f"Failed to list users: {e}")
        return []


def delete_user(username: str) -> bool:
    """Remove a user by name (admin only). Returns success."""
    try:
        users = _load_users()
        remaining = [u for u in users if u["username"] != username]
        if len(remaining) == len(users):
            logger.error(f"delete_user: user '{username}' not found.")
            return False
        _save_users(remaining)
        logger.info(f"Deleted user '{username}'.")
        return True
    except Exception as e:
        logger.error(f"Failed to delete user '{username}': {e}")
        return False
