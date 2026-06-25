import shutil
import time
from pathlib import Path

from core.logger import get_logger
from core.encryption import encrypt_file, load_key
from utils.file_utils import ensure_dir, get_file_hash

logger = get_logger()

# Dashboard live events are optional — the core backup must run without them.
try:
    from dashboard.socket_events import emit_event
except ImportError:
    emit_event = lambda *a, **k: None

# Email alerts are optional too.
try:
    from utils.email_alerts import send_backup_success, send_backup_failure
except ImportError:
    send_backup_success = send_backup_failure = lambda *a, **k: None


class BackupEngine:
    def __init__(self, config: dict, notify=None):
        self.name = config["name"]
        self.watch_folder = Path(config["watch_folder"])
        self.backup_destination = Path(config["backup_destination"])
        self.max_versions = config.get("max_versions", 5)
        self._notify = notify or (lambda msg: None)

        # Per-sync-cycle progress tracking (reset at the start of each full_sync).
        self.files_count = 0
        self.start_time = 0.0

        # Encryption is opt-in per backup system (see Menu Option 1).
        self.encryption_enabled = bool(config.get("encryption_enabled", False))
        self.key = None
        if self.encryption_enabled:
            try:
                key_path = config.get("key_path")
                self.key = load_key(key_path) if key_path else load_key()
            except Exception as e:
                logger.error(f"[{self.name}] Could not load encryption key, "
                             f"disabling encryption: {e}")
                self.encryption_enabled = False

    def _backup_path(self, source: Path) -> Path:
        try:
            rel = source.relative_to(self.watch_folder)
        except ValueError:
            rel = Path(source.name)
        return self.backup_destination / rel

    def _stored_path(self, source: Path) -> Path:
        """Where a source file actually lands in the backup (adds .enc when encrypted)."""
        dst = self._backup_path(source)
        if self.encryption_enabled:
            return dst.with_name(dst.name + ".enc")
        return dst

    def backup_file(self, source_path):
        src = Path(source_path)
        if not src.is_file():
            return

        try:
            if self.encryption_enabled and self.key:
                enc_dst = self._stored_path(src)
                if enc_dst.exists():
                    self._rotate_versions(enc_dst)
                ensure_dir(enc_dst.parent)
                # Encrypt straight from the source into the backup destination.
                encrypt_file(src, enc_dst, self.key)
                logger.debug(f"Backed up (encrypted): {src} → {enc_dst}")
                return

            dst = self._backup_path(src)
            if dst.exists():
                if get_file_hash(src) == get_file_hash(dst):
                    logger.debug(f"Skipped (unchanged): {src.name}")
                    return
                self._rotate_versions(dst)

            ensure_dir(dst.parent)
            shutil.copy2(src, dst)
            logger.debug(f"Backed up: {src} → {dst}")
        except Exception as e:
            logger.error(f"Failed to back up {src}: {e}")
            emit_event("backup_failed", {"system": self.name, "error": str(e)})
            send_backup_failure(self.name, str(e))

    def delete_backup(self, source_path):
        src = Path(source_path)
        dst = self._stored_path(src)
        if dst.exists():
            try:
                dst.unlink()
                logger.debug(f"Deleted from backup: {dst}")
            except Exception as e:
                logger.error(f"Failed to delete backup {dst}: {e}")

    def move_backup(self, src_path, dest_path):
        backup_src = self._stored_path(Path(src_path))
        backup_dst = self._stored_path(Path(dest_path))
        if backup_src.exists():
            try:
                ensure_dir(backup_dst.parent)
                backup_src.rename(backup_dst)
                logger.debug(f"Moved in backup: {backup_src.name} → {backup_dst.name}")
            except Exception as e:
                logger.error(f"Failed to move backup {backup_src}: {e}")

    def full_sync(self, announce: bool = False) -> int:
        # Reset per-cycle tracking.
        self.files_count = 0
        self.start_time = time.monotonic()

        ensure_dir(self.backup_destination)

        if not self.watch_folder.exists():
            msg = f"Watch folder does not exist: {self.watch_folder}"
            logger.error(f"[{self.name}] Backup FAILED — {msg}")
            emit_event("backup_failed", {"system": self.name, "error": msg})
            send_backup_failure(self.name, msg)
            return 0

        files = [f for f in self.watch_folder.rglob("*") if f.is_file()]
        total = len(files)

        emit_event("backup_started", {"system": self.name, "total": total})

        try:
            for src_file in files:
                self.backup_file(src_file)
                self.files_count += 1
                if self.files_count % 10 == 0:
                    emit_event("backup_progress", {
                        "system": self.name,
                        "count": self.files_count,
                        "total": total,
                    })

            duration = time.monotonic() - self.start_time
            logger.info(
                f"[{self.name}] Full sync complete — {total} file(s) in {duration:.2f}s."
            )
            emit_event("backup_complete", {
                "system": self.name,
                "count": self.files_count,
                "duration": round(duration, 2),
            })
            send_backup_success(self.name, self.files_count, duration)
        except Exception as e:
            duration = time.monotonic() - self.start_time
            logger.error(f"[{self.name}] Backup FAILED — {e}")
            emit_event("backup_failed", {"system": self.name, "error": str(e)})
            send_backup_failure(self.name, str(e))

        if announce:
            self._notify(f"\n  ✓ '{self.name}' — initial backup complete ({total} file(s) backed up).")

        return total

    def _rotate_versions(self, backup_path: Path):
        stem = backup_path.stem
        suffix = backup_path.suffix
        parent = backup_path.parent

        oldest = parent / f"{stem}.v{self.max_versions}{suffix}"
        if oldest.exists():
            try:
                oldest.unlink()
                logger.debug(f"Removed oldest version: {oldest.name}")
            except Exception as e:
                logger.error(f"Failed to remove oldest version {oldest}: {e}")

        for v in range(self.max_versions - 1, 0, -1):
            old = parent / f"{stem}.v{v}{suffix}"
            new = parent / f"{stem}.v{v + 1}{suffix}"
            if old.exists():
                try:
                    old.rename(new)
                except Exception as e:
                    logger.error(f"Failed to shift version {old.name}: {e}")

        v1 = parent / f"{stem}.v1{suffix}"
        try:
            backup_path.rename(v1)
            logger.debug(f"Versioned: {backup_path.name} → {v1.name}")
        except Exception as e:
            logger.error(f"Failed to create v1 for {backup_path.name}: {e}")
