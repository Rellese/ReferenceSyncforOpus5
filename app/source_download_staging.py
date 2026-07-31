"""Shared gallery-dl staging downloader.

Preview validates the adapter and URL without network access.
Download requires explicit ``download --commit`` and writes only
inside downloads/<source>/incoming/<job-id>.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.source_adapter import get_source_adapter
from app.source_staging_contract import (
    ensure_registered_staging_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
STAGING_ROOT = PROJECT / "downloads"
JOB_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{2,99}$"
)

MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".avif",
    ".heic",
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".mp3",
    ".m4a",
    ".aac",
    ".wav",
    ".ogg",
}


class SourceDownloadError(RuntimeError):
    """Raised when a staging download cannot be trusted."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for block in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def validate_job_id(job_id: str) -> str:
    normalized = str(job_id or "").strip()

    if not JOB_ID_RE.fullmatch(normalized):
        raise SourceDownloadError(
            "Invalid job ID"
        )

    return normalized


def validate_source_url(
    source_code: str,
    url: str,
) -> str:
    adapter = get_source_adapter(source_code)
    module = importlib.import_module(
        adapter.discovery_module
    )

    validator = getattr(
        module,
        f"validate_{source_code}_url",
        None,
    )

    normalized = str(url or "").strip()

    if validator is None:
        if not normalized.startswith(
            ("http://", "https://")
        ):
            raise SourceDownloadError(
                "Source URL must use HTTP or HTTPS"
            )

        return normalized

    return str(validator(normalized))


def staging_paths(
    source_code: str,
    job_id: str,
) -> tuple[Path, Path, Path]:
    job_directory = (
        STAGING_ROOT
        / source_code
        / "incoming"
        / job_id
    )
    media_directory = job_directory / "media"
    job_path = job_directory / "job.json"

    return (
        job_directory,
        media_directory,
        job_path,
    )


def build_gallery_command(
    *,
    source_code: str,
    url: str,
    media_directory: Path,
    limit: int,
    cookies_browser: str | None,
) -> list[str]:
    # id + num keeps carousel components distinct.
    filename = (
        f"{source_code}_"
        "{id}_{num}.{extension}"
    )

    command = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--directory",
        str(media_directory),
        "--filename",
        filename,
        "--write-metadata",
        "--range",
        f"1-{limit}",
    ]

    if cookies_browser:
        command.extend([
            "--cookies-from-browser",
            cookies_browser,
        ])

    command.append(url)
    return command


def safe_command(
    command: list[str],
) -> list[str]:
    result = []
    redact_next = False

    for value in command:
        if redact_next:
            result.append("<browser>")
            redact_next = False
            continue

        result.append(value)

        if value == "--cookies-from-browser":
            redact_next = True

    return result


def sidecar_media_path(
    sidecar_path: Path,
) -> Path | None:
    # gallery-dl --write-metadata writes <media-name>.json.
    candidate = sidecar_path.with_suffix("")

    if (
        candidate.is_file()
        and candidate.suffix.lower()
        in MEDIA_EXTENSIONS
    ):
        return candidate

    stem = sidecar_path.name[:-5]

    candidates = [
        path
        for path in sidecar_path.parent.glob(
            stem + "*"
        )
        if (
            path.is_file()
            and path.suffix.lower()
            in MEDIA_EXTENSIONS
        )
    ]

    if len(candidates) == 1:
        return candidates[0]

    return None


def normalized_sidecar(
    source_code: str,
    sidecar: dict[str, Any],
    media_path: Path,
) -> dict[str, Any]:
    adapter = get_source_adapter(source_code)
    module = importlib.import_module(
        adapter.normalizer_module
    )

    function = getattr(
        module,
        "staging_metadata_from_sidecar",
        None,
    )

    if function is None:
        raise SourceDownloadError(
            f"{source_code} normalizer does not expose "
            "staging_metadata_from_sidecar"
        )

    result = function(
        sidecar,
        local_filename=media_path.name,
    )

    if not isinstance(result, dict):
        raise SourceDownloadError(
            "Staging normalizer returned non-object"
        )

    return result


