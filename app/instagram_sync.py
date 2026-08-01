from __future__ import annotations

import argparse
import os
import signal
import time
import json
import shutil
import subprocess
import threading
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
LOGS = PROJECT / "logs"

# V6.4.8 persistent GUI control channel.
CONTROL_FILE: Path | None = None


def parse_json_output(text: str) -> dict[str, Any]:
    text = text.strip()

    if not text:
        raise RuntimeError("Command returned no JSON output")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"Invalid JSON command output: {error}\n"
            f"Output tail:\n{text[-3000:]}"
        ) from error

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Command output is not a JSON object"
        )

    return payload


def emit_progress(
    percent: int,
    stage: str,
    **details: Any,
) -> None:
    """
    Emit a machine-readable progress event for the GUI.

    The RS_PROGRESS prefix prevents these events from being
    confused with the final workflow JSON.
    """
    payload = {
        "percent": max(0, min(100, int(percent))),
        "stage": str(stage),
        **details,
    }

    print(
        "RS_PROGRESS "
        + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def run_module(
    module: str,
    arguments: list[str],
    log_lines: list[str],
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        module,
        *arguments,
    ]

    display_command = " ".join(command)

    print(
        f"\\n▶ {display_command}",
        flush=True,
    )

    started = datetime.now()

    process = subprocess.Popen(
        command,
        cwd=PROJECT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=True,
    )

    all_stdout_lines: list[str] = []
    payload_stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    stdout_stream = process.stdout
    stderr_stream = process.stderr

    def collect_stdout() -> None:
        if stdout_stream is None:
            return

        for line in stdout_stream:
            all_stdout_lines.append(line)

            if line.startswith("RS_PROGRESS "):
                print(
                    line.rstrip("\n"),
                    flush=True,
                )
            else:
                payload_stdout_lines.append(line)

    def collect_stderr() -> None:
        if stderr_stream is None:
            return

        for line in stderr_stream:
            stderr_lines.append(line)

    stdout_thread = threading.Thread(
        target=collect_stdout,
        name=f"{module}-stdout-reader",
        daemon=False,
    )
    stderr_thread = threading.Thread(
        target=collect_stderr,
        name=f"{module}-stderr-reader",
        daemon=False,
    )

    stdout_thread.start()
    stderr_thread.start()

    last_command = "run"
    child_paused = False
    stop_sent_at: float | None = None

    while process.poll() is None:
        requested_command = "run"

        if CONTROL_FILE is not None and CONTROL_FILE.is_file():
            try:
                control_payload = json.loads(
                    CONTROL_FILE.read_text(
                        encoding="utf-8"
                    )
                )
                requested_command = str(
                    control_payload.get("command")
                    or "run"
                ).strip().lower()
            except Exception:
                requested_command = last_command

        if requested_command != last_command:
            if requested_command == "pause":
                try:
                    os.killpg(
                        process.pid,
                        signal.SIGSTOP,
                    )
                    child_paused = True
                    emit_progress(
                        0,
                        "Загрузка приостановлена",
                        state="PAUSED_BY_USER",
                    )
                except ProcessLookupError:
                    pass

            elif requested_command in {"run", "resume"}:
                if child_paused:
                    try:
                        os.killpg(
                            process.pid,
                            signal.SIGCONT,
                        )
                    except ProcessLookupError:
                        pass

                child_paused = False
                emit_progress(
                    0,
                    "Загрузка продолжена",
                    state="RESUMED",
                )

            elif requested_command == "stop":
                if child_paused:
                    try:
                        os.killpg(
                            process.pid,
                            signal.SIGCONT,
                        )
                    except ProcessLookupError:
                        pass

                child_paused = False

                try:
                    os.killpg(
                        process.pid,
                        signal.SIGTERM,
                    )
                except ProcessLookupError:
                    pass

                stop_sent_at = time.monotonic()
                emit_progress(
                    0,
                    "Загрузка остановлена",
                    state="STOP_REQUESTED",
                )

            last_command = requested_command

        if (
            stop_sent_at is not None
            and process.poll() is None
            and time.monotonic() - stop_sent_at >= 8
        ):
            try:
                os.killpg(
                    process.pid,
                    signal.SIGKILL,
                )
            except ProcessLookupError:
                pass

        time.sleep(0.2)

    process.wait()
    stdout_thread.join()
    stderr_thread.join()

    finished = datetime.now()

    stdout_text = "".join(all_stdout_lines)
    payload_stdout_text = "".join(
        payload_stdout_lines
    )
    stderr_text = "".join(stderr_lines)

    duration = round(
        (finished - started).total_seconds(),
        2,
    )

    log_lines.extend([
        "",
        "=" * 80,
        f"COMMAND: {display_command}",
        f"STARTED: {started.isoformat()}",
        f"FINISHED: {finished.isoformat()}",
        f"DURATION_SECONDS: {duration}",
        f"RETURN_CODE: {process.returncode}",
        "",
        "STDOUT",
        "-" * 80,
        stdout_text,
        "",
        "STDERR",
        "-" * 80,
        stderr_text,
    ])

    if process.returncode != 0:
        if stdout_text:
            print(
                stdout_text,
                end=(
                    ""
                    if stdout_text.endswith("\n")
                    else "\n"
                ),
                flush=True,
            )

        if stderr_text:
            print(
                stderr_text,
                end=(
                    ""
                    if stderr_text.endswith("\n")
                    else "\n"
                ),
                file=sys.stderr,
                flush=True,
            )

        raise RuntimeError(
            f"{module} failed with return code "
            f"{process.returncode}"
        )

    payload = parse_json_output(
        payload_stdout_text
    )

    print(
        f"✓ {module} completed in {duration}s",
        flush=True,
    )

    return payload


def next_registered_post_number() -> int:
    """Return MAX(post_number) + 1 from the local registry."""

    import sqlite3

    database = PROJECT / "data" / "reference_sync.sqlite3"

    if not database.is_file():
        return 1

    connection = sqlite3.connect(database)

    try:
        table_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = 'instagram_sync_post_order'
            """
        ).fetchone()

        if table_exists is None:
            return 1

        row = connection.execute(
            """
            SELECT MAX(post_number)
            FROM instagram_sync_post_order
            WHERE order_marker = 'instpoporder'
            """
        ).fetchone()

        maximum = row[0] if row else None

        if maximum is None:
            return 1

        return int(maximum) + 1
    finally:
        connection.close()



def resolve_mixed_post_numbers(
    candidates: list[dict],
    start_number: int,
) -> list[int]:
    """
    Partial posts retain their registered numbers.
    New posts receive sequential numbers, skipping all
    registered numbers reserved by selected partial posts.
    """
    if start_number < 1:
        raise RuntimeError("INVALID_START_NUMBER")

    reserved: set[int] = set()

    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise RuntimeError(
                "NUMBERING_CANDIDATE_IS_NOT_OBJECT"
            )

        if not bool(
            candidate.get("resume_partial")
            or candidate.get("restore_deleted")
        ):
            continue

        try:
            number = int(
                candidate.get("existing_post_number")
            )
        except (TypeError, ValueError):
            raise RuntimeError(
                "PARTIAL_RESUME_HAS_NO_EXISTING_NUMBER"
            )

        if number < 1:
            raise RuntimeError(
                "PARTIAL_RESUME_HAS_INVALID_NUMBER"
            )

        if number in reserved:
            raise RuntimeError(
                "DUPLICATE_PARTIAL_RESUME_NUMBER"
            )

        reserved.add(number)

    result: list[int] = []
    used: set[int] = set()
    next_number = start_number

    for candidate in candidates:
        if bool(
            candidate.get("resume_partial")
            or candidate.get("restore_deleted")
        ):
            number = int(
                candidate["existing_post_number"]
            )
        else:
            while (
                next_number in reserved
                or next_number in used
            ):
                next_number += 1

            number = next_number
            next_number += 1

        if number in used:
            raise RuntimeError(
                f"POST_NUMBER_ASSIGNED_TWICE: {number}"
            )

        result.append(number)
        used.add(number)

    return result


def cleanup_registered_staging(
    job_id: object,
) -> dict:
    """
    Remove one staging job only after Eagle import and SQLite
    registration have both completed successfully.
    """
    normalized = str(job_id or "").strip()

    result = {
        "attempted": False,
        "staging_deleted": False,
        "source_files_deleted": 0,
        "staging_bytes_freed": 0,
        "cleanup_error": None,
    }

    if (
        not normalized
        or not normalized.startswith("instagram_")
        or "/" in normalized
        or "\\" in normalized
    ):
        result["cleanup_error"] = "INVALID_JOB_ID"
        return result

    incoming = (
        PROJECT
        / "downloads"
        / "instagram"
        / "incoming"
    ).resolve()

    job_directory = (incoming / normalized).resolve()

    try:
        job_directory.relative_to(incoming)
    except ValueError:
        result["cleanup_error"] = (
            "JOB_OUTSIDE_STAGING_DIRECTORY"
        )
        return result

    if not job_directory.is_dir():
        result["cleanup_error"] = (
            "STAGING_JOB_NOT_FOUND"
        )
        return result

    job_file = job_directory / "job.json"

    if not job_file.is_file():
        result["cleanup_error"] = "JOB_JSON_NOT_FOUND"
        return result

    try:
        job = json.loads(
            job_file.read_text(encoding="utf-8")
        )
    except Exception as error:
        result["cleanup_error"] = (
            f"JOB_JSON_INVALID: {error}"
        )
        return result

    if str(job.get("job_id") or "") != normalized:
        result["cleanup_error"] = "JOB_ID_MISMATCH"
        return result

    if job.get("status") != "IMPORTED_REGISTERED":
        result["cleanup_error"] = (
            "JOB_NOT_IMPORTED_REGISTERED"
        )
        return result

    files = [
        path
        for path in job_directory.rglob("*")
        if path.is_file()
    ]

    bytes_to_free = 0

    for path in files:
        try:
            bytes_to_free += path.stat().st_size
        except OSError:
            pass

    result["attempted"] = True

    try:
        shutil.rmtree(job_directory)
    except Exception as error:
        result["cleanup_error"] = (
            f"STAGING_DELETE_FAILED: {error}"
        )
        return result

    result.update({
        "staging_deleted": not job_directory.exists(),
        "source_files_deleted": len(files),
        "staging_bytes_freed": bytes_to_free,
        "cleanup_error": None,
    })

    return result

# DOWNLOAD_SPEED_AND_VPN_SAFETY_V1
# STALE_DISCOVERY_SELECTED_POST_RECOVERY_V1
from app.instagram_candidate_recovery import (
    recover_selected_posts,
)



# V6.4.8 STAGE6 COMPATIBLE_STOPPED_CLEANUP
def _cleanup_job_signature(
    posts: list[dict[str, Any]],
) -> str:
    rows = []

    for post in posts:
        if not isinstance(post, dict):
            continue

        post_id = str(post.get("post_id") or "").strip()

        if not post_id:
            continue

        raw_components = post.get(
            "selected_components",
            [],
        )
        components = []

        if isinstance(raw_components, list):
            for raw_component in raw_components:
                try:
                    component = int(raw_component)
                except (TypeError, ValueError):
                    continue

                if component > 0 and component not in components:
                    components.append(component)

        rows.append({
            "post_id": post_id,
            "selected_components": sorted(components),
        })

    return json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def cleanup_compatible_stopped_staging(
    active_job_id: str | None,
) -> dict[str, Any]:
    """
    Delete exact duplicate STOPPED_BY_USER jobs only after the active
    job has been fully imported and finalized.

    AUTH_REQUIRED, PAUSED_NETWORK, review jobs and incompatible
    selections are never deleted here.
    """
    result = {
        "attempted": False,
        "compatible_jobs_deleted": [],
        "compatible_files_deleted": 0,
        "compatible_bytes_freed": 0,
        "cleanup_errors": [],
    }

    normalized = str(active_job_id or "").strip()

    if (
        not normalized
        or "/" in normalized
        or "\\" in normalized
    ):
        result["cleanup_errors"].append(
            "INVALID_ACTIVE_JOB_ID"
        )
        return result

    incoming = (
        PROJECT
        / "downloads"
        / "instagram"
        / "incoming"
    )
    active_directory = incoming / normalized
    active_job_file = active_directory / "job.json"

    if not active_job_file.is_file():
        result["cleanup_errors"].append(
            "ACTIVE_JOB_JSON_NOT_FOUND"
        )
        return result

    try:
        active_payload = json.loads(
            active_job_file.read_text(encoding="utf-8")
        )
    except Exception as error:
        result["cleanup_errors"].append(
            "ACTIVE_JOB_JSON_INVALID: "
            + str(error)
        )
        return result

    active_posts = active_payload.get("posts")

    if not isinstance(active_posts, list):
        result["cleanup_errors"].append(
            "ACTIVE_JOB_POSTS_INVALID"
        )
        return result

    active_signature = _cleanup_job_signature(
        active_posts
    )

    result["attempted"] = True

    for candidate_directory in sorted(
        incoming.glob("instagram_*")
    ):
        if (
            not candidate_directory.is_dir()
            or candidate_directory.name == normalized
        ):
            continue

        candidate_job_file = (
            candidate_directory / "job.json"
        )

        if not candidate_job_file.is_file():
            continue

        try:
            candidate_payload = json.loads(
                candidate_job_file.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            continue

        if not isinstance(candidate_payload, dict):
            continue

        if (
            candidate_payload.get("status")
            != "STOPPED_BY_USER"
        ):
            continue

        candidate_posts = candidate_payload.get("posts")

        if not isinstance(candidate_posts, list):
            continue

        if (
            _cleanup_job_signature(candidate_posts)
            != active_signature
        ):
            continue

        files = [
            path
            for path in candidate_directory.rglob("*")
            if path.is_file()
        ]

        bytes_to_free = 0

        for path in files:
            try:
                bytes_to_free += path.stat().st_size
            except OSError:
                pass

        try:
            shutil.rmtree(candidate_directory)
        except Exception as error:
            result["cleanup_errors"].append(
                candidate_directory.name
                + ": "
                + str(error)
            )
            continue

        result["compatible_jobs_deleted"].append(
            candidate_directory.name
        )
        result["compatible_files_deleted"] += len(files)
        result["compatible_bytes_freed"] += bytes_to_free

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Instagram Saved → staging → Eagle sync"
        )
    )

    parser.add_argument(
        "--username",
        required=True,
        help="Your Instagram username",
    )

    parser.add_argument(
        "--browser",
        default="chrome",
        help="Browser used for Instagram cookies",
    )

    # DOWNLOAD_SPEED_AND_VPN_SAFETY_V1
    parser.add_argument(
        "--speed-profile",
        choices=("safe", "balanced"),
        default="safe",
        help="Download pacing and retry profile",
    )

    # UNLIMITED_SAVED_RETRIEVAL_V61
    parser.add_argument(
        "--search-mode",
        choices=("recent", "smart", "full"),
        default="recent",
        help=(
            "Saved discovery strategy. Full mode ignores --limit "
            "and continues gallery-dl cursor pagination to the end."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help=(
            "Number of recent saved posts to inspect. "
            "Ignored when --search-mode=full."
        ),
    )

    parser.add_argument(
        "--collection",
        action="append",
        default=[],
        metavar="ID:NAME",
        help=(
            "Limit discovery to a saved collection. "
            "Can be repeated."
        ),
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help=(
            "Maximum number of new posts processed "
            "in one committed batch"
        ),
    )

    numbering_group = parser.add_mutually_exclusive_group()

    numbering_group.add_argument(
        "--start-number",
        type=int,
        default=None,
        help=(
            "Start numbering this batch from the "
            "specified post number"
        ),
    )

    numbering_group.add_argument(
        "--continue-numbering",
        action="store_true",
        help=(
            "Continue after the maximum instpoporder "
            "number stored in the local registry"
        ),
    )

    parser.add_argument(
        "--post-id",
        dest="post_ids",
        action="append",
        default=[],
        help=(
            "Process the specified discovered Instagram "
            "post ID. May be supplied multiple times."
        ),
    )

    parser.add_argument(
        "--naming-manifest",
        default=None,
        help=(
            "Optional JSON manifest containing exact Eagle "
            "names and descriptions for selected posts"
        ),
    )

    parser.add_argument(
        "--discovery-manifest",
        default=None,
        help=(
            "Saved GUI discovery snapshot. When supplied during "
            "commit, Instagram discovery is not repeated."
        ),
    )

    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Download, validate, import and register "
            "new posts. Without this option discovery "
            "is read-only."
        ),
    )

    parser.add_argument(
        "--control-file",
        default=None,
        help=(
            "JSON control channel used by the GUI for "
            "Pause, Resume and Stop."
        ),
    )

    parser.add_argument(
        "--resume-job",
        default=None,
        help=(
            "Continue a specific exact STOPPED_BY_USER "
            "staging job. Normally automatic matching is used."
        ),
    )

    args = parser.parse_args()

    global CONTROL_FILE

    if args.control_file:
        CONTROL_FILE = Path(
            args.control_file
        ).expanduser().resolve()
        CONTROL_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    if args.naming_manifest:
        manifest_path = Path(
            args.naming_manifest
        ).expanduser().resolve()

        if not manifest_path.is_file():
            raise SystemExit(
                "Naming manifest was not found: "
                f"{manifest_path}"
            )

        args.naming_manifest = str(manifest_path)

    if args.discovery_manifest:
        discovery_manifest_path = Path(
            args.discovery_manifest
        ).expanduser().resolve()

        if not discovery_manifest_path.is_file():
            raise SystemExit(
                "Discovery manifest was not found: "
                f"{discovery_manifest_path}"
            )

        args.discovery_manifest = str(
            discovery_manifest_path
        )

    if args.search_mode in {"full", "smart"}:
        if args.limit < 0:
            raise SystemExit(
                "--limit cannot be negative in unlimited modes"
            )
    elif args.limit < 1 or args.limit > 500:
        raise SystemExit(
            "--limit must be between 1 and 500 "
            "for recent mode"
        )

    if args.batch_size < 1 or args.batch_size > 50:
        raise SystemExit(
            "--batch-size must be between 1 and 50"
        )

    normalized_post_ids = []
    seen_post_ids = set()

    for raw_post_id in args.post_ids:
        post_id = str(raw_post_id).strip()

        if not post_id or not post_id.isdigit():
            raise SystemExit(
                "--post-id must contain a numeric Instagram "
                "post ID"
            )

        if post_id in seen_post_ids:
            continue

        seen_post_ids.add(post_id)
        normalized_post_ids.append(post_id)

    if len(normalized_post_ids) > 50:
        raise SystemExit(
            "No more than 50 --post-id values are allowed"
        )

    args.post_ids = normalized_post_ids

    if (
        args.start_number is not None
        and args.start_number < 1
    ):
        raise SystemExit(
            "--start-number must be at least 1"
        )

    eagle_numbering_scan = None

    if args.continue_numbering:
        from app.instagram_order_eagle_scan import (
            scan_order_names,
        )

        eagle_numbering_scan = scan_order_names()

        registry_next_number = (
            next_registered_post_number()
        )
        eagle_next_number = int(
            eagle_numbering_scan.get(
                "automatic_next_number",
                1,
            )
        )

        eagle_scan_errors = (
            eagle_numbering_scan.get(
                "fallback_errors",
                [],
            )
        )

        if (
            eagle_scan_errors
            and int(
                eagle_numbering_scan.get(
                    "items_received",
                    0,
                )
                or 0
            ) == 0
        ):
            # Fail safely to historical SQLite only when Eagle
            # could not be read at all.
            resolved_start_number = (
                registry_next_number
            )
            numbering_source = (
                "SQLITE_FALLBACK_EAGLE_UNAVAILABLE"
            )
        else:
            # Deleted Eagle items release their numbers.
            resolved_start_number = (
                eagle_next_number
            )
            numbering_source = (
                "LIVE_EAGLE_MAXIMUM"
            )

        numbering_mode = "CONTINUE_AUTOMATICALLY"
    elif args.start_number is not None:
        resolved_start_number = args.start_number
        numbering_mode = "MANUAL_START"
    else:
        resolved_start_number = 1
        numbering_mode = "RESET_TO_ONE"

    print(json.dumps(
        {
            "status": "NUMBERING_SETTINGS_RESOLVED",
            "numbering_mode": numbering_mode,
            "start_number": resolved_start_number,
            "numbering_source": (
                locals().get(
                    "numbering_source",
                    "MANUAL_OR_RESET",
                )
            ),
            "batch_size": args.batch_size,
        },
        ensure_ascii=False,
        indent=2,
    ))

    run_id = (
        "sync_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    emit_progress(
        2,
        "Подготовка импорта",
    )

    log_lines = [
        f"RUN_ID: {run_id}",
        f"USERNAME: {args.username}",
        f"BROWSER: {args.browser}",
        f"SEARCH_MODE: {args.search_mode}",
        f"LIMIT: {args.limit}",
        f"COMMIT: {args.commit}",
    ]

    started_at = datetime.now()

    workflow = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "username": args.username,
        "browser": args.browser,
        "search_mode": args.search_mode,
        "limit": (
            args.limit
            if args.search_mode == "recent"
            else None
        ),
        "commit": args.commit,
        "steps": [],
    }

    try:
        # -------------------------------------------------
        # STEP 1: DISCOVERY
        # -------------------------------------------------
        if args.commit and args.discovery_manifest:
            saved_payload = json.loads(
                Path(args.discovery_manifest).read_text(
                    encoding="utf-8"
                )
            )

            saved_posts = saved_payload.get(
                "discovery_posts"
            )

            if (
                not isinstance(saved_posts, list)
                or not saved_posts
            ):
                raise RuntimeError(
                    "DISCOVERY_MANIFEST_HAS_NO_POSTS"
                )

            selected_ids = set(args.post_ids)

            if selected_ids:
                saved_posts = [
                    post
                    for post in saved_posts
                    if (
                        isinstance(post, dict)
                        and str(
                            post.get("post_id") or ""
                        ) in selected_ids
                    )
                ]

            saved_by_id = {
                str(post.get("post_id") or ""): post
                for post in saved_posts
                if isinstance(post, dict)
            }

            missing_saved_ids = [
                post_id
                for post_id in args.post_ids
                if post_id not in saved_by_id
            ]

            if missing_saved_ids:
                raise RuntimeError(
                    "POSTS_MISSING_FROM_DISCOVERY_MANIFEST: "
                    + ", ".join(missing_saved_ids)
                )

            discovery = {
                "summary": {
                    "status": "OK",
                    "logical_posts_returned": len(saved_posts),
                    "known_baseline_posts": 0,
                    "new_post_candidates": len(saved_posts),
                    "source": "SAVED_GUI_DISCOVERY_MANIFEST",
                },
                "new_post_candidates": saved_posts,
                "safety": {
                    "simulation_only": True,
                    "media_downloaded": 0,
                    "eagle_items_created": 0,
                    "database_modified": False,
                },
            }

            workflow["discovery_reused"] = True
            workflow["discovery_manifest"] = (
                args.discovery_manifest
            )

            print(json.dumps({
                "status": "DISCOVERY_SNAPSHOT_REUSED",
                "posts": len(saved_posts),
                "discovery_repeated": False,
                "eagle_items_created": 0,
                "database_modified": False,
            }, ensure_ascii=False, indent=2))
        else:
            discovery_arguments = [
                "--username",
                args.username,
                "--browser",
                args.browser,
                "--scan-speed",
                args.speed_profile,
                "--search-mode",
                args.search_mode,
                "--limit",
                str(args.limit),
            ]

            for entry in args.collection or []:
                discovery_arguments.extend([
                    "--collection",
                    str(entry),
                ])

            discovery = run_module(
                "app.instagram_discover",
                discovery_arguments,
                log_lines,
            )

        discovery_summary = discovery.get(
            "summary",
            {},
        )

        workflow["steps"].append({
            "step": "DISCOVERY",
            "status": discovery_summary.get(
                "status"
            ),
            "logical_posts_returned": (
                discovery_summary.get(
                    "logical_posts_returned"
                )
            ),
            "known_posts": discovery_summary.get(
                "known_baseline_posts"
            ),
            "new_post_candidates": (
                discovery_summary.get(
                    "new_post_candidates"
                )
            ),
        })

        if discovery_summary.get("status") != "OK":
            final_status = "DISCOVERY_REQUIRES_REVIEW"

            result = {
                "status": final_status,
                "run_id": run_id,
                "discovery": discovery_summary,
                "commit": args.commit,
                "eagle_items_created": 0,
                "database_modified": False,
            }

            workflow["status"] = final_status
            workflow["result"] = result
            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))
            return

        new_count = int(
            discovery_summary.get(
                "new_post_candidates",
                0,
            )
        )

        if new_count == 0:
            final_status = "NO_NEW_POSTS"

            result = {
                "status": final_status,
                "run_id": run_id,
                "checked_posts": (
                    discovery_summary.get(
                        "logical_posts_returned"
                    )
                ),
                "known_posts": (
                    discovery_summary.get(
                        "known_baseline_posts"
                    )
                ),
                "new_posts": 0,
                "files_downloaded": 0,
                "eagle_items_created": 0,
                "database_modified": False,
            }

            workflow["status"] = final_status
            workflow["result"] = result

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))
            return

        selected_post_count = min(
            new_count,
            args.batch_size,
        )

        # MIXED_QUEUE_SELECTED_CANDIDATES_ORDER_HOTFIX_V1
        #
        # During discovery/search selected_candidates has not
        # been constructed yet. Preserve the original sequential
        # collision scan for that read-only phase. During an
        # explicit GUI import, resolve the selected candidates
        # directly from the discovery result and retain saved
        # numbers for partial resumes.
        if args.post_ids:
            numbering_raw_candidates = discovery.get(
                "new_post_candidates",
                [],
            )

            if not isinstance(
                numbering_raw_candidates,
                list,
            ):
                raise RuntimeError(
                    "DISCOVERY_CANDIDATES_NOT_LIST"
                )

            numbering_selection_posts = {}

            if args.naming_manifest:
                naming_payload = json.loads(
                    Path(
                        args.naming_manifest
                    ).read_text(encoding="utf-8")
                )
                raw_manifest_posts = naming_payload.get(
                    "posts",
                    {},
                )

                if isinstance(raw_manifest_posts, dict):
                    numbering_selection_posts = {
                        str(post_id): entry
                        for post_id, entry
                        in raw_manifest_posts.items()
                        if isinstance(entry, dict)
                    }

            (
                numbering_candidates,
                recovered_numbering_ids,
            ) = recover_selected_posts(
                numbering_raw_candidates,
                args.post_ids,
                REPORTS,
                numbering_selection_posts,
            )

            current_candidate_ids = {
                str(candidate.get("post_id") or "")
                for candidate in numbering_raw_candidates
                if isinstance(candidate, dict)
            }

            for recovered_candidate in numbering_candidates:
                recovered_id = str(
                    recovered_candidate.get("post_id")
                    or ""
                )

                if recovered_id not in current_candidate_ids:
                    numbering_raw_candidates.append(
                        recovered_candidate
                    )
                    current_candidate_ids.add(
                        recovered_id
                    )

            discovery["new_post_candidates"] = (
                numbering_raw_candidates
            )

            if recovered_numbering_ids:
                print(json.dumps({
                    "status": (
                        "SELECTED_POSTS_RECOVERED_FROM_"
                        "DISCOVERY_HISTORY"
                    ),
                    "post_ids": recovered_numbering_ids,
                    "eagle_items_created": 0,
                    "database_modified": False,
                }, ensure_ascii=False, indent=2))

            resolved_post_numbers = (
                resolve_mixed_post_numbers(
                    numbering_candidates,
                    resolved_start_number,
                )
            )

            target_post_numbers = set(
                resolved_post_numbers
            )
        else:
            target_post_numbers = set(range(
                resolved_start_number,
                resolved_start_number
                + selected_post_count,
            ))

        if eagle_numbering_scan is None:
            from app.instagram_order_eagle_scan import (
                scan_order_names,
            )

            eagle_numbering_scan = scan_order_names(
                target_post_numbers
            )
        else:
            existing_rows = (
                eagle_numbering_scan.get(
                    "collisions",
                    [],
                )
            )

            if not existing_rows:
                from app.instagram_order_eagle_scan import (
                    scan_order_names,
                )

                eagle_numbering_scan = scan_order_names(
                    target_post_numbers
                )

        numbering_collisions = (
            eagle_numbering_scan.get(
                "collisions",
                [],
            )
        )

        # SAFE_RESUME_NUMBERING_COLLISION_V1
        #
        # A number already used by the same partially imported
        # carousel is not a new-number collision. This early
        # exception only allows rows that correspond to components
        # already registered for the resume candidate.
        #
        # eagle_import_staging performs the stricter SQLite ↔ Eagle
        # identity verification before any new Eagle item is created.
        if numbering_collisions:
            raw_candidates = discovery.get(
                "new_post_candidates",
                [],
            )

            if not isinstance(raw_candidates, list):
                raw_candidates = []

            candidate_by_id = {
                str(candidate.get("post_id") or ""): candidate
                for candidate in raw_candidates
                if isinstance(candidate, dict)
            }

            if args.post_ids:
                collision_candidates = [
                    candidate_by_id[post_id]
                    for post_id in args.post_ids
                    if post_id in candidate_by_id
                ]
            else:
                collision_candidates = raw_candidates[
                    :selected_post_count
                ]

            resume_candidates = []

            for candidate in collision_candidates:
                if not isinstance(candidate, dict):
                    continue

                if not bool(candidate.get("resume_partial")):
                    continue

                try:
                    saved_number = int(
                        candidate.get("existing_post_number")
                    )
                except (TypeError, ValueError):
                    continue

                if saved_number < 1:
                    continue

                candidate_url = str(
                    candidate.get("post_url")
                    or candidate.get("canonical_url")
                    or ""
                ).strip().rstrip("/")

                if not candidate_url:
                    continue

                imported_components = set()

                for value in candidate.get(
                    "imported_component_numbers",
                    [],
                ):
                    try:
                        component_number = int(value)
                    except (TypeError, ValueError):
                        continue

                    if component_number > 0:
                        imported_components.add(
                            component_number
                        )

                imported_media_ids = {
                    str(value).strip()
                    for value in candidate.get(
                        "imported_media_ids",
                        [],
                    )
                    if str(value).strip()
                }

                if not imported_components:
                    continue

                if (
                    len(imported_media_ids)
                    != len(imported_components)
                ):
                    continue

                resume_candidates.append({
                    "post_id": str(
                        candidate.get("post_id") or ""
                    ),
                    "post_url": candidate_url,
                    "post_number": saved_number,
                    "imported_component_numbers": (
                        imported_components
                    ),
                    "imported_media_ids": imported_media_ids,
                })

            blocked_numbering_collisions = []
            allowed_resume_collisions = []

            for collision in numbering_collisions:
                if not isinstance(collision, dict):
                    blocked_numbering_collisions.append(
                        collision
                    )
                    continue

                try:
                    collision_number = int(
                        collision.get("post_number")
                    )
                    collision_component = int(
                        collision.get("component_number")
                    )
                except (TypeError, ValueError):
                    blocked_numbering_collisions.append(
                        collision
                    )
                    continue

                collision_url = str(
                    collision.get("url") or ""
                ).strip().rstrip("/")

                matching_candidates = [
                    candidate
                    for candidate in resume_candidates
                    if (
                        candidate["post_number"]
                        == collision_number
                        and candidate["post_url"]
                        == collision_url
                        and collision_component
                        in candidate[
                            "imported_component_numbers"
                        ]
                    )
                ]

                if len(matching_candidates) == 1:
                    allowed_resume_collisions.append({
                        "eagle_item_id": collision.get("id"),
                        "post_id": matching_candidates[0][
                            "post_id"
                        ],
                        "post_url": collision_url,
                        "post_number": collision_number,
                        "component_number": (
                            collision_component
                        ),
                        "reason": (
                            "MATCHED_PARTIAL_RESUME_COMPONENT"
                        ),
                    })
                else:
                    blocked_numbering_collisions.append(
                        collision
                    )

            if allowed_resume_collisions:
                audit_event = {
                    "status": (
                        "RESUME_NUMBERING_COLLISIONS_ALLOWED"
                    ),
                    "allowed": allowed_resume_collisions,
                    "remaining_blocked": (
                        len(blocked_numbering_collisions)
                    ),
                    "note": (
                        "Final SQLite and Eagle identity "
                        "verification is still required."
                    ),
                }

                print(json.dumps(
                    audit_event,
                    ensure_ascii=False,
                    indent=2,
                ))

                workflow[
                    "allowed_resume_numbering_collisions"
                ] = allowed_resume_collisions

            numbering_collisions = (
                blocked_numbering_collisions
            )

        if numbering_collisions:
            final_status = (
                "NUMBERING_COLLISIONS_FOUND"
            )

            result = {
                "status": final_status,
                "run_id": run_id,
                "numbering_mode": numbering_mode,
                "requested_start_number": (
                    resolved_start_number
                ),
                "requested_post_numbers": sorted(
                    target_post_numbers
                ),
                "collisions": (
                    numbering_collisions
                ),
                "suggested_next_number": (
                    eagle_numbering_scan.get(
                        "automatic_next_number"
                    )
                ),
                "instruction": (
                    "Choose another --start-number "
                    "or use --continue-numbering."
                ),
                "files_downloaded": 0,
                "eagle_items_created": 0,
                "database_modified": False,
            }

            workflow["status"] = final_status
            workflow["result"] = result

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))
            return

        if not args.commit:
            final_status = "NEW_POSTS_FOUND_PREVIEW"

            all_candidates = discovery.get(
                "new_post_candidates",
                [],
            )

            if not isinstance(all_candidates, list):
                raise RuntimeError(
                    "new_post_candidates is not a list"
                )

            selected_preview_count = (
                selected_post_count
            )

            if args.post_ids:
                candidate_by_id = {
                    str(candidate.get("post_id")): candidate
                    for candidate in all_candidates
                    if isinstance(candidate, dict)
                }

                missing_post_ids = [
                    post_id
                    for post_id in args.post_ids
                    if post_id not in candidate_by_id
                ]

                if missing_post_ids:
                    raise RuntimeError(
                        "Requested post IDs were not found "
                        "among new candidates: "
                        + ", ".join(missing_post_ids)
                    )

                selected_candidates = [
                    candidate_by_id[post_id]
                    for post_id in args.post_ids
                ]
                selected_preview_count = len(
                    selected_candidates
                )
            else:
                # V6.2: the table receives every candidate.
                # posts_selected_for_batch remains capped at 50;
                # the GUI checks only the first batch.
                selected_candidates = all_candidates

            numbering_preview = []

            resolved_post_numbers = (
                resolve_mixed_post_numbers(
                    selected_candidates,
                    resolved_start_number,
                )
            )

            # MIXED_QUEUE_OFFSET_HOTFIX_V1
            for offset, (
                candidate,
                resolved_post_number,
            ) in enumerate(
                zip(
                    selected_candidates,
                    resolved_post_numbers,
                    strict=True,
                )
            ):
                if not isinstance(candidate, dict):
                    raise RuntimeError(
                        "Discovery candidate is not an object"
                    )

                existing_post_number = candidate.get(
                    "existing_post_number"
                )

                try:
                    existing_post_number = int(
                        existing_post_number
                    )
                except (TypeError, ValueError):
                    existing_post_number = None

                resume_partial = bool(
                    candidate.get("resume_partial")
                )
                restore_deleted = bool(
                    candidate.get("restore_deleted")
                )
                preserves_existing_number = (
                    resume_partial
                    or restore_deleted
                )

                post_number = resolved_post_number

                if (
                    preserves_existing_number
                    and (
                        existing_post_number is None
                        or existing_post_number < 1
                        or post_number
                        != existing_post_number
                    )
                ):
                    raise RuntimeError(
                        "RESUME_POST_NUMBER_CHANGED"
                    )

                username = str(
                    candidate.get("username")
                    or candidate.get("owner_username")
                    or "unknown"
                ).strip().lstrip("@")

                raw_component_count = candidate.get(
                    "component_count_returned"
                )

                if raw_component_count is None:
                    raw_component_count = candidate.get(
                        "components"
                    )

                if raw_component_count is None:
                    media_ids = candidate.get(
                        "media_ids"
                    )

                    if isinstance(media_ids, list):
                        raw_component_count = len(
                            media_ids
                        )
                    else:
                        raw_component_count = 1

                try:
                    component_count = max(
                        1,
                        int(raw_component_count),
                    )
                except (TypeError, ValueError):
                    component_count = 1

                base_name = f"@{username}"

                if component_count == 1:
                    proposed_names = [
                        (
                            f"{base_name} "
                            f"instpoporder-{post_number}"
                        )
                    ]
                else:
                    proposed_names = [
                        (
                            f"{base_name} "
                            f"instpoporder-{post_number}-"
                            f"{component_number}"
                        )
                        for component_number in range(
                            1,
                            component_count + 1,
                        )
                    ]

                numbering_preview.append({
                                        "containers": (
                        candidate.get("containers")
                        if isinstance(
                            candidate.get("containers"),
                            list,
                        )
                        else []
                    ),
                    "position_in_batch": offset + 1,
                    "post_id": candidate.get(
                        "post_id"
                    ),
                    "post_url": (
                        candidate.get("post_url")
                        or candidate.get(
                            "canonical_url"
                        )
                    ),
                    "username": f"@{username}",
                    "description": str(
                        candidate.get("description") or ""
                    ),
                    "post_number": post_number,
                    "component_count": component_count,
                    "extensions": (
                        candidate.get("extensions")
                        if isinstance(
                            candidate.get("extensions"),
                            dict,
                        )
                        else {}
                    ),
                    "component_items": (
                        candidate.get("component_items")
                        if isinstance(
                            candidate.get("component_items"),
                            list,
                        )
                        else []
                    ),
                    "resume_partial": resume_partial,
                    "restore_deleted": restore_deleted,
                    "discovery_status": (
                        candidate.get("discovery_status")
                    ),
                    "existing_post_number": (
                        existing_post_number
                    ),
                    "imported_component_numbers": (
                        candidate.get(
                            "imported_component_numbers"
                        )
                        if isinstance(
                            candidate.get(
                                "imported_component_numbers"
                            ),
                            list,
                        )
                        else []
                    ),
                    "imported_media_ids": (
                        candidate.get(
                            "imported_media_ids"
                        )
                        if isinstance(
                            candidate.get(
                                "imported_media_ids"
                            ),
                            list,
                        )
                        else []
                    ),
                    "available_component_numbers": (
                        candidate.get(
                            "available_component_numbers"
                        )
                        if isinstance(
                            candidate.get(
                                "available_component_numbers"
                            ),
                            list,
                        )
                        else []
                    ),
                    "media_ids": (
                        candidate.get("media_ids")
                        if isinstance(
                            candidate.get("media_ids"),
                            list,
                        )
                        else []
                    ),
                    "component_numbers": (
                        candidate.get("component_numbers")
                        if isinstance(
                            candidate.get(
                                "component_numbers"
                            ),
                            list,
                        )
                        else []
                    ),
                    "proposed_names": proposed_names,
                })

            if numbering_mode == "MANUAL_START":
                numbering_argument = (
                    f"--start-number "
                    f"{resolved_start_number} "
                )
            elif (
                numbering_mode
                == "CONTINUE_AUTOMATICALLY"
            ):
                numbering_argument = (
                    "--continue-numbering "
                )
            else:
                numbering_argument = ""

            collection_argument = "".join(
                f'--collection "{entry}" '
                for entry in (args.collection or [])
            )

            next_command = (
                "python -m app.instagram_sync "
                f'--username "{args.username}" '
                f'--browser "{args.browser}" '
                f'--speed-profile "{args.speed_profile}" '
                f'--search-mode "{args.search_mode}" '
                f"--limit {args.limit} "
                f"{collection_argument}"
                f"--batch-size "
                f"{selected_preview_count} "
                f"{numbering_argument}"
                "--commit"
            )

            result = {
                "status": final_status,
                "run_id": run_id,
                "numbering": {
                    "mode": numbering_mode,
                    "start_number": (
                        resolved_start_number
                    ),
                    "selected_posts": (
                        selected_preview_count
                    ),
                    "last_post_number": (
                        resolved_start_number
                        + selected_preview_count
                        - 1
                    ),
                },
                "eagle_numbering_check": {
                    "status": (
                        "NUMBERING_RANGE_AVAILABLE"
                    ),
                    "strategy": (
                        eagle_numbering_scan.get(
                            "strategy"
                        )
                    ),
                    "target_numbers": sorted(
                        target_post_numbers
                    ),
                    "existing_maximum": (
                        eagle_numbering_scan.get(
                            "maximum_post_number"
                        )
                    ),
                    "automatic_next_number": (
                        eagle_numbering_scan.get(
                            "automatic_next_number"
                        )
                    ),
                    "collisions": [],
                },
                "new_posts_available": new_count,
                "posts_shown_in_table": len(
                    numbering_preview
                ),
                "posts_selected_for_batch": (
                    selected_preview_count
                ),
                "numbering_preview": (
                    numbering_preview
                ),
                "confirmation_required": True,
                "confirmation_instruction": (
                    "Review numbering_preview. "
                    "Run next_command only if the "
                    "names and numbering are correct."
                ),
                "next_command": next_command,
                "files_downloaded": 0,
                "eagle_items_created": 0,
                "database_modified": False,
            }

            workflow["status"] = final_status
            workflow["result"] = result

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))
            return

        # -------------------------------------------------
        # STEP 2: DOWNLOAD + VALIDATION
        # -------------------------------------------------
        staging_arguments = [
            "--browser",
            args.browser,
            "--speed-profile",
            args.speed_profile,
            "--max-posts",
            str(selected_post_count),
        ]

        for post_id in args.post_ids:
            staging_arguments.extend([
                "--post-id",
                post_id,
            ])

        if args.naming_manifest:
            staging_arguments.extend([
                "--selection-manifest",
                args.naming_manifest,
            ])

        if args.discovery_manifest:
            staging_arguments.extend([
                "--discovery-manifest",
                args.discovery_manifest,
            ])

        if args.resume_job:
            staging_arguments.extend([
                "--resume-job",
                args.resume_job,
            ])

        emit_progress(
            3,
            "Подготовка скачивания",
        )

        staging = run_module(
            "app.instagram_download_staging",
            staging_arguments,
            log_lines,
        )

        staging_summary = staging.get(
            "summary",
            {},
        )

        if (
            staging_summary.get("status")
            == "STOPPED_BY_USER"
        ):
            emit_progress(
                0,
                "Процесс остановлен пользователем",
                state="STOPPED_BY_USER",
            )
        elif (
            staging_summary.get("status")
            == "STAGING_READY"
        ):
            emit_progress(
                90,
                "Скачивание и проверка завершены",
            )

        workflow["steps"].append({
            "step": "STAGING",
            "status": staging_summary.get("status"),
            "job_id": staging_summary.get("job_id"),
            "posts": staging_summary.get(
                "logical_posts_requested"
            ),
            "media": staging_summary.get(
                "downloaded_primary_media"
            ),
            "issues": staging_summary.get("issues"),
        })

        if staging_summary.get("status") != "STAGING_READY":
            if (
                staging_summary.get("status")
                == "STOPPED_BY_USER"
            ):
                final_status = (
                    "DOWNLOAD_STOPPED_BY_USER"
                )
            elif (
                staging_summary.get("status")
                == "PAUSED_NETWORK"
            ):
                final_status = (
                    "DOWNLOAD_PAUSED_NETWORK"
                )
            else:
                final_status = (
                    "STAGING_REQUIRES_REVIEW"
                )

            result = {
                "status": final_status,
                "run_id": run_id,
                "job_id": staging_summary.get("job_id"),
                "summary": staging_summary,
                "issues": staging.get("issues", []),
                "network_paused": staging_summary.get(
                    "network_paused",
                    False,
                ),
                "network_pause_reason": (
                    staging_summary.get(
                        "network_pause_reason"
                    )
                ),
                "missed_post_ids": staging_summary.get(
                    "missed_post_ids",
                    [],
                ),
                "eagle_items_created": 0,
                "database_modified": False,
            }

            workflow["status"] = final_status
            workflow["result"] = result

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))
            return

        # -------------------------------------------------
        # STEP 3: EAGLE PREFLIGHT
        # -------------------------------------------------
        staging_job_id = str(
            staging_summary.get("job_id") or ""
        ).strip()

        if not staging_job_id:
            raise RuntimeError(
                "STAGING_READY_RESULT_HAS_NO_JOB_ID"
            )

        eagle_arguments = [
            "--job-id",
            staging_job_id,
            "--source-code",
            "instagram",
            "--start-number",
            str(resolved_start_number),
        ]

        if args.naming_manifest:
            eagle_arguments.extend([
                "--naming-manifest",
                args.naming_manifest,
            ])

        emit_progress(
            91,
            "Проверка импорта в Eagle",
        )

        preview = run_module(
            "app.eagle_import_staging",
            eagle_arguments,
            log_lines,
        )

        emit_progress(
            93,
            "Проверка Eagle завершена",
        )

        preview_data = preview.get("preview", {})

        workflow["steps"].append({
            "step": "EAGLE_PREFLIGHT",
            "status": preview.get("status"),
            "planned_items": preview_data.get(
                "planned_items"
            ),
            "blocked_posts": preview_data.get(
                "blocked_posts"
            ),
            "importable_items": preview_data.get(
                "importable_items"
            ),
        })

        if preview.get("status") != "PREVIEW_ONLY":
            raise RuntimeError(
                "Unexpected Eagle preflight status"
            )

        if int(
            preview_data.get("blocked_posts", 0)
        ) != 0:
            final_status = (
                "EAGLE_PREFLIGHT_BLOCKED"
            )

            result = {
                "status": final_status,
                "run_id": run_id,
                "preview": preview_data,
                "preflight": preview.get(
                    "preflight",
                    [],
                ),
                "eagle_items_created": 0,
                "database_modified": False,
            }

            workflow["status"] = final_status
            workflow["result"] = result

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))
            return

        # -------------------------------------------------
        # STEP 4: EAGLE IMPORT
        # -------------------------------------------------
        import_arguments = [
            "--commit",
            *eagle_arguments,
        ]

        emit_progress(
            94,
            "Добавление файлов в Eagle",
        )

        imported = run_module(
            "app.eagle_import_staging",
            import_arguments,
            log_lines,
        )

        emit_progress(
            98,
            "Файлы добавлены в Eagle",
        )

        workflow["steps"].append({
            "step": "EAGLE_IMPORT",
            "status": imported.get("status"),
            "planned_items": imported.get(
                "planned_items"
            ),
            "imported_items": imported.get(
                "imported_items"
            ),
            "remaining_items": imported.get(
                "remaining_items"
            ),
            "stop_reason": imported.get(
                "stop_reason"
            ),
        })

        if imported.get("status") != "BATCH_IMPORTED":
            final_status = (
                "IMPORT_PARTIAL_REQUIRES_REVIEW"
            )

            result = {
                "status": final_status,
                "run_id": run_id,
                "import": {
                    "planned_items": imported.get(
                        "planned_items"
                    ),
                    "imported_items": imported.get(
                        "imported_items"
                    ),
                    "remaining_items": imported.get(
                        "remaining_items"
                    ),
                    "stop_reason": imported.get(
                        "stop_reason"
                    ),
                    "created_eagle_ids": imported.get(
                        "created_eagle_ids",
                        [],
                    ),
                },
                "database_modified": False,
            }

            workflow["status"] = final_status
            workflow["result"] = result

            print(json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ))
            return

        # -------------------------------------------------
        # STEP 5: FINALIZE SQLITE REGISTRY
        # -------------------------------------------------
        emit_progress(
            99,
            "Обновление локального реестра",
        )

        finalized = run_module(
            "app.instagram_finalize_job",
            [],
            log_lines,
        )

        workflow["steps"].append({
            "step": "FINALIZE",
            "status": finalized.get("status"),
            "posts_registered": finalized.get(
                "posts_registered"
            ),
            "media_registered": finalized.get(
                "media_registered"
            ),
            "imports_registered": finalized.get(
                "imports_registered"
            ),
        })

        if finalized.get("status") != "FINALIZED":
            final_status = (
                "IMPORTED_BUT_FINALIZE_REQUIRES_REVIEW"
            )
        else:
            final_status = "SYNC_COMPLETED"

        cleanup_result = {
            "attempted": False,
            "staging_deleted": False,
            "source_files_deleted": 0,
            "staging_bytes_freed": 0,
            "cleanup_error": None,
        }
        compatible_cleanup = {
            "attempted": False,
            "compatible_jobs_deleted": [],
            "compatible_files_deleted": 0,
            "compatible_bytes_freed": 0,
            "cleanup_errors": [],
        }

        if final_status == "SYNC_COMPLETED":
            # Finalization has succeeded. Exact stopped duplicates
            # are now redundant because the same selected media has
            # been registered successfully.
            compatible_cleanup = (
                cleanup_compatible_stopped_staging(
                    staging_summary.get("job_id")
                )
            )

            cleanup_result = cleanup_registered_staging(
                staging_summary.get("job_id")
            )

            emit_progress(
                100,
                (
                    "Импорт завершён; временные файлы удалены"
                    if cleanup_result.get("staging_deleted")
                    else (
                        "Импорт завершён; временные файлы "
                        "сохранены для проверки"
                    )
                ),
            )

            print(json.dumps({
                "status": (
                    "STAGING_CLEANUP_COMPLETED"
                    if cleanup_result.get(
                        "staging_deleted"
                    )
                    else "STAGING_CLEANUP_WARNING"
                ),
                **cleanup_result,
            }, ensure_ascii=False, indent=2))

        result = {
            "status": final_status,
            "run_id": run_id,
            "job_id": staging_summary.get("job_id"),
            "new_posts": new_count,
            "downloaded_media": staging_summary.get(
                "downloaded_primary_media"
            ),
            "imported_items": imported.get(
                "imported_items"
            ),
            "created_eagle_ids": imported.get(
                "created_eagle_ids",
                [],
            ),
            "posts_registered": finalized.get(
                "posts_registered"
            ),
            "media_registered": finalized.get(
                "media_registered"
            ),
            "registry_totals": finalized.get(
                "registry_totals"
            ),
            "staging_deleted": cleanup_result.get(
                "staging_deleted",
                False,
            ),
            "source_files_deleted": cleanup_result.get(
                "source_files_deleted",
                0,
            ),
            "staging_bytes_freed": cleanup_result.get(
                "staging_bytes_freed",
                0,
            ),
            "staging_cleanup_error": cleanup_result.get(
                "cleanup_error"
            ),
            "compatible_stopped_jobs_deleted": (
                compatible_cleanup.get(
                    "compatible_jobs_deleted",
                    [],
                )
            ),
            "compatible_stopped_files_deleted": (
                compatible_cleanup.get(
                    "compatible_files_deleted",
                    0,
                )
            ),
            "compatible_stopped_bytes_freed": (
                compatible_cleanup.get(
                    "compatible_bytes_freed",
                    0,
                )
            ),
            "compatible_cleanup_errors": (
                compatible_cleanup.get(
                    "cleanup_errors",
                    [],
                )
            ),
        }

        workflow["status"] = final_status
        workflow["result"] = result

        print(json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ))

    except Exception as error:
        workflow["status"] = "SYNC_FAILED"
        workflow["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }

        print(json.dumps({
            "status": "SYNC_FAILED",
            "run_id": run_id,
            "error_type": type(error).__name__,
            "error": str(error),
            "note": (
                "Automatic continuation stopped. "
                "No automatic retry of Eagle write "
                "requests was performed."
            ),
        }, ensure_ascii=False, indent=2))

        raise

    finally:
        finished_at = datetime.now()

        workflow["finished_at"] = (
            finished_at.isoformat()
        )
        workflow["duration_seconds"] = round(
            (
                finished_at - started_at
            ).total_seconds(),
            2,
        )

        REPORTS.mkdir(
            parents=True,
            exist_ok=True,
        )
        LOGS.mkdir(
            parents=True,
            exist_ok=True,
        )

        report_path = (
            REPORTS
            / f"instagram_sync_{run_id}.json"
        )
        log_path = (
            LOGS
            / f"instagram_sync_{run_id}.log"
        )

        report_path.write_text(
            json.dumps(
                workflow,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        log_path.write_text(
            "\n".join(log_lines),
            encoding="utf-8",
        )

        print(
            f"\nReport: {report_path}\n"
            f"Log: {log_path}",
            flush=True,
        )


if __name__ == "__main__":
    main()
