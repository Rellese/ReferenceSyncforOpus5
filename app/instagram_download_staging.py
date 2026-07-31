from __future__ import annotations

from app.source_staging_contract import ensure_staging_contract

import argparse
import hashlib
import json
import re
import signal
import time
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.browser_cookie_source import (
    gallery_dl_browser_spec,
    public_browser_details,
)


PROJECT = Path(__file__).resolve().parents[1]
REPORTS = PROJECT / "reports"
DOWNLOADS = PROJECT / "downloads" / "instagram" / "incoming"
CONFIG_PATH = PROJECT / "config" / "local.json"

MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".mp4",
    ".mov",
}

SENSITIVE_PATTERNS = [
    re.compile(
        r"(sessionid\s*[=:]\s*)[^;\s,\"']+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(csrftoken\s*[=:]\s*)[^;\s,\"']+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(ds_user_id\s*[=:]\s*)[^;\s,\"']+",
        re.IGNORECASE,
    ),
]


def redact(text: str) -> str:
    result = text

    for pattern in SENSITIVE_PATTERNS:
        result = pattern.sub(r"\1<REDACTED>", result)

    return result


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}

    return json.loads(
        CONFIG_PATH.read_text(encoding="utf-8")
    )


def latest_discovery_report() -> Path:
    pattern = re.compile(
        r"^instagram_discovery_\d{8}_\d{6}\.json$"
    )

    candidates = [
        path
        for path in REPORTS.glob(
            "instagram_discovery_*.json"
        )
        if pattern.match(path.name)
    ]

    candidates.sort(
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        raise FileNotFoundError(
            "No Instagram discovery report found"
        )

    return candidates[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def emit_progress(
    percent: int,
    stage: str,
    **details: Any,
) -> None:
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


def validate_video(
    ffmpeg: str | None,
    path: Path,
) -> dict[str, Any]:
    if not ffmpeg:
        return {
            "status": "FFMPEG_NOT_FOUND",
            "valid": None,
            "returncode": None,
        }

    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=10 * 60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "VALIDATION_TIMEOUT",
            "valid": False,
            "returncode": None,
        }

    stderr = redact(process.stderr)

    return {
        "status": (
            "VALID"
            if process.returncode == 0
            else "INVALID"
        ),
        "valid": process.returncode == 0,
        "returncode": process.returncode,
        "error": stderr[-2000:] if stderr else None,
    }


def read_sidecar(
    media_path: Path,
) -> tuple[Path, dict[str, Any] | None, str | None]:
    sidecar_path = Path(f"{media_path}.json")

    if not sidecar_path.is_file():
        return sidecar_path, None, "SIDECAR_MISSING"

    try:
        metadata = json.loads(
            sidecar_path.read_text(encoding="utf-8")
        )
    except Exception as error:
        return (
            sidecar_path,
            None,
            f"SIDECAR_INVALID: {error}",
        )

    if not isinstance(metadata, dict):
        return sidecar_path, None, "SIDECAR_NOT_OBJECT"

    return sidecar_path, metadata, None


def inspect_downloaded_media(
    media_directory: Path,
    ffmpeg: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for media_path in sorted(media_directory.rglob("*")):
        if not media_path.is_file():
            continue

        if media_path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue

        if ".fdash-" in media_path.name.lower():
            issues.append({
                "type": "AUXILIARY_DASH_FILE",
                "path": str(media_path),
            })
            continue

        sidecar_path, metadata, sidecar_error = (
            read_sidecar(media_path)
        )

        if sidecar_error:
            issues.append({
                "type": sidecar_error,
                "path": str(media_path),
                "sidecar": str(sidecar_path),
            })

        metadata = metadata or {}

        post_id = str(
            metadata.get("post_id") or ""
        ).strip()

        media_id = str(
            metadata.get("media_id") or ""
        ).strip()

        shortcode = str(
            metadata.get("post_shortcode")
            or metadata.get("sidecar_shortcode")
            or ""
        ).strip()

        raw_num = metadata.get("num")
        try:
            component_index = int(raw_num)
        except (TypeError, ValueError):
            component_index = None

        video_validation = None

        if media_path.suffix.lower() in {".mp4", ".mov"}:
            video_validation = validate_video(
                ffmpeg,
                media_path,
            )

            if video_validation.get("valid") is False:
                issues.append({
                    "type": "VIDEO_VALIDATION_FAILED",
                    "path": str(media_path),
                    "validation": video_validation,
                })

        if not post_id:
            issues.append({
                "type": "POST_ID_MISSING",
                "path": str(media_path),
            })

        if not media_id:
            issues.append({
                "type": "MEDIA_ID_MISSING",
                "path": str(media_path),
            })

        records.append({
            "post_id": post_id or None,
            "media_id": media_id or None,
            "post_shortcode": shortcode or None,
            "component_index": component_index,
            "filename": media_path.name,
            "extension": (
                media_path.suffix.lower().lstrip(".")
            ),
            "size": media_path.stat().st_size,
            "sha256": sha256_file(media_path),
            "local_path": str(media_path),
            "sidecar_path": str(sidecar_path),
            "sidecar_valid": sidecar_error is None,
            "video_validation": video_validation,
        })

    return records, issues



# DOWNLOAD_SPEED_AND_VPN_SAFETY_V1
SPEED_PROFILES = {
    "safe": {
        "label": "Безопасный",
        # External V6.4.8 retry loop is authoritative.
        # Long gallery-dl internal retries prevented the GUI
        # countdown from appearing after VPN loss.
        "retries": "0",
        "http_timeout": "12",
        "sleep": "2.5-5.0",
        "sleep_request": "1.5-3.0",
        "sleep_extractor": "3.0-6.0",
        "sleep_retries": "exp=10",
        "sleep_429": "exp=60",
        "limit_rate": "700k-1.5M",
    },
    "balanced": {
        "label": "Сбалансированный",
        "retries": "0",
        "http_timeout": "12",
        "sleep": "1.0-2.5",
        "sleep_request": "0.6-1.5",
        "sleep_extractor": "1.5-3.0",
        "sleep_retries": "exp=6",
        "sleep_429": "exp=45",
        "limit_rate": "1.5M-3M",
    },
}


def save_job_state(
    job_file: Path,
    payload: dict[str, Any],
) -> None:
    """Atomically persist the current resumable job state."""

    payload = ensure_staging_contract(
        payload,
        default_source_code="instagram",
        default_tags=["Instagram"],
        default_folder_ids=["MRWRIOJO42ER5"],
        default_name_marker="instpoporder",
    )

    temporary = job_file.with_suffix(".json.tmp")

    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temporary.replace(job_file)


def normalize_subprocess_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    return str(value)


def classify_download_failure(
    returncode: int,
    stdout: str,
    stderr: str,
) -> str | None:
    if returncode == 0:
        return None

    combined = f"{stdout}\n{stderr}".lower()

    rate_limit_patterns = (
        "429",
        "too many requests",
        "rate limit",
        "ratelimit",
        "temporarily blocked",
        "please wait a few minutes",
    )

    network_patterns = (
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "connection refused",
        "connection error",
        "network is unreachable",
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname provided",
        "failed to establish a new connection",
        "remote end closed connection",
        "server disconnected",
        "no route to host",
        "ssl error",
        "unexpected eof",
    )

    if any(pattern in combined for pattern in rate_limit_patterns):
        return "RATE_LIMIT"

    if any(pattern in combined for pattern in network_patterns):
        return "NETWORK_ERROR"

    authentication_patterns = (
        "accounts/login",
        "redirect to login page",
        "login required",
        "not logged in",
        "no session cookies",
        "database disk image is malformed",
        "cookies: database",
        "cookie database",
    )

    if any(
        pattern in combined
        for pattern in authentication_patterns
    ):
        return "AUTH_REQUIRED"

    if "403" in combined or "forbidden" in combined:
        return "ACCESS_BLOCKED"

    return "COMMAND_ERROR"




# V6.4.6_RESTORE_CACHE_REUSE
def _component_identity_map(
    post: dict[str, Any],
) -> dict[int, str]:
    components = post.get("component_items")

    if not isinstance(components, list):
        components = post.get("components")

    if not isinstance(components, list):
        components = []

    result: dict[int, str] = {}

    for fallback_index, component in enumerate(
        components,
        start=1,
    ):
        if not isinstance(component, dict):
            continue

        try:
            component_index = int(
                component.get("component_index")
                or component.get("num")
                or fallback_index
            )
        except (TypeError, ValueError):
            continue

        media_id = str(
            component.get("media_id")
            or component.get("id")
            or component.get("pk")
            or ""
        ).strip()

        if component_index > 0 and media_id:
            result[component_index] = media_id

    return result



def _cached_component_candidates(
    *,
    post_id: str,
    media_id: str,
    destination_directory: Path,
) -> list[Path]:
    """
    Find components by sidecar identity, not by filename.

    A valid component already inside the active resumed job always
    takes priority. Only when it is absent are older jobs searched.
    """
    local_matches = []
    external_matches = []

    for media_directory in DOWNLOADS.glob("*/media"):
        if not media_directory.is_dir():
            continue

        try:
            is_destination = (
                media_directory.resolve()
                == destination_directory.resolve()
            )
        except OSError:
            continue

        for path in media_directory.rglob("*"):
            if (
                not path.is_file()
                or path.suffix.lower()
                not in MEDIA_EXTENSIONS
                or ".fdash-" in path.name.lower()
            ):
                continue

            identity = _sidecar_identity(path)

            if identity is None:
                continue

            metadata_post_id, metadata_media_id, _ = identity

            if (
                metadata_post_id != post_id
                or metadata_media_id != media_id
            ):
                continue

            if is_destination:
                local_matches.append(path)
            else:
                external_matches.append(path)

    if local_matches:
        return sorted(local_matches)

    return sorted(external_matches)


def reuse_cached_components(
    *,
    post: dict[str, Any],
    selected_components: list[int],
    destination_directory: Path,
) -> dict[str, Any]:
    """
    Copy verified media+sidecar pairs from previous staging jobs.

    Conflicting copies of one media_id are rejected instead of
    choosing one silently.
    """
    post_id = str(post.get("post_id") or "").strip()
    identity_map = _component_identity_map(post)

    reused = []
    missing = []

    for component_index in selected_components:
        media_id = identity_map.get(component_index)

        if not media_id:
            missing.append(component_index)
            continue

        candidates = _cached_component_candidates(
            post_id=post_id,
            media_id=media_id,
            destination_directory=destination_directory,
        )

        verified = []

        for source_path in candidates:
            sidecar_path = Path(f"{source_path}.json")

            if not sidecar_path.is_file():
                continue

            try:
                metadata = json.loads(
                    sidecar_path.read_text(
                        encoding="utf-8"
                    )
                )
            except Exception:
                continue

            if not isinstance(metadata, dict):
                continue

            metadata_post_id = str(
                metadata.get("post_id") or ""
            ).strip()
            metadata_media_id = str(
                metadata.get("media_id") or ""
            ).strip()

            try:
                metadata_index = int(
                    metadata.get("num")
                )
            except (TypeError, ValueError):
                metadata_index = component_index

            if (
                metadata_post_id != post_id
                or metadata_media_id != media_id
                or metadata_index != component_index
            ):
                continue

            verified.append({
                "media": source_path,
                "sidecar": sidecar_path,
                "sha256": sha256_file(source_path),
            })

        if not verified:
            missing.append(component_index)
            continue

        hashes = {
            item["sha256"]
            for item in verified
        }

        if len(hashes) != 1:
            raise RuntimeError(
                "CACHE_COMPONENT_CONTENT_CONFLICT: "
                f"post={post_id}, "
                f"component={component_index}, "
                f"media_id={media_id}"
            )

        selected_source = max(
            verified,
            key=lambda item: (
                item["media"].stat().st_mtime,
                str(item["media"]),
            ),
        )

        destination_media = (
            destination_directory
            / selected_source["media"].name
        )
        destination_sidecar = Path(
            f"{destination_media}.json"
        )

        if destination_media.exists():
            if (
                sha256_file(destination_media)
                != selected_source["sha256"]
            ):
                raise RuntimeError(
                    "CACHE_DESTINATION_CONFLICT: "
                    f"{destination_media}"
                )
        else:
            shutil.copy2(
                selected_source["media"],
                destination_media,
            )

        if destination_sidecar.exists():
            existing_metadata = json.loads(
                destination_sidecar.read_text(
                    encoding="utf-8"
                )
            )

            if (
                str(existing_metadata.get("post_id") or "")
                != post_id
                or str(
                    existing_metadata.get("media_id") or ""
                )
                != media_id
            ):
                raise RuntimeError(
                    "CACHE_SIDECAR_DESTINATION_CONFLICT: "
                    f"{destination_sidecar}"
                )
        else:
            shutil.copy2(
                selected_source["sidecar"],
                destination_sidecar,
            )

        if (
            sha256_file(destination_media)
            != selected_source["sha256"]
        ):
            raise RuntimeError(
                "CACHE_COPY_HASH_MISMATCH: "
                f"{destination_media}"
            )

        reused.append({
            "post_id": post_id,
            "component_index": component_index,
            "media_id": media_id,
            "source_path": str(
                selected_source["media"]
            ),
            "destination_path": str(
                destination_media
            ),
            "sha256": selected_source["sha256"],
        })

    return {
        "reused": reused,
        "reused_component_numbers": sorted(
            item["component_index"]
            for item in reused
        ),
        "missing_component_numbers": sorted(
            missing
        ),
    }


# STALE_DISCOVERY_SELECTED_POST_RECOVERY_V1
from app.instagram_candidate_recovery import (
    recover_selected_posts,
)



# V6.4.8 STAGE6 TRUE_RESUME_HELPERS
def _selected_job_signature(
    posts: list[dict[str, Any]],
    selected_by_post: dict[str, list[int]] | None = None,
) -> str:
    """
    Build a stable signature from the exact selected post IDs and
    component indexes. Cookie values and local paths are excluded.
    """
    rows = []

    for post in posts:
        if not isinstance(post, dict):
            continue

        post_id = str(post.get("post_id") or "").strip()

        if not post_id:
            continue

        if selected_by_post is not None:
            raw_components = selected_by_post.get(
                post_id,
                [],
            )
        else:
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


def _load_resumable_job(
    *,
    requested_job_id: str | None,
    current_posts: list[dict[str, Any]],
    selected_by_post: dict[str, list[int]],
) -> tuple[Path | None, dict[str, Any] | None]:
    """
    Find only an exact STOPPED_BY_USER job.

    Explicit --resume-job never falls back silently to another job.
    Automatic mode selects the newest exact match.
    """
    expected_signature = _selected_job_signature(
        current_posts,
        selected_by_post,
    )

    candidates = []

    if requested_job_id:
        normalized = str(requested_job_id).strip()

        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or not normalized.startswith("instagram_")
        ):
            raise RuntimeError(
                "INVALID_RESUME_JOB_ID"
            )

        candidate_directory = DOWNLOADS / normalized

        try:
            candidate_directory.resolve().relative_to(
                DOWNLOADS.resolve()
            )
        except ValueError as error:
            raise RuntimeError(
                "RESUME_JOB_OUTSIDE_STAGING"
            ) from error

        candidates = [candidate_directory]
    else:
        candidates = sorted(
            (
                path
                for path in DOWNLOADS.glob("instagram_*")
                if path.is_dir()
            ),
            key=lambda path: (
                path.stat().st_mtime,
                path.name,
            ),
            reverse=True,
        )

    for candidate_directory in candidates:
        job_file = candidate_directory / "job.json"

        if not job_file.is_file():
            if requested_job_id:
                raise RuntimeError(
                    "RESUME_JOB_JSON_NOT_FOUND: "
                    + str(job_file)
                )
            continue

        try:
            payload = json.loads(
                job_file.read_text(encoding="utf-8")
            )
        except Exception as error:
            if requested_job_id:
                raise RuntimeError(
                    "RESUME_JOB_JSON_INVALID: "
                    + str(error)
                ) from error
            continue

        if not isinstance(payload, dict):
            if requested_job_id:
                raise RuntimeError(
                    "RESUME_JOB_NOT_OBJECT"
                )
            continue

        payload_job_id = str(
            payload.get("job_id") or ""
        ).strip()

        if payload_job_id != candidate_directory.name:
            if requested_job_id:
                raise RuntimeError(
                    "RESUME_JOB_ID_MISMATCH"
                )
            continue

        if payload.get("status") != "STOPPED_BY_USER":
            if requested_job_id:
                raise RuntimeError(
                    "RESUME_JOB_NOT_STOPPED: "
                    + str(payload.get("status"))
                )
            continue

        old_posts = payload.get("posts")

        if not isinstance(old_posts, list):
            if requested_job_id:
                raise RuntimeError(
                    "RESUME_JOB_POSTS_INVALID"
                )
            continue

        old_signature = _selected_job_signature(
            old_posts,
            None,
        )

        if old_signature != expected_signature:
            if requested_job_id:
                raise RuntimeError(
                    "RESUME_JOB_SELECTION_MISMATCH"
                )
            continue

        return candidate_directory, payload

    if requested_job_id:
        raise RuntimeError(
            "REQUESTED_RESUME_JOB_NOT_FOUND"
        )

    return None, None


def _safe_discard_generated_job(
    generated_directory: Path,
) -> None:
    """
    A fresh job directory may have been initialized before an old
    resumable job was located. Delete it only when it contains no files.
    """
    if not generated_directory.exists():
        return

    files = [
        path
        for path in generated_directory.rglob("*")
        if path.is_file()
    ]

    if files:
        raise RuntimeError(
            "GENERATED_JOB_NOT_EMPTY; refusing deletion: "
            + str(generated_directory)
        )

    shutil.rmtree(generated_directory)


def _sidecar_identity(
    media_path: Path,
) -> tuple[str, str, int | None] | None:
    sidecar_path = Path(f"{media_path}.json")

    if not sidecar_path.is_file():
        return None

    try:
        metadata = json.loads(
            sidecar_path.read_text(encoding="utf-8")
        )
    except Exception:
        return None

    if not isinstance(metadata, dict):
        return None

    post_id = str(
        metadata.get("post_id") or ""
    ).strip()
    media_id = str(
        metadata.get("media_id") or ""
    ).strip()

    try:
        component_index = int(metadata.get("num"))
    except (TypeError, ValueError):
        component_index = None

    if not post_id or not media_id:
        return None

    return post_id, media_id, component_index


def _ready_post_ids_in_directory(
    *,
    posts: list[dict[str, Any]],
    selected_by_post: dict[str, list[int]],
    media_directory: Path,
) -> set[str]:
    """
    Count fully ready publications in the active job without relying
    on filenames.
    """
    available: dict[
        tuple[str, str, int],
        list[Path],
    ] = {}

    if media_directory.is_dir():
        for media_path in media_directory.rglob("*"):
            if (
                not media_path.is_file()
                or media_path.suffix.lower()
                not in MEDIA_EXTENSIONS
                or ".fdash-" in media_path.name.lower()
            ):
                continue

            identity = _sidecar_identity(media_path)

            if identity is None:
                continue

            post_id, media_id, component_index = identity

            if component_index is None:
                continue

            available.setdefault(
                (post_id, media_id, component_index),
                [],
            ).append(media_path)

    ready_posts = set()

    for post in posts:
        post_id = str(post.get("post_id") or "").strip()

        if not post_id:
            continue

        identity_map = _component_identity_map(post)
        selected_components = selected_by_post.get(
            post_id,
            [],
        )

        if not selected_components:
            continue

        complete = True

        for component_index in selected_components:
            media_id = identity_map.get(component_index)

            if not media_id:
                complete = False
                break

            matches = available.get(
                (post_id, media_id, component_index),
                [],
            )

            if not matches:
                complete = False
                break

            hashes = {
                sha256_file(path)
                for path in matches
            }

            if len(hashes) != 1:
                raise RuntimeError(
                    "RESUME_LOCAL_COMPONENT_CONFLICT: "
                    f"post={post_id}, "
                    f"component={component_index}"
                )

        if complete:
            ready_posts.add(post_id)

    return ready_posts


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download discovered Instagram posts into "
            "an isolated staging directory."
        )
    )
    parser.add_argument(
        "--browser",
        default=None,
    )
    parser.add_argument(
        "--speed-profile",
        choices=sorted(SPEED_PROFILES),
        default="safe",
        help=(
            "Download pacing profile: safe or balanced"
        ),
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=None,
        help="Optional maximum number of posts",
    )
    parser.add_argument(
        "--post-id",
        dest="post_ids",
        action="append",
        default=[],
        help=(
            "Download the specified discovered post ID. "
            "May be supplied multiple times."
        ),
    )
    parser.add_argument(
        "--selection-manifest",
        default=None,
        help=(
            "GUI manifest containing selected carousel "
            "component indexes"
        ),
    )

    parser.add_argument(
        "--discovery-manifest",
        default=None,
        help=(
            "Explicit saved discovery snapshot. This prevents "
            "selection from drifting to a newer discovery report."
        ),
    )
    parser.add_argument(
        "--resume-job",
        default=None,
        help=(
            "Continue an exact STOPPED_BY_USER staging job. "
            "If omitted, the newest exact matching stopped job "
            "is selected automatically."
        ),
    )

    args = parser.parse_args()

    stop_requested = False

    def request_graceful_stop(
        _signum,
        _frame,
    ) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(
        signal.SIGTERM,
        request_graceful_stop,
    )
    signal.signal(
        signal.SIGINT,
        request_graceful_stop,
    )

    speed_profile = SPEED_PROFILES[
        args.speed_profile
    ]

    selection_posts: dict[str, dict[str, Any]] = {}

    if args.selection_manifest:
        selection_path = Path(
            args.selection_manifest
        ).expanduser().resolve()

        if not selection_path.is_file():
            raise FileNotFoundError(selection_path)

        selection_payload = json.loads(
            selection_path.read_text(encoding="utf-8")
        )

        raw_selection_posts = selection_payload.get(
            "posts"
        )

        if not isinstance(raw_selection_posts, dict):
            raise SystemExit(
                "Selection manifest must contain a posts object"
            )

        for raw_post_id, entry in (
            raw_selection_posts.items()
        ):
            post_id = str(raw_post_id).strip()

            if not post_id or not isinstance(entry, dict):
                raise SystemExit(
                    "Invalid post entry in selection manifest"
                )

            raw_indexes = entry.get(
                "selected_components"
            )

            if not isinstance(raw_indexes, list):
                raise SystemExit(
                    f"Post {post_id} does not contain "
                    "selected_components"
                )

            indexes = []

            for raw_index in raw_indexes:
                try:
                    component_index = int(raw_index)
                except (TypeError, ValueError):
                    raise SystemExit(
                        f"Invalid component index for "
                        f"post {post_id}: {raw_index!r}"
                    )

                if component_index < 1:
                    raise SystemExit(
                        f"Component indexes must be positive "
                        f"for post {post_id}"
                    )

                if component_index not in indexes:
                    indexes.append(component_index)

            if not indexes:
                raise SystemExit(
                    f"No components selected for post {post_id}"
                )

            normalized_entry = dict(entry)
            normalized_entry[
                "selected_components"
            ] = sorted(indexes)
            selection_posts[post_id] = normalized_entry

    config = load_config()
    browser = (
        args.browser
        or config.get("browser")
        or "chrome"
    )

    browser_cookie_source = (
        gallery_dl_browser_spec(browser)
    )
    browser_details = public_browser_details(
        browser
    )

    gallery_dl = shutil.which("gallery-dl")
    ffmpeg = shutil.which("ffmpeg")

    if not gallery_dl:
        raise FileNotFoundError(
            "gallery-dl executable not found"
        )

    if args.discovery_manifest:
        discovery_path = Path(
            args.discovery_manifest
        ).expanduser().resolve()

        if not discovery_path.is_file():
            raise FileNotFoundError(discovery_path)

        discovery = json.loads(
            discovery_path.read_text(encoding="utf-8")
        )

        raw_discovered_posts = discovery.get(
            "discovery_posts"
        )

        if not isinstance(raw_discovered_posts, list):
            raise SystemExit(
                "Discovery manifest must contain "
                "a discovery_posts list"
            )

        discovered_posts = [
            dict(post)
            for post in raw_discovered_posts
            if isinstance(post, dict)
        ]

        for post in discovered_posts:
            components = post.get("components")

            if not isinstance(components, list):
                components = post.get(
                    "component_items",
                    [],
                )

            if not isinstance(components, list):
                components = []

            post["components"] = components
            post["component_items"] = components
            post["component_count_returned"] = int(
                post.get("component_count_returned")
                or post.get("component_count")
                or len(components)
                or 1
            )
            post["discovery_status"] = (
                post.get("discovery_status")
                or "NEW_POST_CANDIDATE"
            )
    else:
        discovery_path = latest_discovery_report()
        discovery = json.loads(
            discovery_path.read_text(encoding="utf-8")
        )

        discovered_posts = [
            post
            for post in discovery.get("posts", [])
            if post.get("discovery_status")
            in {
                "NEW_POST_CANDIDATE",
                "RESTORE_DELETED_POST",
            }
        ]

    if args.post_ids:
        (
            recovered_selected_posts,
            recovered_download_ids,
        ) = recover_selected_posts(
            discovered_posts,
            args.post_ids,
            REPORTS,
            selection_posts,
        )

        current_ids = {
            str(post.get("post_id") or "")
            for post in discovered_posts
            if isinstance(post, dict)
        }

        for recovered_post in recovered_selected_posts:
            recovered_id = str(
                recovered_post.get("post_id") or ""
            )

            if recovered_id not in current_ids:
                discovered_posts.append(recovered_post)
                current_ids.add(recovered_id)

        if recovered_download_ids:
            print(json.dumps({
                "status": (
                    "DOWNLOAD_POSTS_RECOVERED_FROM_"
                    "DISCOVERY_HISTORY"
                ),
                "post_ids": recovered_download_ids,
                "eagle_items_created": 0,
                "database_modified": False,
            }, ensure_ascii=False, indent=2), file=__import__("sys").stderr)

    if args.post_ids:
        requested_post_ids = []
        seen_post_ids = set()

        for raw_post_id in args.post_ids:
            post_id = str(raw_post_id).strip()

            if not post_id or not post_id.isdigit():
                raise SystemExit(
                    "--post-id must contain a numeric "
                    "Instagram post ID"
                )

            if post_id in seen_post_ids:
                continue

            seen_post_ids.add(post_id)
            requested_post_ids.append(post_id)

        discovered_by_id = {
            str(post.get("post_id")): post
            for post in discovered_posts
        }

        missing_post_ids = [
            post_id
            for post_id in requested_post_ids
            if post_id not in discovered_by_id
        ]

        if missing_post_ids:
            raise SystemExit(
                "Requested post IDs were not found in the "
                "current discovery report: "
                + ", ".join(missing_post_ids)
            )

        discovered_posts = [
            discovered_by_id[post_id]
            for post_id in requested_post_ids
        ]

    if args.max_posts is not None:
        if args.max_posts < 1:
            raise SystemExit(
                "--max-posts must be greater than zero"
            )

        discovered_posts = discovered_posts[
            :args.max_posts
        ]

    if not discovered_posts:
        raise SystemExit(
            "No NEW_POST_CANDIDATE records found"
        )

    job_id = (
        "instagram_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )

    job_directory = DOWNLOADS / job_id
    media_directory = job_directory / "media"
    logs_directory = job_directory / "logs"

    media_directory.mkdir(parents=True, exist_ok=False)
    logs_directory.mkdir(parents=True, exist_ok=True)

    expected_by_post: dict[str, int] = {}
    selected_by_post: dict[str, list[int]] = {}

    for post in discovered_posts:
        post_id = str(post["post_id"])
        total_components = int(
            post.get("component_count_returned") or 0
        )

        manifest_entry = selection_posts.get(post_id)

        if manifest_entry is not None:
            selected_components = list(
                manifest_entry["selected_components"]
            )
        else:
            selected_components = list(
                range(1, total_components + 1)
            )

        if not selected_components:
            raise SystemExit(
                f"No components selected for post {post_id}"
            )

        invalid_components = [
            component
            for component in selected_components
            if (
                component < 1
                or (
                    total_components > 0
                    and component > total_components
                )
            )
        ]

        if invalid_components:
            raise SystemExit(
                f"Selected components are outside the "
                f"carousel range for post {post_id}: "
                f"{invalid_components}"
            )

        selected_by_post[post_id] = (
            selected_components
        )
        expected_by_post[post_id] = len(
            selected_components
        )

        post["selected_components"] = (
            selected_components
        )
        post["total_component_count"] = (
            total_components
        )

    # V6.4.8 STAGE6 TRUE_RESUME_JOB_SELECTION
    generated_job_directory = job_directory

    (
        resumed_job_directory,
        resumed_job_state,
    ) = _load_resumable_job(
        requested_job_id=args.resume_job,
        current_posts=discovered_posts,
        selected_by_post=selected_by_post,
    )

    resumed_existing_job = (
        resumed_job_directory is not None
        and resumed_job_state is not None
    )

    if resumed_existing_job:
        _safe_discard_generated_job(
            generated_job_directory
        )

        job_directory = resumed_job_directory
        job_id = job_directory.name
        media_directory = job_directory / "media"
        logs_directory = job_directory / "logs"

        media_directory.mkdir(
            parents=True,
            exist_ok=True,
        )
        logs_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        job_file = job_directory / "job.json"
        job_state = dict(resumed_job_state)

        previous_results = job_state.get(
            "download_results",
            [],
        )

        resume_history = job_state.get(
            "resume_history",
            [],
        )

        if not isinstance(resume_history, list):
            resume_history = []

        resume_history.append({
            "resumed_at": datetime.now().isoformat(),
            "previous_status": job_state.get("status"),
            "previous_download_results": (
                len(previous_results)
                if isinstance(previous_results, list)
                else 0
            ),
        })

        job_state.update({
            "job_id": job_id,
            "status": "DOWNLOADING",
            "posts": discovered_posts,
            "browser": browser,
            "speed_profile": args.speed_profile,
            "speed_profile_settings": dict(speed_profile),
            "download_results": [],
            "cache_reuse_results": [],
            "network_pause": None,
            "remaining_post_ids": [
                str(post.get("post_id") or "")
                for post in discovered_posts
            ],
            "resume_history": resume_history,
            "resumed_from_same_job": True,
            "last_resumed_at": (
                datetime.now().isoformat()
            ),
        })
    else:
        job_state = {
            "job_id": job_id,
            "created_at": datetime.now().isoformat(),
            "status": "DOWNLOADING",
            "source_discovery_report": str(discovery_path),
            "browser": browser,
            "speed_profile": args.speed_profile,
            "speed_profile_settings": dict(speed_profile),
            "posts": discovered_posts,
            "download_results": [],
            "network_pause": None,
            "resume_history": [],
            "resumed_from_same_job": False,
        }

        job_file = job_directory / "job.json"

    save_job_state(job_file, job_state)

    # V6.4.8 COOKIE_SNAPSHOT_ONCE_PER_BATCH
    #
    # Read the live browser cookie database only once. Every
    # subsequent gallery-dl invocation uses this private Netscape
    # snapshot, avoiding repeated access to browser SQLite.
    cookie_snapshot = (
        job_directory
        / "cookies.instagram.txt"
    )

    if cookie_snapshot.is_file():
        cookie_snapshot.unlink()

    first_post_url = next(
        (
            str(post.get("post_url") or "").strip()
            for post in discovered_posts
            if str(post.get("post_url") or "").strip()
        ),
        None,
    )

    if not first_post_url:
        job_state["status"] = "AUTH_REQUIRED"
        job_state["authentication"] = {
            "status": "POST_URL_UNAVAILABLE",
            "cookie_snapshot_created": False,
        }
        save_job_state(job_file, job_state)
        raise RuntimeError(
            "AUTH_REQUIRED: no Instagram URL is available "
            "for cookie snapshot validation"
        )

    cookie_export_command = [
        gallery_dl,
        "--config-ignore",
        "--no-input",
        "--cookies-from-browser",
        browser_cookie_source,
        "--cookies-export",
        str(cookie_snapshot),
        "--no-download",
        first_post_url,
    ]

    emit_progress(
        13,
        "Подготовка сеанса Instagram — поиск не выполняется",
        state="PREPARING_COOKIE_SNAPSHOT",
    )

    try:
        cookie_export = subprocess.run(
            cookie_export_command,
            cwd=PROJECT,
            capture_output=True,
            text=True,
            timeout=5 * 60,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        cookie_export = subprocess.CompletedProcess(
            cookie_export_command,
            124,
            stdout=normalize_subprocess_text(
                error.stdout
            ),
            stderr=(
                normalize_subprocess_text(
                    error.stderr
                )
                + "\nCOOKIE_SNAPSHOT_TIMEOUT"
            ),
        )

    cookie_export_stdout = redact(
        normalize_subprocess_text(
            cookie_export.stdout
        )
    )
    cookie_export_stderr = redact(
        normalize_subprocess_text(
            cookie_export.stderr
        )
    )

    cookie_snapshot_ready = (
        cookie_export.returncode == 0
        and cookie_snapshot.is_file()
        and cookie_snapshot.stat().st_size > 0
    )

    if cookie_snapshot.is_file():
        cookie_snapshot.chmod(0o600)

    job_state["cookie_snapshot"] = {
        "status": (
            "READY"
            if cookie_snapshot_ready
            else "AUTH_REQUIRED"
        ),
        "path": (
            str(cookie_snapshot)
            if cookie_snapshot_ready
            else None
        ),
        "created_once_for_batch": True,
        "cookie_values_saved_to_job_json": False,
        "permissions": (
            "0600"
            if cookie_snapshot_ready
            else None
        ),
    }

    if not cookie_snapshot_ready:
        job_state["status"] = "AUTH_REQUIRED"
        job_state["authentication"] = {
            "status": "AUTH_REQUIRED",
            "cookie_snapshot_created": False,
            "browser": browser,
            "error": (
                cookie_export_stderr[-2000:]
                or cookie_export_stdout[-2000:]
                or "COOKIE_SNAPSHOT_FAILED"
            ),
        }
        save_job_state(job_file, job_state)

        if cookie_snapshot.is_file():
            cookie_snapshot.unlink()

        raise RuntimeError(
            "AUTH_REQUIRED: browser cookies could not be "
            "read safely. Open Instagram in the selected "
            "browser, sign in, and retry."
        )

    save_job_state(job_file, job_state)

    emit_progress(
        4,
        "Сеанс Instagram подготовлен",
        state="COOKIE_SNAPSHOT_READY",
    )

    download_results = []
    cache_reuse_results = []
    network_paused = False
    network_pause_reason = None
    stopped_by_user = False

    total_posts = len(discovered_posts)

    resume_ready_post_ids = _ready_post_ids_in_directory(
        posts=discovered_posts,
        selected_by_post=selected_by_post,
        media_directory=media_directory,
    )
    completed_post_ids = set(
        resume_ready_post_ids
    )

    initial_download_percent = 5 + int(
        (
            len(completed_post_ids)
            / max(1, total_posts)
        )
        * 80
    )

    emit_progress(
        initial_download_percent,
        (
            "Продолжаем загрузку"
            if resumed_existing_job
            else "Начинаем скачивание"
        ),
        current=len(completed_post_ids),
        total=total_posts,
        state=(
            "RESUMING_EXISTING_JOB"
            if resumed_existing_job
            else None
        ),
    )

    for index, post in enumerate(
        discovered_posts,
        start=1,
    ):
        post_id = str(post["post_id"])
        shortcode = post.get("post_shortcode")
        post_url = post.get("post_url")

        before_percent = max(
            initial_download_percent,
            5 + int(
                (
                    len(completed_post_ids)
                    / max(1, total_posts)
                )
                * 80
            ),
        )

        username = str(
            post.get("username")
            or post.get("owner_username")
            or ""
        ).strip()

        stage_name = (
            f"Скачивание {username}"
            if username
            else f"Скачивание публикации {index}"
        )

        emit_progress(
            before_percent,
            stage_name,
            current=len(completed_post_ids),
            total=total_posts,
            post_id=post_id,
        )

        if not post_url:
            download_results.append({
                "post_id": post_id,
                "shortcode": shortcode,
                "status": "URL_MISSING",
                "returncode": None,
            })
            continue

        selected_components = selected_by_post[
            post_id
        ]

        cache_result = reuse_cached_components(
            post=post,
            selected_components=selected_components,
            destination_directory=media_directory,
        )
        cache_reuse_results.extend(
            cache_result["reused"]
        )

        download_components = list(
            cache_result[
                "missing_component_numbers"
            ]
        )

        command = [
            gallery_dl,
            "--config-ignore",
            "--no-input",
            "--cookies",
            str(cookie_snapshot),
            "--directory",
            str(media_directory),
            "--filename",
            (
                "{sidecar_media_id:?/_/}"
                "{media_id}.{extension}"
            ),
            "--write-metadata",
            "--retries",
            speed_profile["retries"],
            "--http-timeout",
            speed_profile["http_timeout"],
            "--sleep",
            speed_profile["sleep"],
            "--sleep-request",
            speed_profile["sleep_request"],
            "--sleep-extractor",
            speed_profile["sleep_extractor"],
            "--sleep-retries",
            speed_profile["sleep_retries"],
            "--sleep-429",
            speed_profile["sleep_429"],
            "--limit-rate",
            speed_profile["limit_rate"],
        ]

        total_components = int(
            post.get("total_component_count")
            or post.get("component_count_returned")
            or 0
        )

        if (
            total_components > 1
            and len(download_components)
            < total_components
        ):
            if len(download_components) == 1:
                filter_tuple = (
                    f"({download_components[0]},)"
                )
            else:
                filter_tuple = (
                    "("
                    + ", ".join(
                        str(component)
                        for component in download_components
                    )
                    + ")"
                )

            command.extend([
                "--filter",
                f"num in {filter_tuple}",
            ])

        started = datetime.now()
        timed_out = False
        retry_attempt = 0
        retry_delays = [2, 5, 10, 20]

        while True:
            timed_out = False

            if stop_requested:
                process = subprocess.CompletedProcess(
                    command,
                    130,
                    stdout="",
                    stderr="USER_STOP_REQUESTED",
                )
                break

            if download_components:
                command_with_url = [
                    *command,
                    post_url,
                ]

                try:
                    process = subprocess.run(
                        command_with_url,
                        cwd=PROJECT,
                        capture_output=True,
                        text=True,
                        timeout=30 * 60,
                        check=False,
                    )
                except subprocess.TimeoutExpired as error:
                    timed_out = True
                    process = subprocess.CompletedProcess(
                        command_with_url,
                        124,
                        stdout=normalize_subprocess_text(
                            error.stdout
                        ),
                        stderr=(
                            normalize_subprocess_text(
                                error.stderr
                            )
                            + "\nDOWNLOAD_PROCESS_TIMEOUT"
                        ),
                    )
            else:
                command_with_url = [
                    *command,
                    post_url,
                ]
                process = subprocess.CompletedProcess(
                    command_with_url,
                    0,
                    stdout=(
                        "REFERENCE_SYNC_CACHE_REUSED_ALL_"
                        "SELECTED_COMPONENTS\n"
                    ),
                    stderr="",
                )

            retry_stdout = redact(
                normalize_subprocess_text(
                    process.stdout
                )
            )
            retry_stderr = redact(
                normalize_subprocess_text(
                    process.stderr
                )
            )

            retry_failure = classify_download_failure(
                process.returncode,
                retry_stdout,
                retry_stderr,
            )

            if timed_out:
                retry_failure = "NETWORK_ERROR"

            if retry_failure not in {
                "NETWORK_ERROR",
                "RATE_LIMIT",
            }:
                break

            delay = (
                retry_delays[retry_attempt]
                if retry_attempt < len(retry_delays)
                else 30
            )
            retry_attempt += 1

            for remaining_seconds in range(
                delay,
                0,
                -1,
            ):
                emit_progress(
                    before_percent,
                    (
                        "Соединение прервано. "
                        "Следующая попытка через "
                        f"{remaining_seconds} сек."
                    ),
                    current=index,
                    total=total_posts,
                    post_id=post_id,
                    state="CONNECTION_LOST",
                    retry_in=remaining_seconds,
                    retry_attempt=retry_attempt,
                )

                if stop_requested:
                    break

                time.sleep(1)

            if stop_requested:
                process = subprocess.CompletedProcess(
                    command_with_url,
                    130,
                    stdout="",
                    stderr="USER_STOP_REQUESTED",
                )
                break

            emit_progress(
                before_percent,
                "Повторное подключение",
                current=index,
                total=total_posts,
                post_id=post_id,
                state="RETRYING_NETWORK",
                retry_attempt=retry_attempt,
            )

        finished = datetime.now()

        stdout = redact(
            normalize_subprocess_text(process.stdout)
        )
        stderr = redact(
            normalize_subprocess_text(process.stderr)
        )

        failure_kind = classify_download_failure(
            process.returncode,
            stdout,
            stderr,
        )

        if timed_out:
            failure_kind = "NETWORK_ERROR"

        if stop_requested:
            failure_kind = "USER_STOPPED"

        log_path = (
            logs_directory
            / f"{index:03d}_{post_id}.log"
        )

        log_path.write_text(
            (
                f"URL: {post_url}\n"
                f"RETURN CODE: {process.returncode}\n\n"
                f"STDOUT\n{'=' * 70}\n"
                f"{stdout}\n\n"
                f"STDERR\n{'=' * 70}\n"
                f"{stderr}\n"
            ),
            encoding="utf-8",
        )

        current_result = {
            "post_id": post_id,
            "shortcode": shortcode,
            "post_url": post_url,
            "status": (
                "COMMAND_SUCCESS"
                if process.returncode == 0
                else "COMMAND_FAILED"
            ),
            "returncode": process.returncode,
            "failure_kind": failure_kind,
            "timed_out": timed_out,
            "duration_seconds": round(
                (finished - started).total_seconds(),
                2,
            ),
            "cache_reused_components": (
                cache_result[
                    "reused_component_numbers"
                ]
            ),
            "network_requested_components": (
                download_components
            ),
            "log": str(log_path),
        }

        download_results.append(current_result)

        job_state["download_results"] = list(
            download_results
        )
        job_state["last_updated_at"] = (
            datetime.now().isoformat()
        )
        save_job_state(job_file, job_state)

        if failure_kind == "USER_STOPPED":
            stopped_by_user = True

            remaining_posts = discovered_posts[
                index:
            ]

            job_state["status"] = "STOPPED_BY_USER"
            job_state["network_pause"] = None
            job_state["stopped_at"] = (
                datetime.now().isoformat()
            )
            job_state["remaining_post_ids"] = [
                post_id,
                *[
                    str(
                        remaining.get("post_id")
                        or ""
                    )
                    for remaining in remaining_posts
                ],
            ]
            job_state["download_results"] = list(
                download_results
            )
            save_job_state(job_file, job_state)
            break

        if failure_kind in {
            "ACCESS_BLOCKED",
            "AUTH_REQUIRED",
        }:
            network_paused = True
            network_pause_reason = failure_kind

            remaining_posts = discovered_posts[index:]

            for remaining_post in remaining_posts:
                download_results.append({
                    "post_id": str(
                        remaining_post.get("post_id") or ""
                    ),
                    "shortcode": remaining_post.get(
                        "post_shortcode"
                    ),
                    "post_url": remaining_post.get(
                        "post_url"
                    ),
                    "status": (
                        "QUEUED_AFTER_NETWORK_PAUSE"
                    ),
                    "returncode": None,
                    "failure_kind": failure_kind,
                    "timed_out": False,
                    "duration_seconds": 0,
                    "log": None,
                })

            job_state["status"] = "PAUSED_NETWORK"
            job_state["network_pause"] = {
                "reason": failure_kind,
                "failed_post_id": post_id,
                "paused_at": datetime.now().isoformat(),
                "remaining_post_ids": [
                    str(post.get("post_id") or "")
                    for post in remaining_posts
                ],
            }
            job_state["download_results"] = list(
                download_results
            )
            save_job_state(job_file, job_state)
            break

        if process.returncode == 0:
            completed_post_ids.add(post_id)

        after_percent = 5 + int(
            (
                len(completed_post_ids)
                / max(1, total_posts)
            )
            * 80
        )

        emit_progress(
            after_percent,
            (
                "Публикация скачана"
                if process.returncode == 0
                else "Загрузка приостановлена"
            ),
            current=len(completed_post_ids),
            total=total_posts,
            post_id=post_id,
        )

    # V6.4.8 FAST_STOP_FINAL
    #
    # Stop is not a successful download. Do not hash or validate
    # every staged file after the user explicitly stops the job.
    if stop_requested:
        stopped_by_user = True

    if stopped_by_user:
        if cookie_snapshot.is_file():
            cookie_snapshot.unlink()

        cookie_state = job_state.get(
            "cookie_snapshot"
        )

        if isinstance(cookie_state, dict):
            cookie_state["status"] = (
                "REMOVED_AFTER_STOP"
            )
            cookie_state["path"] = None

        existing_media = [
            path
            for path in media_directory.rglob("*")
            if (
                path.is_file()
                and path.suffix.lower()
                in MEDIA_EXTENSIONS
                and ".fdash-"
                not in path.name.lower()
            )
        ]

        stopped_extension_counts = Counter(
            path.suffix.lower().lstrip(".")
            for path in existing_media
        )

        stopped_summary = {
            "job_id": job_id,
            "status": "STOPPED_BY_USER",
            "browser": browser,
            "speed_profile": args.speed_profile,
            "logical_posts_requested": len(
                discovered_posts
            ),
            "expected_media_components": sum(
                expected_by_post.values()
            ),
            "downloaded_primary_media": len(
                existing_media
            ),
            "extension_counts": dict(
                sorted(
                    stopped_extension_counts.items()
                )
            ),
            "stopped_by_user": True,
            "resumed_from_same_job": bool(
                job_state.get("resumed_from_same_job")
            ),
            "ready_posts_at_resume": len(
                resume_ready_post_ids
            ),
            "network_paused": False,
            "network_pause_reason": None,
            "automatic_network_retry": True,
            "network_retry_timeout": None,
            "eagle_items_created": 0,
            "database_modified": False,
        }

        job_state.update({
            "finished_at": (
                datetime.now().isoformat()
            ),
            "status": "STOPPED_BY_USER",
            "summary": stopped_summary,
            "download_results": download_results,
            "cache_reuse_results": (
                cache_reuse_results
            ),
            "cookie_snapshot": cookie_state,
        })
        save_job_state(job_file, job_state)

        stopped_report_path = (
            REPORTS
            / f"instagram_staging_{job_id}.json"
        )
        stopped_report_path.write_text(
            json.dumps(
                job_state,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        emit_progress(
            max(
                5,
                int(
                    locals().get(
                        "before_percent",
                        5,
                    )
                ),
            ),
            "Процесс остановлен пользователем",
            state="STOPPED_BY_USER",
        )

        print(json.dumps({
            "summary": stopped_summary,
            "issues": [],
            "outputs": {
                "job_directory": str(
                    job_directory
                ),
                "media_directory": str(
                    media_directory
                ),
                "job_file": str(job_file),
                "report": str(
                    stopped_report_path
                ),
            },
            "safety": {
                "old_instagram_archive_modified": False,
                "eagle_library_modified": False,
                "eagle_items_created": 0,
                "database_modified": False,
                "staging_files_retained": len(
                    existing_media
                ),
                "cookie_snapshot_deleted": True,
                "files_deleted": 0,
            },
        }, ensure_ascii=False, indent=2))
        return

    emit_progress(
        88,
        "Проверка скачанных файлов",
    )

    records, issues = inspect_downloaded_media(
        media_directory,
        ffmpeg,
    )

    actual_by_post: dict[str, int] = defaultdict(int)
    actual_indexes_by_post: dict[str, set[int]] = (
        defaultdict(set)
    )

    for record in records:
        post_id = record.get("post_id")

        if post_id:
            actual_by_post[post_id] += 1

            component_index = record.get(
                "component_index"
            )

            if component_index is not None:
                actual_indexes_by_post[post_id].add(
                    int(component_index)
                )

    count_checks = []

    for post_id, expected_count in expected_by_post.items():
        actual_count = actual_by_post.get(post_id, 0)

        expected_indexes = set(
            selected_by_post.get(post_id, [])
        )
        actual_indexes = actual_indexes_by_post.get(
            post_id,
            set(),
        )

        count_matches = (
            actual_count == expected_count
            and actual_indexes == expected_indexes
        )

        count_checks.append({
            "post_id": post_id,
            "selected_components": sorted(
                expected_indexes
            ),
            "downloaded_component_indexes": sorted(
                actual_indexes
            ),
            "expected_components": expected_count,
            "downloaded_components": actual_count,
            "matches": count_matches,
        })

        if not count_matches:
            issues.append({
                "type": "COMPONENT_COUNT_MISMATCH",
                "post_id": post_id,
                "expected": expected_count,
                "actual": actual_count,
                "expected_indexes": sorted(
                    expected_indexes
                ),
                "actual_indexes": sorted(
                    actual_indexes
                ),
            })

    unexpected_post_ids = sorted(
        set(actual_by_post) - set(expected_by_post)
    )

    for post_id in unexpected_post_ids:
        issues.append({
            "type": "UNEXPECTED_POST_ID",
            "post_id": post_id,
            "components": actual_by_post[post_id],
        })

    failed_commands = [
        result
        for result in download_results
        if result["status"] != "COMMAND_SUCCESS"
    ]

    invalid_videos = [
        record
        for record in records
        if record.get("video_validation")
        and record["video_validation"].get("valid")
        is False
    ]

    invalid_sidecars = [
        record
        for record in records
        if not record["sidecar_valid"]
    ]

    extension_counts = Counter(
        record["extension"]
        for record in records
    )

    all_counts_match = all(
        check["matches"]
        for check in count_checks
    )

    ready = (
        not failed_commands
        and not issues
        and not invalid_videos
        and not invalid_sidecars
        and all_counts_match
        and len(records) == sum(expected_by_post.values())
    )

    summary = {
        "job_id": job_id,
        "status": (
            "STAGING_READY"
            if ready
            else (
                "STOPPED_BY_USER"
                if stopped_by_user
                else (
                    "PAUSED_NETWORK"
                    if network_paused
                    else "STAGING_REQUIRES_REVIEW"
                )
            )
        ),
        "browser": browser,
        "speed_profile": args.speed_profile,
        "network_paused": network_paused,
        "network_pause_reason": network_pause_reason,
        "stopped_by_user": stopped_by_user,
        "resumed_from_same_job": bool(
            job_state.get("resumed_from_same_job")
        ),
        "ready_posts_at_resume": len(
            resume_ready_post_ids
        ),
        "automatic_network_retry": True,
        "network_retry_timeout": None,
        "missed_post_ids": [
            str(result.get("post_id") or "")
            for result in download_results
            if result.get("status")
            in {
                "COMMAND_FAILED",
                "QUEUED_AFTER_NETWORK_PAUSE",
            }
        ],
        "browser_details": browser_details,
        "logical_posts_requested": len(
            discovered_posts
        ),
        "expected_media_components": sum(
            expected_by_post.values()
        ),
        "downloaded_primary_media": len(records),
        "extension_counts": dict(
            sorted(extension_counts.items())
        ),
        "valid_sidecars": sum(
            1
            for record in records
            if record["sidecar_valid"]
        ),
        "valid_videos": sum(
            1
            for record in records
            if record.get("video_validation")
            and record["video_validation"].get("valid")
            is True
        ),
        "invalid_videos": len(invalid_videos),
        "failed_download_commands": len(
            failed_commands
        ),
        "cache_reused_components": len(
            cache_reuse_results
        ),
        "network_download_components_requested": sum(
            len(
                result.get(
                    "network_requested_components",
                    [],
                )
            )
            for result in download_results
        ),
        "component_counts_match": all_counts_match,
        "issues": len(issues),
        "eagle_items_created": 0,
        "database_modified": False,
    }

    job_state.update({
        "finished_at": datetime.now().isoformat(),
        "status": summary["status"],
        "summary": summary,
        "download_results": download_results,
        "cache_reuse_results": cache_reuse_results,
        "count_checks": count_checks,
        "issues": issues,
        "records": records,
    })

    save_job_state(job_file, job_state)

    report_path = (
        REPORTS
        / f"instagram_staging_{job_id}.json"
    )

    report_path.write_text(
        json.dumps(
            job_state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps({
        "summary": summary,
        "count_checks": count_checks,
        "issues": issues,
        "outputs": {
            "job_directory": str(job_directory),
            "media_directory": str(media_directory),
            "job_file": str(job_file),
            "report": str(report_path),
        },
        "safety": {
            "old_instagram_archive_modified": False,
            "eagle_library_modified": False,
            "eagle_items_created": 0,
            "database_modified": False,
            "staging_files_created": len(records),
            "files_deleted": 0,
            "files_moved": 0,
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
