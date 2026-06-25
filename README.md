# backITup

**Automated Backup Management System**

A Python-based backup daemon for Ubuntu Linux that watches folders and mirrors
them in real time, with at-rest encryption, role-based authentication, a
real-time web dashboard, email alerts, and recovery (RTO/RPO) testing. It runs
with a single command and auto-installs its own dependencies on first launch.

> Developed as a learning project for **INTE403 – Systems Administration and
> Maintenance**, Polytechnic University of the Philippines – Taguig Campus.

---

## Features

- **Continuous backup daemon** — a `watchdog`-based watcher mirrors file changes
  the moment they happen, backed by an interval scheduler for guaranteed
  convergence.
- **Encryption at rest** — optional per-system AES/Fernet encryption with keys
  derived via PBKDF2HMAC (SHA256, 480,000 iterations, per-system salt).
- **Authentication & roles** — `bcrypt`-hashed logins with `admin` and `user`
  roles; first launch forces creation of an admin account.
- **Web dashboard** — Flask + Socket.IO dashboard with a live activity feed,
  job history, a benchmarks tab, and user management.
- **Email alerts** — SMTP success/failure notifications over STARTTLS
  (fail-silent when unconfigured).
- **Performance & recovery** — backup benchmarking, restoration testing
  (decrypt + SHA256 verification), and RTO/RPO rating.
- **Version rotation** — keeps a configurable number of previous versions per
  file.

---

## Requirements

| Component | Requirement |
|---|---|
| Operating System | Ubuntu 22.04 LTS or later (Linux only) |
| Python | 3.8 or higher |
| Disk Space | 100 MB minimum (plus backup storage) |
| Privileges | `sudo` only for installing `python3-tk` (folder picker) |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/lhuise-dev/backITup.git
cd backITup

# 2. Run it — dependencies auto-install on first launch
python backITup.py
```

On first run, backITup installs `watchdog`, `schedule`, `plyer`, `cryptography`,
`bcrypt`, `flask`, `flask-socketio`, and `flask-login`, then restarts and prompts
you to create the first administrator account.

To install dependencies manually instead:

```bash
pip install -r requirements.txt
```

If the graphical folder picker is unavailable, install Tkinter (the app will
prompt you) and it otherwise falls back to manual path entry:

```bash
sudo apt-get install -y python3-tk
```

---

## Usage

### Command-line menu

Run `python backITup.py` and log in. Administrators see the full menu:

| Option | Action |
|---|---|
| 1 | Create a new backup system |
| 2 | Configure a backup system / email alerts |
| 3 | Delete a backup system |
| 4 | View backup status |
| 5 | Trigger a manual backup |
| 6 | Manage users *(admin)* |
| 7 | Run in background |
| 8 | Launch Web Dashboard *(admin)* |
| 9 | Performance & Recovery *(admin)* |

Standard users are limited to viewing status and triggering manual backups.

### Web dashboard

Choose **Launch Web Dashboard** (admin), then open
[http://localhost:5000](http://localhost:5000) and log in with the same
credentials as the CLI. The dashboard shows backup system cards, a live activity
feed, job history with a benchmarks tab, and admin-only user management.

### Email alerts

From **Configure → Email alerts**, enter your SMTP host, port (default 587),
sender address, app password, and recipient. A test email is sent immediately.
For Gmail, generate an **App Password** under *Google Account → Security →
2-Step Verification → App Passwords* — your normal password will not work.

---

## Project structure

```
backITup/
├── backITup.py              # CLI entry point: auth, menu, daemon control
├── core/
│   ├── config_manager.py    # backup_systems.json read/write
│   ├── backup_engine.py     # copy/encrypt, version rotation, full sync
│   ├── watcher.py           # watchdog filesystem listener
│   ├── event_handler.py     # maps fs events to engine actions
│   ├── scheduler.py         # interval-based full sync
│   ├── logger.py            # unified console + file logging
│   ├── encryption.py        # AES/Fernet + PBKDF2HMAC
│   ├── auth.py              # bcrypt login + roles
│   └── benchmark.py         # benchmarking, restore tests, RTO/RPO
├── dashboard/
│   ├── app.py               # Flask app factory + LoginManager
│   ├── routes.py            # dashboard routes
│   ├── socket_events.py     # Socket.IO broadcasting
│   └── templates/           # base, login, index, jobs, users
├── utils/
│   ├── file_utils.py        # SHA256 hashing, path helpers
│   ├── notify.py            # desktop notifications
│   └── email_alerts.py      # SMTP alerts
├── data/                    # runtime config & keys (gitignored)
└── logs/                    # application log & reports (gitignored)
```

---

## Documentation

Full technical documentation is generated with `python-docx`:

```bash
pip install python-docx
python generate_docs.py
```

This produces `INTE403_VALILA_LHUISEGAHBRIELLE_BACKITUP_DOCUMENTATION.docx`
covering architecture, installation, the user manual, security, performance and
recovery, and the email system.

---

## Security notes

Runtime secrets and state are **not** committed to the repository (see
`.gitignore`): the Flask session key (`data/dashboard_secret.key`), the user
store (`data/users.json`), per-system encryption keys (`data/*.key`), email
settings, and logs are all generated locally on first use. Keep your
`data/*.key` files safe — without a system's key, its encrypted backups cannot
be decrypted.

The bundled dashboard runs on the Werkzeug development server and is **not**
hardened for production exposure; place it behind a proper WSGI server and
reverse proxy before any untrusted network use.

---

## License

Released under the [MIT License](LICENSE).

## Author

**Lhuise Gahbrielle Valila** — Section DIT 2-1, PUP Taguig.