def build_job(
    *,
    source_code: str,
    job_id: str,
    source_url: str,
    job_path: Path,
    media_directory: Path,
) -> dict[str, Any]:
    records = []
    posts_by_id: dict[str, dict[str, Any]] = {}
    container_chain: list[str] = []

    sidecars = sorted(
        media_directory.rglob("*.json")
    )

    if not sidecars:
        raise SourceDownloadError(
            "gallery-dl produced no metadata sidecars"
        )

    for sidecar_path in sidecars:
        media_path = sidecar_media_path(
            sidecar_path
        )

        if media_path is None:
            raise SourceDownloadError(
                f"No unique media file for sidecar: "
                f"{sidecar_path}"
            )

        payload = json.loads(
            sidecar_path.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(payload, dict):
            raise SourceDownloadError(
                f"Sidecar root is not an object: "
                f"{sidecar_path}"
            )

        normalized = normalized_sidecar(
            source_code,
            payload,
            media_path,
        )

        post_id = str(
            normalized.get("post_id") or ""
        ).strip()
        media_id = str(
            normalized.get("media_id") or ""
        ).strip()
        canonical_url = str(
            normalized.get("canonical_url") or ""
        ).strip()
        component_index = int(
            normalized.get("component_index")
            or 1
        )
        component_count = int(
            normalized.get(
                "total_component_count"
            )
            or 1
        )

        if not post_id or not media_id or not canonical_url:
            raise SourceDownloadError(
                f"Incomplete normalized sidecar: "
                f"{sidecar_path}"
            )

        current_containers = [
            str(value).strip()
            for value in normalized.get(
                "container_ids",
                [],
            )
            if str(value).strip()
        ]

        for container_id in current_containers:
            if container_id not in container_chain:
                container_chain.append(
                    container_id
                )

        records.append({
            "post_id": post_id,
            "media_id": media_id,
            "component_index": component_index,
            "component_count": component_count,
            "container_ids": current_containers,
            "local_path": str(
                media_path.resolve()
            ),
            "sidecar_path": str(
                sidecar_path.resolve()
            ),
            "extension": (
                media_path.suffix
                .lower()
                .lstrip(".")
            ),
            "size": media_path.stat().st_size,
            "sha256": sha256_file(media_path),
        })

        post = posts_by_id.setdefault(
            post_id,
            {
                "post_id": post_id,
                "canonical_url": canonical_url,
                "title": normalized.get(
                    "display_name"
                ),
                "description": normalized.get(
                    "description"
                ),
                "container_ids": (
                    current_containers
                ),
                "total_component_count": (
                    component_count
                ),
            },
        )

        if (
            int(post["total_component_count"])
            != component_count
        ):
            raise SourceDownloadError(
                f"Inconsistent component count "
                f"for post {post_id}"
            )

    record_keys = {
        (
            record["post_id"],
            record["media_id"],
            record["component_index"],
        )
        for record in records
    }

    if len(record_keys) != len(records):
        raise SourceDownloadError(
            "Duplicate normalized staging records"
        )

    job = {
        "job_id": job_id,
        "status": "STAGING_READY",
        "source_code": source_code,
        "source_url": source_url,
        "created_at": utc_now(),
        "container_chain": container_chain,
        "posts": list(posts_by_id.values()),
        "records": sorted(
            records,
            key=lambda record: (
                record["post_id"],
                record["component_index"],
                record["media_id"],
            ),
        ),
        "summary": {
            "logical_posts": len(posts_by_id),
            "media_components": len(records),
            "containers": len(container_chain),
        },
    }

    job = ensure_registered_staging_contract(
        job,
        source_code=source_code,
    )

    temporary = job_path.with_suffix(
        ".json.tmp"
    )
    temporary.write_text(
        json.dumps(
            job,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(job_path)

    return job


def preview(
    *,
    source_code: str,
    job_id: str,
    url: str,
    limit: int,
    cookies_browser: str | None,
) -> dict[str, Any]:
    adapter = get_source_adapter(source_code)
    url = validate_source_url(
        source_code,
        url,
    )
    job_id = validate_job_id(job_id)

    (
        job_directory,
        media_directory,
        job_path,
    ) = staging_paths(source_code, job_id)

    command = build_gallery_command(
        source_code=source_code,
        url=url,
        media_directory=media_directory,
        limit=limit,
        cookies_browser=cookies_browser,
    )

    return {
        "status": "PREVIEW_ONLY",
        "source_code": source_code,
        "display_name": adapter.display_name,
        "job_id": job_id,
        "source_url": url,
        "job_directory": str(job_directory),
        "media_directory": str(media_directory),
        "job_json": str(job_path),
        "command": safe_command(command),
        "limit": limit,
        "safety": {
            "network_requests": 0,
            "files_downloaded": 0,
            "job_created": False,
            "eagle_api_requests": 0,
            "database_modified": False,
        },
    }


def download(
    *,
    source_code: str,
    job_id: str,
    url: str,
    limit: int,
    cookies_browser: str | None,
    timeout: int,
    commit: bool,
) -> dict[str, Any]:
    if not commit:
        raise SourceDownloadError(
            "DOWNLOAD_REQUIRES_EXPLICIT_COMMIT"
        )

    preview_result = preview(
        source_code=source_code,
        job_id=job_id,
        url=url,
        limit=limit,
        cookies_browser=cookies_browser,
    )

    (
        job_directory,
        media_directory,
        job_path,
    ) = staging_paths(source_code, job_id)

    if job_directory.exists():
        raise SourceDownloadError(
            f"Staging job already exists: "
            f"{job_directory}"
        )

    media_directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    command = build_gallery_command(
        source_code=source_code,
        url=url,
        media_directory=media_directory,
        limit=limit,
        cookies_browser=cookies_browser,
    )

    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )

        if completed.returncode != 0:
            diagnostic = completed.stderr.strip()

            if len(diagnostic) > 3000:
                diagnostic = diagnostic[-3000:]

            raise SourceDownloadError(
                "gallery-dl failed with exit status "
                f"{completed.returncode}: "
                f"{diagnostic or 'no diagnostic'}"
            )

        job = build_job(
            source_code=source_code,
            job_id=job_id,
            source_url=url,
            job_path=job_path,
            media_directory=media_directory,
        )

    except Exception:
        # A failed job is removed only from its new isolated directory.
        if job_directory.exists():
            shutil.rmtree(job_directory)
        raise

    result = dict(preview_result)
    result["status"] = "STAGING_READY"
    result["summary"] = job["summary"]
    result["safety"] = {
        "network_requests": "performed_by_gallery_dl",
        "files_downloaded": len(
            job["records"]
        ),
        "job_created": True,
        "eagle_api_requests": 0,
        "database_modified": False,
    }

    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=("preview", "download"),
    )
    parser.add_argument(
        "--source-code",
        required=True,
    )
    parser.add_argument(
        "--job-id",
        required=True,
    )
    parser.add_argument(
        "--url",
        required=True,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
    )
    parser.add_argument(
        "--cookies-browser",
        default="chrome",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
    )
    parser.add_argument(
        "--commit",
        action="store_true",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    if args.timeout < 1:
        raise SystemExit(
            "--timeout must be at least 1"
        )

    if args.operation == "preview":
        if args.commit:
            raise SystemExit(
                "--commit is only valid with download"
            )

        result = preview(
            source_code=args.source_code,
            job_id=args.job_id,
            url=args.url,
            limit=args.limit,
            cookies_browser=args.cookies_browser,
        )

    else:
        result = download(
            source_code=args.source_code,
            job_id=args.job_id,
            url=args.url,
            limit=args.limit,
            cookies_browser=args.cookies_browser,
            timeout=args.timeout,
            commit=args.commit,
        )

    text = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
    )

    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output.write_text(
            text + "\n",
            encoding="utf-8",
        )

    print(text)


if __name__ == "__main__":
    main()
