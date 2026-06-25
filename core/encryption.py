import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from core.logger import get_logger

logger = get_logger()

_DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_KEY_PATH = _DATA_DIR / "backup.key"

# PBKDF2 parameters
_KDF_ITERATIONS = 480_000
_SALT_BYTES = 16


def generate_salt() -> bytes:
    """Return a fresh random salt for a new backup system."""
    return os.urandom(_SALT_BYTES)


def encode_salt(salt: bytes) -> str:
    """Encode raw salt bytes into a JSON-storable string."""
    return base64.urlsafe_b64encode(salt).decode("utf-8")


def decode_salt(salt_str: str) -> bytes:
    """Decode a stored salt string back into raw bytes."""
    return base64.urlsafe_b64decode(salt_str.encode("utf-8"))


def generate_key(password: str, salt: bytes = None) -> bytes:
    """
    Derive a Fernet key from a user password using PBKDF2HMAC + SHA256.

    If no salt is provided a fresh one is generated. The salt MUST be stored
    (see encode_salt) so the same key can be re-derived later for decryption.
    """
    try:
        if salt is None:
            salt = generate_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_KDF_ITERATIONS,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    except Exception as e:
        logger.error(f"Failed to generate encryption key: {e}")
        raise


def encrypt_file(source_path, dest_path, key) -> Path:
    """
    Encrypt `source_path` with `key` and write the ciphertext to `dest_path`.

    The destination is always given a `.enc` suffix. Returns the final path.
    """
    try:
        src = Path(source_path)
        dst = Path(dest_path)
        if dst.suffix != ".enc":
            dst = dst.with_name(dst.name + ".enc")

        fernet = Fernet(key)
        token = fernet.encrypt(src.read_bytes())

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(token)
        logger.debug(f"Encrypted: {src} → {dst}")
        return dst
    except Exception as e:
        logger.error(f"Failed to encrypt {source_path}: {e}")
        raise


def decrypt_file(enc_path, dest_path, key) -> Path:
    """
    Decrypt a `.enc` file back to its original form at `dest_path`.
    """
    try:
        enc = Path(enc_path)
        dst = Path(dest_path)
        if dst.suffix == ".enc":
            dst = dst.with_suffix("")

        fernet = Fernet(key)
        data = fernet.decrypt(enc.read_bytes())

        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        logger.debug(f"Decrypted: {enc} → {dst}")
        return dst
    except Exception as e:
        logger.error(f"Failed to decrypt {enc_path}: {e}")
        raise


def save_key(key, path=DEFAULT_KEY_PATH) -> None:
    """Persist a derived key to disk (default: data/backup.key)."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(key)
        logger.debug(f"Saved encryption key to {p}")
    except Exception as e:
        logger.error(f"Failed to save key to {path}: {e}")
        raise


def load_key(path=DEFAULT_KEY_PATH) -> bytes:
    """Load a previously saved key from disk (default: data/backup.key)."""
    try:
        return Path(path).read_bytes()
    except Exception as e:
        logger.error(f"Failed to load key from {path}: {e}")
        raise
