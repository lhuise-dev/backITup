import os
import subprocess
import sys


def ensure_deps():
    required = ["watchdog", "schedule", "plyer"]
    installed_any = False
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            print(f"Installing {pkg}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg, "--break-system-packages", "--quiet"]
            )
            installed_any = True

    if installed_any:
        print("Dependencies installed. Restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)


def ensure_tkinter():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("Installing tkinter (requires sudo)...")
        try:
            subprocess.check_call(["sudo", "apt-get", "install", "-y", "python3-tk"])
        except Exception as e:
            print(f"Could not install tkinter: {e}")
            print("Falling back to manual path input.")


ensure_deps()
ensure_tkinter()

import queue
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from core.logger import get_logger
from core.config_manager import ConfigManager
from core.backup_engine import BackupEngine
from core.watcher import start_watcher, stop_watcher
from core.scheduler import start_scheduler
from utils.file_utils import delete_path

logger = get_logger()

DAEMON_PID_FILE = BASE_DIR / "data" / "backITup.pid"

# Background threads post notifications here; main loop drains them safely
_notifications: queue.Queue = queue.Queue()

# { name: { "config", "watcher", "scheduler_thread", "stop_event", "engine" } }
running_systems: dict = {}

# Set by SIGUSR1 handler in daemon mode; main loop acts on it
_reload_requested = False


# ── daemon detection ─────────────────────────────────────────────────────────

def is_daemon_running():
    if not DAEMON_PID_FILE.exists():
        return False, None
    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
        os.kill(pid, 0)   # raises if process doesn't exist
        return True, pid
    except Exception:
        try:
            DAEMON_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return False, None


def signal_daemon(daemon_pid: int):
    try:
        os.kill(daemon_pid, signal.SIGUSR1)
    except Exception:
        pass


# ── thread management ─────────────────────────────────────────────────────────

def launch_system(config: dict):
    name = config["name"]
    stop_event = threading.Event()
    engine = BackupEngine(config, notify=_notifications.put)
    observer = start_watcher(config, engine)
    scheduler_thread = start_scheduler(config, engine, stop_event)
    running_systems[name] = {
        "config": config,
        "watcher": observer,
        "scheduler_thread": scheduler_thread,
        "stop_event": stop_event,
        "engine": engine,
    }


def stop_system(name: str):
    if name in running_systems:
        entry = running_systems.pop(name)
        entry["stop_event"].set()
        stop_watcher(entry["watcher"])


def reload_systems():
    """Diff running systems against saved config; start/stop/restart as needed."""
    configs = ConfigManager().load_all()
    config_map = {c["name"]: c for c in configs}
    running_names = set(running_systems.keys())
    config_names = set(config_map.keys())

    for name in running_names - config_names:
        stop_system(name)
        logger.info(f"[daemon] Stopped removed system: {name}")

    for name in config_names - running_names:
        try:
            launch_system(config_map[name])
            logger.info(f"[daemon] Started new system: {name}")
        except Exception as e:
            logger.error(f"[daemon] Failed to start '{name}': {e}")

    for name in config_names & running_names:
        if config_map[name] != running_systems[name].get("config"):
            stop_system(name)
            try:
                launch_system(config_map[name])
                logger.info(f"[daemon] Restarted changed system: {name}")
            except Exception as e:
                logger.error(f"[daemon] Failed to restart '{name}': {e}")


def resume_all_systems():
    configs = ConfigManager().load_all()
    if not configs:
        return
    print(f"Resuming {len(configs)} backup system(s) in the background...")
    for config in configs:
        try:
            launch_system(config)
            logger.info(f"Resumed: {config['name']}")
        except Exception as e:
            logger.error(f"Failed to resume '{config['name']}': {e}")


# ── daemonize ─────────────────────────────────────────────────────────────────

def run_in_background():
    """Fork into a proper Unix daemon; parent exits, child keeps backup running."""
    pid = os.fork()
    if pid > 0:
        # Parent: tell user and exit
        print(f"\nbackITup is now running in the background (PID: {pid}).")
        print("Run 'python3 backITup.py' again to open the menu.")
        sys.exit(0)

    # Child: detach from terminal
    os.setsid()

    # Second fork so daemon is not a session leader (can't re-acquire terminal)
    pid2 = os.fork()
    if pid2 > 0:
        sys.exit(0)

    # Grandchild = the actual daemon
    DAEMON_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    DAEMON_PID_FILE.write_text(str(os.getpid()))

    # Detach from terminal I/O
    devnull_r = open(os.devnull, "r")
    devnull_w = open(os.devnull, "w")
    os.dup2(devnull_r.fileno(), 0)
    os.dup2(devnull_w.fileno(), 1)
    os.dup2(devnull_w.fileno(), 2)

    # Threads are NOT inherited across fork — re-launch everything
    running_systems.clear()
    for config in ConfigManager().load_all():
        try:
            launch_system(config)
        except Exception as e:
            logger.error(f"[daemon] Failed to launch '{config['name']}': {e}")

    # SIGUSR1 → schedule a reload (safe: avoid doing heavy work inside signal handler)
    def _sigusr1(sig, frame):
        global _reload_requested
        _reload_requested = True

    signal.signal(signal.SIGUSR1, _sigusr1)

    # SIGTERM → clean shutdown
    def _sigterm(sig, frame):
        for name in list(running_systems.keys()):
            stop_system(name)
        try:
            DAEMON_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm)

    # Keep main thread alive; check for reload requests
    global _reload_requested
    while True:
        if _reload_requested:
            _reload_requested = False
            reload_systems()
        time.sleep(1)


