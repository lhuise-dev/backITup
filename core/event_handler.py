from watchdog.events import FileSystemEventHandler

from core.logger import get_logger

logger = get_logger()


class BackupEventHandler(FileSystemEventHandler):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def on_created(self, event):
        if not event.is_directory:
            logger.debug(f"CREATE detected: {event.src_path}")
            self.engine.backup_file(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            logger.debug(f"MODIFY detected: {event.src_path}")
            self.engine.backup_file(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            logger.debug(f"DELETE detected: {event.src_path}")
            self.engine.delete_backup(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            logger.debug(f"MOVE detected: {event.src_path} → {event.dest_path}")
            self.engine.move_backup(event.src_path, event.dest_path)
