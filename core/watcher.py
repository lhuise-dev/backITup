from watchdog.observers import Observer

from core.event_handler import BackupEventHandler
from core.logger import get_logger

logger = get_logger()


def start_watcher(config: dict, engine) -> Observer:
    handler = BackupEventHandler(engine)
    observer = Observer()
    observer.schedule(handler, config["watch_folder"], recursive=True)
    observer.daemon = True
    observer.start()
    logger.info(f"Watcher started: {config['watch_folder']}")
    return observer


def stop_watcher(observer: Observer):
    try:
        observer.stop()
        observer.join(timeout=5)
        logger.info("Watcher stopped.")
    except Exception as e:
        logger.error(f"Error stopping watcher: {e}")
