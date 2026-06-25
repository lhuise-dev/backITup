"""
Performance benchmarking, restore testing, and RTO/RPO tracking for backITup.

Uses only the standard library plus existing project modules — no new pip deps.
All results (both "benchmark" and "restore_test" entries) are stored together in
data/benchmarks.json, newest first.
"""

import json
import random
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path

from core.config_manager import ConfigManager
from core.encryption import decrypt_file, load_key
from core.logger import get_logger
from utils.file_utils import get_file_hash

logger = get_logger()

_DATA_DIR = Path(__file__).parent.parent / "data"
_BENCHMARK_FILE = _DATA_DIR / "benchmarks.json"
_LOG_FILE = Path(__file__).parent.parent / "logs" / "backITup.log"


# ── persistence helpers ──────────────────────────────────────────────────────────

def _load_benchmarks() -> list:
    """Read benchmarks.json, returning [] if missing or unparseable."""
    try:
        if not _BENCHMARK_FILE.exists():
            return []
        return json.loads(_BENCHMARK_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"Failed to load benchmarks: {e}")
        return []


def _save_benchmarks(data: list) -> None:
    """Persist the full benchmarks list (newest first)."""
    try:
        _BENCHMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BENCHMARK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save benchmarks: {e}")


def _prepend_entry(entry: dict) -> None:
    """Add a new entry to the front of benchmarks.json (newest first)."""
    data = [entry] + _load_benchmarks()
    _save_benchmarks(data)


# ── FUNCTION 1 — backup benchmark ────────────────────────────────────────────────

def run_backup_benchmark(system_name: str) -> dict:
    """Measure size/throughput of a system's backup destination."""
    config = ConfigManager().get(system_name)
    if not config:
        return {"error": f"System '{system_name}' not found"}

    total_files = 0
    total_bytes = 0
    start_time = time.perf_counter()
    try:
        dest = Path(config["backup_destination"])
        for f in dest.rglob("*"):
            try:
                if f.is_file():
                    total_files += 1
                    total_bytes += f.stat().st_size
            except Exception as e:
                logger.error(f"Benchmark: could not stat {f}: {e}")
    except Exception as e:
        logger.error(f"Benchmark walk failed for '{system_name}': {e}")
    end_time = time.perf_counter()

    duration_seconds = end_time - start_time
    total_mb = total_bytes / (1024 * 1024)
    throughput_mbps = total_mb / duration_seconds if duration_seconds > 0 else 0.0

    result = {
        "type": "benchmark",
        "system": system_name,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": total_files,
        "total_mb": round(total_mb, 3),
        "duration_seconds": round(duration_seconds, 4),
        "throughput_mbps": round(throughput_mbps, 3),
    }

    _prepend_entry(result)
    logger.info(f"[{system_name}] Benchmark — {total_files} file(s), "
                f"{result['total_mb']} MB, {result['throughput_mbps']} MB/s.")
    return result


# ── FUNCTION 2 — restore test ─────────────────────────────────────────────────────

def run_restore_test(system_name: str) -> dict:
    """Restore up to 3 random backup files to a temp dir and verify integrity."""
    config = ConfigManager().get(system_name)
    if not config:
        return {"error": f"System '{system_name}' not found"}

    encryption_enabled = bool(config.get("encryption_enabled"))
    key = None
    if encryption_enabled:
        try:
            key = load_key(config.get("key_path")) if config.get("key_path") else load_key()
        except Exception as e:
            logger.error(f"Restore test: failed to load key for '{system_name}': {e}")
            return {"error": f"Could not load encryption key: {e}"}

    backup_destination = Path(config["backup_destination"])
    watch_folder = Path(config["watch_folder"])

    try:
        all_files = [f for f in backup_destination.rglob("*") if f.is_file()]
    except Exception as e:
        logger.error(f"Restore test: failed to walk backup for '{system_name}': {e}")
        return {"error": f"Could not read backup destination: {e}"}

    if not all_files:
        return {"error": "No files found in backup destination"}

    sample = random.sample(all_files, min(3, len(all_files)))
    tmp = Path(tempfile.mkdtemp())
    results = []

    try:
        for file in sample:
            file_start = time.perf_counter()
            try:
                # Relative path within the backup, and matching source path.
                try:
                    rel = file.relative_to(backup_destination)
                except ValueError:
                    rel = Path(file.name)

                is_encrypted = encryption_enabled and file.suffix == ".enc"
                if is_encrypted:
                    restore_name = file.name[:-4]  # strip ".enc"
                    source_rel = rel.with_name(rel.name[:-4])
                else:
                    restore_name = file.name
                    source_rel = rel

                restore_dest = tmp / restore_name
                source_file = watch_folder / source_rel

                if is_encrypted:
                    written = decrypt_file(file, restore_dest, key)
                else:
                    shutil.copy2(file, restore_dest)
                    written = restore_dest

                restored_hash = get_file_hash(written)
                source_hash = get_file_hash(source_file)
                passed = (
                    restored_hash is not None
                    and source_hash is not None
                    and restored_hash == source_hash
                )

                file_end = time.perf_counter()
                results.append({
                    "file": file.name,
                    "passed": passed,
                    "restore_time_seconds": round(file_end - file_start, 4),
                })
            except Exception as e:
                file_end = time.perf_counter()
                logger.error(f"Restore test failed for {file}: {e}")
                results.append({
                    "file": file.name,
                    "passed": False,
                    "restore_time_seconds": round(file_end - file_start, 4),
                    "error": str(e),
                })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total_restore_time = sum(r["restore_time_seconds"] for r in results)
    passed_count = sum(1 for r in results if r["passed"])
    failed_count = sum(1 for r in results if not r["passed"])

    result = {
        "type": "restore_test",
        "system": system_name,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files_tested": len(sample),
        "results": results,
        "passed": passed_count,
        "failed": failed_count,
        "total_restore_time_seconds": round(total_restore_time, 4),
    }

    _prepend_entry(result)
    logger.info(f"[{system_name}] Restore test — {passed_count} passed, "
                f"{failed_count} failed in {result['total_restore_time_seconds']}s.")
    return result


