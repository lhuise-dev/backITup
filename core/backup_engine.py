import shutil
from pathlib import Path

from core.logger import get_logger
from utils.file_utils import ensure_dir, get_file_hash

logger = get_logger()


class BackupEngine:
    def __init__(self, config: dict):
        self.name = config["name"]
        self.watch_folder = Path(config["watch_folder"])
        self.backup_destination = Path(config["backup_destination"])
        self.max_versions = config.get("max_versions", 5)

    def _backup_path(self, source: Path) -> Path:
        try:
            rel = source.relative_to(self.watch_folder)
        except ValueError:
            rel = Path(source.name)
        return self.backup_destination / rel

    def backup_file(self, source_path):
        src = Path(source_path)
        if not src.is_file():
            return

        dst = self._backup_path(src)

        if dst.exists():
            if get_file_hash(src) == get_file_hash(dst):
                logger.debug(f"Skipped (unchanged): {src.name}")
                return
            self._rotate_versions(dst)

        try:
            ensure_dir(dst.parent)
            shutil.copy2(src, dst)
            logger.debug(f"Backed up: {src} → {dst}")
        except Exception as e:
            logger.error(f"Failed to back up {src}: {e}")

    def delete_backup(self, source_path):
        src = Path(source_path)
        dst = self._backup_path(src)
        if dst.exists():
            try:
                dst.unlink()
                logger.debug(f"Deleted from backup: {dst}")
            except Exception as e:
                logger.error(f"Failed to delete backup {dst}: {e}")

    def move_backup(self, src_path, dest_path):
        backup_src = self._backup_path(Path(src_path))
        backup_dst = self._backup_path(Path(dest_path))
        if backup_src.exists():
            try:
                ensure_dir(backup_dst.parent)
                backup_src.rename(backup_dst)
                logger.debug(f"Moved in backup: {backup_src.name} → {backup_dst.name}")
            except Exception as e:
                logger.error(f"Failed to move backup {backup_src}: {e}")

    def full_sync(self, announce: bool = False) -> int:
        ensure_dir(self.backup_destination)

        if not self.watch_folder.exists():
            logger.error(f"Watch folder does not exist: {self.watch_folder}")
            return 0

        files = [f for f in self.watch_folder.rglob("*") if f.is_file()]
        total = len(files)

        for src_file in files:
            self.backup_file(src_file)

        logger.debug(f"[{self.name}] Full sync complete — {total} file(s) processed.")

        if announce:
            print(f"\n  ✓ '{self.name}' — initial backup complete ({total} file(s) backed up).")

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
