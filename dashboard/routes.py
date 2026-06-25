"""All HTTP routes for the dashboard. Everything except /login and /logout
requires authentication; admin-only routes return 403 for normal users."""

import re
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from core import auth
from core.backup_engine import BackupEngine
from core.config_manager import ConfigManager
from core.logger import get_logger
from dashboard.app import DashboardUser

logger = get_logger()

bp = Blueprint("routes", __name__)

_LOG_FILE = Path(__file__).parent.parent / "logs" / "backITup.log"

# [2026-06-25 12:00:00] LEVEL — message
_LOG_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+\w+\s+—\s+(?P<msg>.*)$")
_SYNC_OK_RE = re.compile(
    r"\[(?P<sys>[^\]]+)\]\s+Full sync complete\s+—\s+(?P<files>\d+)\s+file\(s\)(?:\s+in\s+(?P<dur>[\d.]+)s)?"
)
_FAIL_RE = re.compile(r"\[(?P<sys>[^\]]+)\]\s+Backup FAILED\s+—\s+(?P<err>.+)")


# ── helpers ──────────────────────────────────────────────────────────────────────

def admin_required(view):
    """Return 403 HTML for non-admin users on admin-only routes."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            return (
                "<h1 style='font-family:sans-serif;color:#dc2626;'>403 — Forbidden</h1>"
                "<p style='font-family:sans-serif;'>You need administrator privileges "
                "to access this page.</p>",
                403,
            )
        return view(*args, **kwargs)

    return wrapped


def _read_log_lines() -> list:
    try:
        if not _LOG_FILE.exists():
            return []
        return _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        logger.error(f"Failed to read log file: {e}")
        return []


def _last_backup_times() -> dict:
    """Map system name -> most recent 'Full sync complete' timestamp."""
    times = {}
    for line in _read_log_lines():
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        ok = _SYNC_OK_RE.search(m.group("msg"))
        if ok:
            times[ok.group("sys")] = m.group("ts")
    return times


def _parse_jobs(limit: int = 100) -> list:
    """Extract job rows (newest first) from the log for jobs.html."""
    rows = []
    for line in _read_log_lines():
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        ts, msg = m.group("ts"), m.group("msg")

        ok = _SYNC_OK_RE.search(msg)
        if ok:
            rows.append({
                "timestamp": ts,
                "system": ok.group("sys"),
                "files": ok.group("files"),
                "duration": f"{ok.group('dur')}s" if ok.group("dur") else "-",
                "status": "Success",
            })
            continue

        fail = _FAIL_RE.search(msg)
        if fail:
            rows.append({
                "timestamp": ts,
                "system": fail.group("sys"),
                "files": "-",
                "duration": "-",
                "status": "Failed",
            })

    rows.reverse()  # newest first
    return rows[:limit]


def _systems_view() -> list:
    """Backup systems enriched with last-backup time and running status."""
    try:
        configs = ConfigManager().load_all()
    except Exception as e:
        logger.error(f"Failed to load systems for dashboard: {e}")
        configs = []

    last_times = _last_backup_times()
    view = []
    for c in configs:
        name = c.get("name", "?")
        view.append({
            "name": name,
            "source": c.get("watch_folder", ""),
            "destination": c.get("backup_destination", ""),
            "encryption_enabled": bool(c.get("encryption_enabled")),
            "last_backup": last_times.get(name, "Never"),
            "status": "Stopped",  # CLI/daemon owns live state; default shown here
        })
    return view


# ── auth routes ──────────────────────────────────────────────────────────────────

@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("routes.index"))

    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        try:
            if auth.login(username, password):
                login_user(DashboardUser(username))
                logger.info(f"[dashboard] '{username}' logged in.")
                return redirect(url_for("routes.index"))
            error = "Invalid username or password."
        except Exception as e:
            logger.error(f"[dashboard] login error: {e}")
            error = "Login failed. Please try again."

    return render_template("login.html", error=error)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("routes.login"))


# ── main pages ────────────────────────────────────────────────────────────────────

@bp.route("/")
@login_required
def index():
    return render_template("index.html", systems=_systems_view())


@bp.route("/backup-now/<system_name>", methods=["POST"])
@admin_required
def backup_now(system_name):
    try:
        config = ConfigManager().get(system_name)
        if not config:
            flash(f"System '{system_name}' not found.", "error")
            return redirect(url_for("routes.index"))
        engine = BackupEngine(config)
        engine.full_sync(announce=False)
        flash(f"Manual backup triggered for '{system_name}'.", "success")
    except Exception as e:
        logger.error(f"[dashboard] manual backup failed for '{system_name}': {e}")
        flash(f"Backup failed: {e}", "error")
    return redirect(url_for("routes.index"))


def _load_benchmarks() -> list:
    try:
        from core.benchmark import _load_benchmarks as lb
        return lb()
    except Exception as e:
        logger.error(f"Failed to load benchmarks: {e}")
        return []


def _benchmark_ratings(benchmarks: list) -> dict:
    """Map each system that has restore-test data to its RTO/RPO rating."""
    ratings = {}
    try:
        from core.benchmark import calculate_rto_rpo
        systems = {
            b.get("system") for b in benchmarks
            if b.get("type") == "restore_test" and b.get("system")
        }
        for sys_name in systems:
            ratings[sys_name] = calculate_rto_rpo(sys_name).get("rating", "Insufficient Data")
    except Exception as e:
        logger.error(f"Failed to compute benchmark ratings: {e}")
    return ratings


@bp.route("/jobs")
@login_required
def jobs():
    benchmarks = _load_benchmarks()
    return render_template(
        "jobs.html",
        jobs=_parse_jobs(100),
        benchmarks=benchmarks,
        ratings=_benchmark_ratings(benchmarks),
    )


# ── user management (admin only) ──────────────────────────────────────────────────

@bp.route("/users")
@admin_required
def users():
    return render_template("users.html", users=auth.list_users())


@bp.route("/users/add", methods=["POST"])
@admin_required
def users_add():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    role = (request.form.get("role") or auth.ROLE_USER).strip().lower()
    if role not in auth.VALID_ROLES:
        role = auth.ROLE_USER
    if auth.register_user(username, password, role):
        flash(f"User '{username}' created.", "success")
    else:
        flash("Could not create user (duplicate or invalid input).", "error")
    return redirect(url_for("routes.users"))


@bp.route("/users/delete/<username>", methods=["POST"])
@admin_required
def users_delete(username):
    if username == current_user.id:
        flash("You cannot delete the account you are logged in with.", "error")
        return redirect(url_for("routes.users"))
    if auth.delete_user(username):
        flash(f"User '{username}' deleted.", "success")
    else:
        flash("Could not delete user.", "error")
    return redirect(url_for("routes.users"))
