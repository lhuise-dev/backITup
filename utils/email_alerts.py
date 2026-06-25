import json
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from core.logger import get_logger

logger = get_logger()

_CONFIG_FILE = Path(__file__).parent.parent / "data" / "email_config.json"

_DEFAULT_CONFIG = {
    "smtp_host": "",
    "smtp_port": 587,
    "sender_email": "",
    "sender_password": "",
    "recipient_email": "",
    "enabled": False,
}


# ── config persistence ──────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        if not _CONFIG_FILE.exists():
            return dict(_DEFAULT_CONFIG)
        data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(_DEFAULT_CONFIG)
        merged.update(data or {})
        return merged
    except Exception as e:
        logger.error(f"Failed to load email config: {e}")
        return dict(_DEFAULT_CONFIG)


def _save_config(config: dict) -> None:
    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save email config: {e}")


def configure_email(smtp_host, smtp_port, sender_email, sender_password, recipient_email) -> None:
    """Persist SMTP settings and enable email alerts."""
    try:
        config = {
            "smtp_host": str(smtp_host).strip(),
            "smtp_port": int(smtp_port),
            "sender_email": str(sender_email).strip(),
            "sender_password": str(sender_password),
            "recipient_email": str(recipient_email).strip(),
            "enabled": True,
        }
        _save_config(config)
        logger.info("Email alerts configured and enabled.")
    except Exception as e:
        logger.error(f"Failed to configure email: {e}")


# ── HTML templating ─────────────────────────────────────────────────────────────

def _wrap_html(title: str, inner_html: str) -> str:
    """Wrap content in the shared backITup email shell (inline CSS only)."""
    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0; padding:0; background:#f1f5f9; font-family:Arial,Helvetica,sans-serif;">
    <div style="max-width:600px; margin:24px auto; background:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 1px 4px rgba(0,0,0,0.1);">
      <div style="background:#1e1e2e; color:#ffffff; padding:18px 24px; font-size:20px; font-weight:bold;">
        backITup
      </div>
      <div style="padding:24px; color:#1f2937;">
        <h2 style="margin-top:0; font-size:18px;">{title}</h2>
        {inner_html}
      </div>
      <div style="padding:16px 24px; font-size:11px; color:#9ca3af; border-top:1px solid #e5e7eb;">
        Sent by backITup Automated Backup System
      </div>
    </div>
  </body>
</html>"""


def _table(rows: list, status_label: str, status_color: str) -> str:
    """Build a 2-column detail table with a colored status row."""
    cells = ""
    for i, (label, value) in enumerate(rows):
        bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
        cells += (
            f'<tr style="background:{bg};">'
            f'<td style="padding:10px 12px; font-weight:bold; width:40%; border:1px solid #e5e7eb;">{label}</td>'
            f'<td style="padding:10px 12px; border:1px solid #e5e7eb;">{value}</td>'
            f"</tr>"
        )
    cells += (
        '<tr>'
        '<td style="padding:10px 12px; font-weight:bold; border:1px solid #e5e7eb;">Status</td>'
        f'<td style="padding:10px 12px; border:1px solid #e5e7eb;">'
        f'<span style="display:inline-block; padding:4px 12px; border-radius:4px; '
        f'background:{status_color}; color:#ffffff; font-weight:bold;">{status_label}</span>'
        "</td></tr>"
    )
    return f'<table style="width:100%; border-collapse:collapse; font-size:14px;">{cells}</table>'


# ── sending ──────────────────────────────────────────────────────────────────────

def send_alert(subject: str, body_html: str) -> bool:
    """
    Send an HTML email using the stored config via STARTTLS.
    Returns True on success, False on any failure. Never raises.
    """
    config = _load_config()

    if not config.get("enabled"):
        logger.warning("send_alert: email alerts are not configured/enabled.")
        return False

    required = ("smtp_host", "sender_email", "sender_password", "recipient_email")
    if not all(config.get(k) for k in required):
        logger.warning("send_alert: email config is incomplete.")
        return False

    try:
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = config["sender_email"]
        message["To"] = config["recipient_email"]
        message.attach(MIMEText(body_html, "html"))

        context = ssl.create_default_context()
        with smtplib.SMTP(config["smtp_host"], int(config["smtp_port"])) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(config["sender_email"], config["sender_password"])
            server.sendmail(
                config["sender_email"],
                [config["recipient_email"]],
                message.as_string(),
            )
        logger.info(f"Email alert sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email alert '{subject}': {e}")
        return False


def send_backup_success(system_name: str, files_count: int, duration_seconds: float) -> bool:
    subject = f"✅ backITup — Backup Complete: {system_name}"
    rows = [
        ("System", system_name),
        ("Files Backed Up", str(files_count)),
        ("Duration", f"{float(duration_seconds):.2f} seconds"),
    ]
    inner = _table(rows, "SUCCESS", "#16a34a")
    return send_alert(subject, _wrap_html("Backup Completed Successfully", inner))


def send_backup_failure(system_name: str, error_message: str) -> bool:
    subject = f"🚨 backITup — Backup FAILED: {system_name}"
    rows = [
        ("System", system_name),
        ("Error", error_message),
        ("Timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    inner = _table(rows, "FAILED", "#dc2626")
    return send_alert(subject, _wrap_html("Backup Failed", inner))


def test_email() -> bool:
    """Send a plain test email and report the result to the console."""
    subject = "backITup — Test Email"
    inner = (
        '<p style="font-size:14px;">This is a test email from backITup. '
        "If you can read this, your email alerts are configured correctly.</p>"
    )
    ok = send_alert(subject, _wrap_html("Test Email", inner))
    if ok:
        print("✅ Test email sent successfully.")
    else:
        print("❌ Test email failed. Check your SMTP settings.")
    return ok
