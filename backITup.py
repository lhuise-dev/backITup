import os
import subprocess
import sys


def ensure_deps():
    # pip install name -> importable module name (they differ for the flask extras,
    # so we must check the module name or we'd reinstall on every launch).
    required = {
        "watchdog": "watchdog",
        "schedule": "schedule",
        "plyer": "plyer",
        "cryptography": "cryptography",
        "bcrypt": "bcrypt",
        "flask": "flask",
        "flask-socketio": "flask_socketio",
        "flask-login": "flask_login",
    }
    installed_any = False
    for pip_name, import_name in required.items():
        try:
            __import__(import_name)
        except ImportError:
            print(f"Installing {pip_name}...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pip_name, "--break-system-packages", "--quiet"]
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

import getpass
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
from core import auth
from core import encryption

try:
    from core import benchmark as bm
except ImportError:
    bm = None
from utils.file_utils import delete_path

logger = get_logger()

# Set after a successful login; gates which menu actions are available.
current_user: str | None = None
current_role: str | None = None

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


def pause():
    input("\nPress Enter to go back to the menu...")


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


def print_menu(connected: bool, role: str):
    os.system("clear")
    print(_LOGO)
    print("  Automated File Backup System")
    if connected:
        print("  [connected to background daemon]")
    print(f"  Logged in as: {current_user} ({role})")

    if role == auth.ROLE_ADMIN:
        print("\nHello! What do you want to do today?\n")
        print("[1] Create a new backup system")
        print("[2] Configure a current backup system")
        print("[3] Delete a backup system")
        print("[4] Stop all backup systems")
        print("[5] View backup status")
        print("[6] Trigger a manual backup")
        print("[7] Manage users")
        print("[8] Launch Web Dashboard")
        print("[9] Performance & Recovery")
        if connected:
            print("[10] Logout")
            print("[11] Exit  (daemon keeps running in background)\n")
        else:
            print("[10] Logout")
            print("[11] Run in background")
            print("[12] Exit\n")
    else:
        print("\nHello! What do you want to do today?\n")
        print("[1] View backup status")
        print("[2] Trigger a manual backup")
        print("[3] Logout")
        print("[4] Exit\n")


# ── auth actions ────────────────────────────────────────────────────────────────

def first_run_setup():
    """No users yet → force creation of an admin account before anything else."""
    os.system("clear")
    print(_LOGO)
    print("  First-time setup — create the administrator account.\n")
    while True:
        username = input("Choose an admin username: ").strip()
        if not username:
            print("Username cannot be empty.\n")
            continue
        password = getpass.getpass("Choose an admin password: ").strip()
        confirm = getpass.getpass("Confirm password: ").strip()
        if not password:
            print("Password cannot be empty.\n")
            continue
        if password != confirm:
            print("Passwords did not match. Try again.\n")
            continue
        if auth.register_user(username, password, auth.ROLE_ADMIN):
            print(f"\nAdmin account '{username}' created.")
            pause()
            return
        print("Could not create the account. Try again.\n")


def do_login() -> bool:
    """Prompt for credentials; on success set the global user/role. Returns success."""
    global current_user, current_role
    os.system("clear")
    print(_LOGO)
    print("  Please log in to continue.\n")
    for attempt in range(3):
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ").strip()
        if auth.login(username, password):
            current_user = username
            current_role = auth.get_role(username)
            logger.info(f"User '{username}' logged in ({current_role}).")
            return True
        remaining = 2 - attempt
        print(f"Invalid credentials." + (f" {remaining} attempt(s) left.\n" if remaining else "\n"))
    print("Too many failed attempts.")
    pause()
    return False


def manage_users():
    """Admin-only user management: list, add, delete."""
    if current_role != auth.ROLE_ADMIN:
        print("Permission denied — admin only.")
        pause()
        return

    while True:
        os.system("clear")
        print("  User Management\n")
        users = auth.list_users()
        for u in users:
            print(f"  - {u['username']:<20} {u['role']:<6} (created {u['created_at']})")
        print("\n  [1] Add user")
        print("  [2] Delete user")
        print("  [0] Back")
        sub = input("\nChoice: ").strip()

        if sub == "0":
            return
        elif sub == "1":
            username = input("New username: ").strip()
            password = getpass.getpass("New password: ").strip()
            role_raw = input("Role (admin/user) [user]: ").strip().lower()
            role = auth.ROLE_ADMIN if role_raw == "admin" else auth.ROLE_USER
            if auth.register_user(username, password, role):
                print(f"User '{username}' created.")
            else:
                print("Could not create user (duplicate or invalid input).")
            pause()
        elif sub == "2":
            target = input("Username to delete: ").strip()
            if target == current_user:
                print("You cannot delete the account you are logged in with.")
                pause()
                continue
            confirm = input(f"Delete '{target}'? (y/n): ").strip().lower()
            if confirm == "y" and auth.delete_user(target):
                print(f"User '{target}' deleted.")
            else:
                print("Cancelled or user not found.")
            pause()
        else:
            print("Invalid choice.")
            pause()


# ── status / manual backup (available to all roles) ─────────────────────────────

def view_status():
    manager = ConfigManager()
    configs = manager.load_all()
    os.system("clear")
    print("  Backup Status\n")
    if not configs:
        print("  No backup systems configured.")
        pause()
        return
    for c in configs:
        running = "running" if c["name"] in running_systems else "stopped (this process)"
        enc = "on" if c.get("encryption_enabled") else "off"
        print(f"  • {c['name']}")
        print(f"      watch       : {c['watch_folder']}")
        print(f"      destination : {c['backup_destination']}")
        print(f"      interval    : {c['interval_minutes']} min   versions: {c['max_versions']}")
        print(f"      encryption  : {enc}   status: {running}\n")
    pause()


def trigger_manual_backup():
    manager = ConfigManager()
    configs = manager.load_all()
    if not configs:
        print("No backup systems found.")
        pause()
        return

    print("\nAvailable backup systems:")
    for i, c in enumerate(configs, 1):
        print(f"  [{i}] {c['name']}")
    print("  [0] Cancel")

    try:
        choice = int(input("\nSelect a system to back up now: ").strip())
        if choice == 0:
            return
        if not 1 <= choice <= len(configs):
            print("Invalid selection.")
            pause()
            return
    except ValueError:
        print("Invalid input.")
        pause()
        return

    config = configs[choice - 1]
    try:
        engine = BackupEngine(config, notify=_notifications.put)
        total = engine.full_sync(announce=False)
        logger.info(f"Manual backup triggered for '{config['name']}' by {current_user}.")
        print(f"\nManual backup complete for '{config['name']}' — {total} file(s) processed.")
    except Exception as e:
        logger.error(f"Manual backup failed for '{config['name']}': {e}")
        print("Manual backup failed. See logs for details.")
    pause()


# ── menu actions ──────────────────────────────────────────────────────────────

def create_backup_system(daemon_pid=None):
    manager = ConfigManager()

    name = input("Enter a name for this backup system: ").strip()
    if not name:
        print("Name cannot be empty.")
        pause()
        return

    if any(c["name"] == name for c in manager.load_all()):
        print(f"A backup system named '{name}' already exists.")
        pause()
        return

    print("\nSelect the folder to WATCH (source):")
    watch_folder = pick_folder("Select the folder to WATCH (source)")
    if not watch_folder:
        print("No folder selected. Cancelled.")
        pause()
        return

    print("\nSelect the folder where BACKUPS will be stored (destination):")
    backup_destination = pick_folder("Select BACKUP DESTINATION folder")
    if not backup_destination:
        print("No folder selected. Cancelled.")
        pause()
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

    # ── Encryption (Feature 1) — set once at creation time ────────────────────
    encryption_enabled = False
    salt_str = None
    key_path = None
    enable = input("Encrypt backups for this system? (y/n): ").strip().lower()
    if enable == "y":
        try:
            password = getpass.getpass("Set an encryption password: ").strip()
            confirm = getpass.getpass("Confirm encryption password: ").strip()
            if not password:
                print("Empty password — encryption disabled for this system.")
            elif password != confirm:
                print("Passwords did not match — encryption disabled for this system.")
            else:
                salt = encryption.generate_salt()
                key = encryption.generate_key(password, salt)
                # Per-system key file so multiple systems can coexist.
                safe = "".join(c if c.isalnum() else "_" for c in name)
                key_path = str(BASE_DIR / "data" / f"{safe}.key")
                encryption.save_key(key, key_path)
                salt_str = encryption.encode_salt(salt)
                encryption_enabled = True
                print("Encryption enabled. Keep your password safe — it cannot be recovered.")
        except Exception as e:
            logger.error(f"Failed to set up encryption for '{name}': {e}")
            print("Encryption setup failed — continuing without encryption.")

    config = {
        "name": name,
        "watch_folder": str(Path(watch_folder).expanduser().resolve()),
        "backup_destination": str(Path(backup_destination).expanduser().resolve()),
        "interval_minutes": interval_minutes,
        "max_versions": max_versions,
        "encryption_enabled": encryption_enabled,
        "salt": salt_str,
        "key_path": key_path,
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
    pause()


def configure_backup_system(daemon_pid=None):
    manager = ConfigManager()

    print("\nWhat would you like to configure?")
    print("  [1] A backup system")
    print("  [2] Email alerts")
    print("  [0] Cancel")
    top = input("\nChoice: ").strip()
    if top == "0":
        return
    if top == "2":
        configure_email_alerts()
        return
    if top != "1":
        print("Invalid choice.")
        pause()
        return

    configs = manager.load_all()

    if not configs:
        print("No backup systems found.")
        pause()
        return

    print("\nAvailable backup systems:")
    for i, c in enumerate(configs, 1):
        print(f"  [{i}] {c['name']}  |  Watch: {c['watch_folder']}")

    try:
        choice = int(input("\nSelect a backup system to configure: ").strip())
        if not 1 <= choice <= len(configs):
            print("Invalid selection.")
            pause()
            return
    except ValueError:
        print("Invalid input.")
        pause()
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
            pause()
            return
        config["backup_destination"] = str(Path(new_dest).expanduser().resolve())
    elif sub == "2":
        try:
            config["interval_minutes"] = int(input("New interval in minutes: ").strip())
        except ValueError:
            print("Invalid input.")
            pause()
            return
    elif sub == "3":
        try:
            config["max_versions"] = int(input("New max versions: ").strip())
        except ValueError:
            print("Invalid input.")
            pause()
            return
    else:
        print("Invalid choice.")
        pause()
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
    pause()


def delete_backup_system(daemon_pid=None):
    manager = ConfigManager()
    configs = manager.load_all()

    if not configs:
        print("No backup systems found.")
        pause()
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
            pause()
            return
    except ValueError:
        print("Invalid input.")
        pause()
        return

    config = configs[choice - 1]
    name = config["name"]

    confirm = input(f"Are you sure you want to delete '{name}'? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        pause()
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
    pause()


def stop_all_systems(daemon_pid=None):
    confirm = input("Are you sure you want to stop ALL backup systems? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        pause()
        return False

    if daemon_pid:
        try:
            os.kill(daemon_pid, signal.SIGTERM)
            time.sleep(1)
        except Exception as e:
            logger.error(f"Could not stop daemon: {e}")
        try:
            DAEMON_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        print("\nBackground daemon stopped. All backup systems are now inactive.")
    else:
        names = list(running_systems.keys())
        for name in names:
            stop_system(name)
        print(f"\nStopped {len(names)} backup system(s).")

    print("Your backup configurations are saved. Run backITup.py again to resume them.")
    logger.info("All backup systems stopped by user.")
    pause()
    return True


# ── main ──────────────────────────────────────────────────────────────────────

# ── web dashboard (admin only) ──────────────────────────────────────────────────

_dashboard_started = False


def launch_dashboard():
    """Start the Flask + Socket.IO dashboard in a daemon thread (admin only)."""
    global _dashboard_started
    if current_role != auth.ROLE_ADMIN:
        print("Permission denied — admin only.")
        pause()
        return
    if _dashboard_started:
        print("Dashboard is already running at http://localhost:5000")
        pause()
        return

    try:
        from dashboard.app import create_app
        from dashboard.socket_events import get_socketio

        app = create_app()
        socketio = get_socketio()

        def _serve():
            try:
                socketio.run(app, host="0.0.0.0", port=5000,
                             debug=False, use_reloader=False,
                             allow_unsafe_werkzeug=True)
            except TypeError:
                # Older Flask-SocketIO without allow_unsafe_werkzeug.
                socketio.run(app, host="0.0.0.0", port=5000,
                             debug=False, use_reloader=False)
            except Exception as e:
                logger.error(f"Dashboard server crashed: {e}")

        t = threading.Thread(target=_serve, daemon=True, name="dashboard")
        t.start()
        _dashboard_started = True
        logger.info("Web dashboard launched on port 5000.")
        print("Dashboard running at http://localhost:5000")
    except Exception as e:
        logger.error(f"Failed to launch dashboard: {e}")
        print(f"Could not launch dashboard: {e}")
    pause()


# ── email alerts configuration ──────────────────────────────────────────────────

def configure_email_alerts():
    """Prompt for SMTP settings, save them, and send a test email immediately."""
    if current_role != auth.ROLE_ADMIN:
        print("Permission denied — admin only.")
        pause()
        return

    try:
        from utils.email_alerts import configure_email, test_email
    except Exception as e:
        logger.error(f"Email alerts unavailable: {e}")
        print("Email alerts module could not be loaded.")
        pause()
        return

    print("\nConfigure Email Alerts")
    smtp_host = input("SMTP host (e.g. smtp.gmail.com): ").strip()
    port_raw = input("SMTP port (default 587): ").strip()
    try:
        smtp_port = int(port_raw) if port_raw else 587
    except ValueError:
        smtp_port = 587
    sender_email = input("Sender email: ").strip()
    sender_password = getpass.getpass("Sender app password: ").strip()
    recipient_email = input("Recipient email: ").strip()

    try:
        configure_email(smtp_host, smtp_port, sender_email, sender_password, recipient_email)
        if test_email():
            print("✅ Email configured and test sent!")
        else:
            print("❌ Test failed, check your settings.")
    except Exception as e:
        logger.error(f"Failed to configure email alerts: {e}")
        print("❌ Test failed, check your settings.")
    pause()


# ── performance & recovery (admin only) ─────────────────────────────────────────

def _pick_system():
    """Show a numbered list of systems and return the chosen config, or None."""
    configs = ConfigManager().load_all()
    if not configs:
        print("No backup systems found.")
        pause()
        return None
    print("\nWhich backup system?")
    for i, c in enumerate(configs, 1):
        print(f"  [{i}] {c['name']}")
    print("  [0] Cancel")
    try:
        choice = int(input("\nSelect: ").strip())
    except ValueError:
        print("Invalid input.")
        pause()
        return None
    if choice == 0:
        return None
    if not 1 <= choice <= len(configs):
        print("Invalid selection.")
        pause()
        return None
    return configs[choice - 1]


def performance_menu():
    """Sub-menu for benchmarking, restore tests, and RTO/RPO reporting."""
    if current_role != auth.ROLE_ADMIN:
        print("Permission denied — admin only.")
        pause()
        return
    if bm is None:
        print("Benchmark module unavailable.")
        pause()
        return

    while True:
        os.system("clear")
        print("══════════════════════════════")
        print("  Performance & Recovery")
        print("══════════════════════════════")
        print("[1] Run Backup Benchmark")
        print("[2] Run Restore Test")
        print("[3] View RTO/RPO Report")
        print("[4] Generate Full Report")
        print("[0] Back to Main Menu")
        choice = input("\nChoice: ").strip()

        if choice == "0":
            return

        if choice not in ("1", "2", "3", "4"):
            print("Invalid choice.")
            pause()
            continue

        config = _pick_system()
        if not config:
            continue
        name = config["name"]

        try:
            if choice == "1":
                print(f"\nRunning backup benchmark for '{name}'...")
                result = bm.run_backup_benchmark(name)
                _print_benchmark_result(result)
            elif choice == "2":
                print(f"\nRunning restore test for '{name}'...")
                result = bm.run_restore_test(name)
                _print_restore_result(result)
            elif choice == "3":
                result = bm.calculate_rto_rpo(name)
                print(f"\nRTO / RPO Report — {name}")
                print("─" * 40)
                for label, key in (
                    ("System", "system"),
                    ("RTO (seconds)", "rto_seconds"),
                    ("RPO (minutes)", "rpo_minutes"),
                    ("Rating", "rating"),
                    ("Restore tests used", "restore_tests_used"),
                ):
                    print(f"  {label:<22}: {result.get(key)}")
            elif choice == "4":
                report = bm.generate_benchmark_report(name)
                print("\n" + report)
        except Exception as e:
            logger.error(f"Performance action failed for '{name}': {e}")
            print(f"Operation failed: {e}")
        pause()


def _print_benchmark_result(result: dict):
    if result.get("error"):
        print(f"  Error: {result['error']}")
        return
    print("\nBenchmark complete:")
    print(f"  System       : {result['system']}")
    print(f"  Timestamp    : {result['timestamp']}")
    print(f"  Files        : {result['files']}")
    print(f"  Total size   : {result['total_mb']} MB")
    print(f"  Duration     : {result['duration_seconds']} s")
    print(f"  Throughput   : {result['throughput_mbps']} MB/s")


def _print_restore_result(result: dict):
    if result.get("error"):
        print(f"  Error: {result['error']}")
        return
    print("\nRestore test complete:")
    print(f"  System       : {result['system']}")
    print(f"  Files tested : {result['files_tested']}")
    print(f"  Passed       : {result['passed']}")
    print(f"  Failed       : {result['failed']}")
    print(f"  Total time   : {result['total_restore_time_seconds']} s")
    for r in result.get("results", []):
        status = "PASS" if r.get("passed") else "FAIL"
        extra = f"  ({r['error']})" if r.get("error") else ""
        print(f"    [{status}] {r['file']}  —  {r['restore_time_seconds']} s{extra}")


def _admin_dispatch(choice: str, daemon_running: bool, daemon_pid):
    """Handle an admin menu choice. Returns (continue_loop, daemon_running, daemon_pid)."""
    dpid = daemon_pid if daemon_running else None

    if choice == "1":
        create_backup_system(dpid)
    elif choice == "2":
        configure_backup_system(dpid)
    elif choice == "3":
        delete_backup_system(dpid)
    elif choice == "4":
        stopped = stop_all_systems(dpid)
        if stopped and daemon_running:
            daemon_running, daemon_pid = False, None
    elif choice == "5":
        view_status()
    elif choice == "6":
        trigger_manual_backup()
    elif choice == "7":
        manage_users()
    elif choice == "8":
        launch_dashboard()
    elif choice == "9":
        performance_menu()
    elif choice == "10":
        return "logout", daemon_running, daemon_pid
    elif choice == "11" and not daemon_running:
        run_in_background()
    elif (choice == "11" and daemon_running) or (choice == "12" and not daemon_running):
        if daemon_running:
            print("Menu closed. Backup systems continue running in the background.")
        else:
            print("Goodbye! Backup systems will stop when this process exits.")
        sys.exit(0)
    else:
        print("Invalid choice.")
        pause()
    return True, daemon_running, daemon_pid


def _user_dispatch(choice: str, daemon_running: bool):
    """Handle a (limited) user menu choice. Returns "logout", True, or exits."""
    if choice == "1":
        view_status()
    elif choice == "2":
        trigger_manual_backup()
    elif choice == "3":
        return "logout"
    elif choice == "4":
        if daemon_running:
            print("Menu closed. Backup systems continue running in the background.")
        else:
            print("Goodbye! Backup systems will stop when this process exits.")
        sys.exit(0)
    else:
        print("Invalid choice.")
        pause()
    return True


def main():
    global current_user, current_role

    daemon_running, daemon_pid = is_daemon_running()

    if daemon_running:
        print(f"backITup daemon is already running in the background (PID: {daemon_pid}).")
    else:
        resume_all_systems()

    # ── Authentication (Feature 2) ────────────────────────────────────────────
    if not auth.users_exist():
        first_run_setup()

    if not do_login():
        sys.exit(0)

    while True:
        _drain_notifications()
        print_menu(connected=daemon_running, role=current_role)
        choice = input("Enter your choice: ").strip()
        _drain_notifications()

        if current_role == auth.ROLE_ADMIN:
            result, daemon_running, daemon_pid = _admin_dispatch(
                choice, daemon_running, daemon_pid
            )
        else:
            result = _user_dispatch(choice, daemon_running)

        if result == "logout":
            current_user = None
            current_role = None
            logger.info("User logged out.")
            if not do_login():
                sys.exit(0)


if __name__ == "__main__":
    main()
