import subprocess
import sys


def ensure_deps():
    required = ["watchdog", "schedule", "plyer"]
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            print(f"Installing {pkg}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg, "--break-system-packages", "--quiet"]
            )


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

import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from core.logger import get_logger
from core.config_manager import ConfigManager
from core.backup_engine import BackupEngine
from core.watcher import start_watcher, stop_watcher
from core.scheduler import start_scheduler, stop_scheduler
from utils.file_utils import delete_path

logger = get_logger()

# Global registry: { name: { "watcher": observer, "scheduler_thread": thread, "stop_event": event } }
running_systems: dict = {}


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


def print_menu():
    print("\n╔══════════════════════════════╗")
    print("║         backITup             ║")
    print("╚══════════════════════════════╝")
    print("\nHello! What do you want to do today?\n")
    print("[1] Create a new backup system")
    print("[2] Configure a current backup system")
    print("[3] Delete a backup system")
    print("[4] Exit\n")


def launch_system(config: dict):
    name = config["name"]
    stop_event = threading.Event()
    engine = BackupEngine(config)
    observer = start_watcher(config, engine)
    scheduler_thread = start_scheduler(config, engine, stop_event)
    running_systems[name] = {
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


def resume_all_systems():
    configs = ConfigManager().load_all()
    if not configs:
        return
    print(f"Resuming {len(configs)} backup system(s) in the background...")
    for config in configs:
        try:
            launch_system(config)
            logger.info(f"Resumed backup system: {config['name']}")
        except Exception as e:
            logger.error(f"Failed to resume '{config['name']}': {e}")


def create_backup_system():
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
    launch_system(config)
    logger.info(f"Created and launched backup system: {name}")
    print(f"\nBackup system '{name}' is now running in the background.")


def configure_backup_system():
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
    stop_system(name)
    launch_system(config)
    logger.info(f"Reconfigured and restarted backup system: {name}")
    print(f"\nBackup system '{name}' has been updated and restarted.")


def delete_backup_system():
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

    stop_system(name)
    manager.remove(name)

    also_delete = input("Do you also want to delete the backed-up files? (y/n): ").strip().lower()
    if also_delete == "y":
        delete_path(Path(config["backup_destination"]))
        print(f"Backup files deleted from: {config['backup_destination']}")

    logger.info(f"Deleted backup system: {name}")
    print(f"\nBackup system '{name}' has been deleted.")


def main():
    resume_all_systems()

    while True:
        print_menu()
        choice = input("Enter your choice: ").strip()

        if choice == "1":
            create_backup_system()
        elif choice == "2":
            configure_backup_system()
        elif choice == "3":
            delete_backup_system()
        elif choice == "4":
            print("Goodbye! Backup systems will stop when this process exits.")
            sys.exit(0)
        else:
            print("Invalid choice. Please enter 1, 2, 3, or 4.")


if __name__ == "__main__":
    main()