# ── FUNCTION 3 — RTO / RPO ────────────────────────────────────────────────────────

def calculate_rto_rpo(system_name: str) -> dict:
    """Derive average RTO (from restore tests) and RPO (from backup log cadence)."""
    entries = [
        e for e in _load_benchmarks()
        if e.get("type") == "restore_test" and e.get("system") == system_name
    ]

    # RTO — mean total restore time over the last 5 restore tests (newest first).
    recent = entries[:5]
    avg_rto = None
    if recent:
        times = [e.get("total_restore_time_seconds", 0) for e in recent]
        avg_rto = sum(times) / len(times)

    # RPO — gap between the two most recent successful backups (from the log).
    rpo_minutes = None
    try:
        timestamps = []
        if _LOG_FILE.exists():
            marker = f"[{system_name}] Full sync complete"
            for line in _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                if marker in line:
                    ts = _extract_timestamp(line)
                    if ts is not None:
                        timestamps.append(ts)
        timestamps.sort(reverse=True)
        if len(timestamps) >= 2:
            rpo_minutes = (timestamps[0] - timestamps[1]).total_seconds() / 60
    except Exception as e:
        logger.error(f"RPO calculation failed for '{system_name}': {e}")

    # Rating — only meaningful when both metrics are known.
    if avg_rto is None or rpo_minutes is None:
        rating = "Insufficient Data"
    elif avg_rto < 30 and rpo_minutes < 5:
        rating = "Excellent"
    elif avg_rto < 120 and rpo_minutes < 30:
        rating = "Good"
    else:
        rating = "Poor"

    return {
        "system": system_name,
        "rto_seconds": round(avg_rto, 2) if avg_rto else None,
        "rpo_minutes": round(rpo_minutes, 2) if rpo_minutes else None,
        "rating": rating,
        "restore_tests_used": len(recent),
    }


def _extract_timestamp(log_line: str):
    """Pull the leading [YYYY-MM-DD HH:MM:SS] timestamp from a log line."""
    try:
        if not log_line.startswith("["):
            return None
        end = log_line.find("]")
        if end == -1:
            return None
        return datetime.strptime(log_line[1:end], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


# ── FUNCTION 4 — full report ──────────────────────────────────────────────────────

def generate_benchmark_report(system_name: str) -> str:
    """Build a plaintext performance/recovery report and save it under logs/."""
    all_entries = [e for e in _load_benchmarks() if e.get("system") == system_name]
    benchmarks = [e for e in all_entries if e.get("type") == "benchmark"][:5]
    restores = [e for e in all_entries if e.get("type") == "restore_test"][:5]
    rr = calculate_rto_rpo(system_name)

    line = "═" * 55
    rule = "─" * 39
    parts = []
    parts.append(line)
    parts.append("  backITup — Performance & Recovery Report")
    parts.append(f"  System : {system_name}")
    parts.append(f"  Date   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    parts.append(line)
    parts.append("")

    # Backup benchmarks table
    parts.append(f"── Backup Benchmarks (last 5) {rule[:25]}")
    if benchmarks:
        parts.append(
            f"  {'Timestamp':<21}{'Files':>7}{'Size (MB)':>12}"
            f"{'Duration (s)':>14}{'Throughput (MB/s)':>20}"
        )
        for b in benchmarks:
            parts.append(
                f"  {b.get('timestamp',''):<21}{b.get('files',0):>7}"
                f"{b.get('total_mb',0):>12}{b.get('duration_seconds',0):>14}"
                f"{b.get('throughput_mbps',0):>20}"
            )
    else:
        parts.append("  No benchmark data found.")
    parts.append("")

    # Restore tests table
    parts.append(f"── Restore Tests (last 5) {rule[:29]}")
    if restores:
        parts.append(
            f"  {'Timestamp':<21}{'Files Tested':>14}{'Passed':>8}"
            f"{'Failed':>8}{'Total Time (s)':>16}"
        )
        for r in restores:
            parts.append(
                f"  {r.get('timestamp',''):<21}{r.get('files_tested',0):>14}"
                f"{r.get('passed',0):>8}{r.get('failed',0):>8}"
                f"{r.get('total_restore_time_seconds',0):>16}"
            )
    else:
        parts.append("  No restore test data found.")
    parts.append("")

    # RTO / RPO summary
    rto = rr.get("rto_seconds")
    rpo = rr.get("rpo_minutes")
    parts.append(f"── RTO / RPO Summary {rule[:34]}")
    parts.append(f"  Average RTO : {rto}s    (target: < 30s for Excellent)")
    parts.append(f"  RPO         : {rpo} min (target: < 5 min for Excellent)")
    parts.append(f"  Rating      : {rr.get('rating')}")
    parts.append("")
    parts.append(line)

    report = "\n".join(parts)

    try:
        log_dir = _LOG_FILE.parent
        log_dir.mkdir(parents=True, exist_ok=True)
        date = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = log_dir / f"benchmark_{system_name}_{date}.txt"
        out.write_text(report, encoding="utf-8")
        logger.info(f"Saved benchmark report: {out}")
    except Exception as e:
        logger.error(f"Failed to save benchmark report for '{system_name}': {e}")

    return report
