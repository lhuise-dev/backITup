import threading
import time

import schedule as _schedule

from core.logger import get_logger

logger = get_logger()


def start_scheduler(config: dict, engine, stop_event: threading.Event) -> threading.Thread:
    interval = config.get("interval_minutes", 30)
    name = config["name"]

    def run():
        sched = _schedule.Scheduler()
        sched.every(interval).minutes.do(engine.full_sync)

        # Initial sync — prints one summary line when done, then stays silent
        engine.full_sync(announce=True)

        while not stop_event.is_set():
            sched.run_pending()
            time.sleep(1)

    thread = threading.Thread(target=run, daemon=True, name=f"scheduler-{name}")
    thread.start()
    logger.info(f"Scheduler started for '{name}' — every {interval} minute(s).")
    return thread


def stop_scheduler(stop_event: threading.Event):
    stop_event.set()