# ── UI helpers ────────────────────────────────────────────────────────────────

def pick_folder(title: str) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title=title)
        root.destroy()
        if path:
            return path
    except Exception:
        pass
    return input(f"{title}\nEnter folder path manually: ").strip()


def _drain_notifications():
    while True:
        try:
            print(_notifications.get_nowait())
        except queue.Empty:
            break


_LOGO = """
██████╗  █████╗  ██████╗██╗  ██╗  ██╗████████╗██╗   ██╗██████╗
██╔══██╗██╔══██╗██╔════╝██║ ██╔╝  ██║╚══██╔══╝██║   ██║██╔══██╗
██████╔╝███████║██║     █████╔╝   ██║   ██║   ██║   ██║██████╔╝
██╔══██╗██╔══██║██║     ██╔═██╗   ██║   ██║   ██║   ██║██╔═══╝
██████╔╝██║  ██║╚██████╗██║  ██╗  ██║   ██║   ╚██████╔╝██║
╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝
"""


def print_menu(connected: bool):
    print(_LOGO)
    print("  Automated File Backup System")
    if connected:
        print("  [connected to background daemon]")
    print("\nHello! What do you want to do today?\n")
    print("[1] Create a new backup system")
    print("[2] Configure a current backup system")
    print("[3] Delete a backup system")
    if connected:
        print("[4] Exit  (daemon keeps running in background)\n")
    else:
        print("[4] Run in background")
        print("[5] Exit\n")


# ── menu actions ──────────────────────────────────────────────────────────────

