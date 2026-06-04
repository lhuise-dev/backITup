import hashlib
import shutil
from pathlib import Path
from typing import Optional


def get_file_hash(path) -> Optional[str]:
    try:
        sha256 = hashlib.sha256()
        with open(Path(path), "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception:
        return None


def normalize_path(path) -> Path:
    return Path(path).expanduser().resolve()


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def delete_path(path):
    p = Path(path)
    try:
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
    except Exception:
        pass