def create_backup_system(daemon_pid=None):
    manager = ConfigManager()

    name = input("Enter a name for this backup system: ").strip()
    if not name:
        print("Name cannot be empty.")
        return

    if any(c["name"] == name for c in manager.load_all()):
        print(f"A backup system named '{name}' already exists.")
        return

    print("\nSelect the folder to WATCH (source):")
    watch_folder = pick_folder("Select the folder to WATCH (source)")
    if not watch_folder:
        print("No folder selected. Cancelled.")
        return

    print("\nSelect the folder where BACKUPS will be stored (destination):")
    backup_destination = pick_folder("Select BACKUP DESTINATION folder")
    if not backup_destination:
        print("No folder selected. Cancelled.")
        return

    try:
        raw = input("Backup interval in minutes (default: 30): ").strip()
        interval_minutes = int(raw) if raw else 30
    except ValueError:
        interval_minutes = 30

    try:
        raw = input("Max versions to keep per file (default: 5): ").strip()
        max_versions = int(raw) if raw else 5
    except ValueError:
        max_versions = 5

    config = {
        "name": name,
        "watch_folder": str(Path(watch_folder).expanduser().resolve()),
        "backup_destination": str(Path(backup_destination).expanduser().resolve()),
        "interval_minutes": interval_minutes,
        "max_versions": max_versions,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    manager.add(config)

    if daemon_pid:
        signal_daemon(daemon_pid)
        print(f"\nBackup system '{name}' created. The background daemon is picking it up.")
    else:
        launch_system(config)
        logger.info(f"Created and launched backup system: {name}")
        print(f"\nBackup system '{name}' is now running in the background.")


def configure_backup_system(daemon_pid=None):
    manager = ConfigManager()
    configs = manager.load_all()

    if not configs:
        print("No backup systems found.")
        return

    print("\nAvailable backup systems:")
    for i, c in enumerate(configs, 1):
        print(f"  [{i}] {c['name']}  |  Watch: {c['watch_folder']}")

    try:
        choice = int(input("\nSelect a backup system to configure: ").strip())
        if not 1 <= choice <= len(configs):
            print("Invalid selection.")
            return
    except ValueError:
        print("Invalid input.")
        return

    config = configs[choice - 1]
    name = config["name"]

    print(f"\nConfiguring: {name}")
    print(f"  Watched folder (read-only): {config['watch_folder']}")
    print(f"  Current destination : {config['backup_destination']}")
    print(f"  Current interval    : {config['interval_minutes']} minutes")
    print(f"  Current max versions: {config['max_versions']}")
    print("\nWhat would you like to change?")
    print("  [1] Backup destination folder")
    print("  [2] Backup interval (minutes)")
    print("  [3] Max versions to keep")
    print("  [0] Cancel")

    sub = input("\nChoice: ").strip()

    if sub == "0":
        return
    elif sub == "1":
        new_dest = pick_folder("Select new BACKUP DESTINATION folder")
        if not new_dest:
            print("No folder selected. Cancelled.")
            return
        config["backup_destination"] = str(Path(new_dest).expanduser().resolve())
    elif sub == "2":
        try:
            config["interval_minutes"] = int(input("New interval in minutes: ").strip())
        except ValueError:
            print("Invalid input.")
            return
    elif sub == "3":
        try:
            config["max_versions"] = int(input("New max versions: ").strip())
        except ValueError:
            print("Invalid input.")
            return
    else:
        print("Invalid choice.")
        return

    manager.update(name, config)

    if daemon_pid:
        signal_daemon(daemon_pid)
        print(f"\nBackup system '{name}' updated. The background daemon is applying changes.")
    else:
        stop_system(name)
        launch_system(config)
        logger.info(f"Reconfigured and restarted backup system: {name}")
        print(f"\nBackup system '{name}' has been updated and restarted.")


def delete_backup_system(daemon_pid=None):
    manager = ConfigManager()
    configs = manager.load_all()

    if not configs:
        print("No backup systems found.")
        return

    print("\nAvailable backup systems:")
    for i, c in enumerate(configs, 1):
        print(f"  [{i}] {c['name']}  |  {c['watch_folder']} → {c['backup_destination']}")
    print("  [0] Cancel")

    try:
        choice = int(input("\nSelect a backup system to delete: ").strip())
        if choice == 0:
            return
        if not 1 <= choice <= len(configs):
            print("Invalid selection.")
            return
    except ValueError:
        print("Invalid input.")
        return

    config = configs[choice - 1]
    name = config["name"]

    confirm = input(f"Are you sure you want to delete '{name}'? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    manager.remove(name)

    if daemon_pid:
        signal_daemon(daemon_pid)
    else:
        stop_system(name)

    also_delete = input("Do you also want to delete the backed-up files? (y/n): ").strip().lower()
    if also_delete == "y":
        delete_path(Path(config["backup_destination"]))
        print(f"Backup files deleted from: {config['backup_destination']}")

    logger.info(f"Deleted backup system: {name}")
    print(f"\nBackup system '{name}' has been deleted.")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    daemon_running, daemon_pid = is_daemon_running()

    if daemon_running:
        print(f"backITup daemon is already running in the background (PID: {daemon_pid}).")
    else:
        resume_all_systems()

    while True:
        _drain_notifications()
        print_menu(connected=daemon_running)
        choice = input("Enter your choice: ").strip()
        _drain_notifications()

        if choice == "1":
            create_backup_system(daemon_pid if daemon_running else None)
        elif choice == "2":
            configure_backup_system(daemon_pid if daemon_running else None)
        elif choice == "3":
            delete_backup_system(daemon_pid if daemon_running else None)
        elif choice == "4" and not daemon_running:
            run_in_background()
        elif (choice == "4" and daemon_running) or (choice == "5" and not daemon_running):
            if daemon_running:
                print("Menu closed. Backup systems continue running in the background.")
            else:
                print("Goodbye! Backup systems will stop when this process exits.")
            sys.exit(0)
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()
