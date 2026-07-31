from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from app.settings import Settings


DASH_PATTERN = re.compile(
    r"^(?P<media_id>\d+)\.fdash-(?P<format_id>[^.]+)\.mp4$"
)


def probe_streams(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        if result.returncode != 0:
            return {
                "ok": False,
                "error": result.stderr.strip()[:500],
            }

        payload = json.loads(result.stdout)

        return {
            "ok": True,
            "streams": payload.get("streams", []),
            "duration": payload.get(
                "format", {}
            ).get("duration"),
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def main() -> None:
    settings = Settings.load()
    root = settings.existing_instagram_source

    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row

    results = []

    dash_files = sorted(
        path
        for path in root.rglob("*.mp4")
        if DASH_PATTERN.match(path.name)
    )

    mapped_count = 0
    main_file_exists_count = 0
    video_only_count = 0
    files_with_audio_count = 0

    for dash_path in dash_files:
        match = DASH_PATTERN.match(dash_path.name)
        media_id = match.group("media_id")

        database_matches = connection.execute(
            """
            SELECT
                m.external_media_id,
                m.component_index,
                m.local_path,
                m.status AS media_status,
                p.external_id AS post_id,
                p.shortcode AS post_shortcode,
                p.canonical_url
            FROM media m
            JOIN posts p ON p.id = m.post_id
            WHERE m.external_media_id = ?
            """,
            (media_id,),
        ).fetchall()

        if database_matches:
            mapped_count += 1

        possible_main_files = []

        for candidate in dash_path.parent.glob(f"{media_id}*"):
            if not candidate.is_file():
                continue

            if candidate == dash_path:
                continue

            if candidate.name.endswith(".json"):
                continue

            if ".fdash-" in candidate.name:
                continue

            possible_main_files.append(candidate)

        if possible_main_files:
            main_file_exists_count += 1

        probe = probe_streams(dash_path)
        stream_types = [
            stream.get("codec_type")
            for stream in probe.get("streams", [])
        ]

        if stream_types and set(stream_types) == {"video"}:
            video_only_count += 1

        if "audio" in stream_types:
            files_with_audio_count += 1

        results.append(
            {
                "dash_file": str(dash_path.relative_to(root)),
                "size": dash_path.stat().st_size,
                "media_id_from_filename": media_id,
                "format_id": match.group("format_id"),
                "database_matches": [
                    dict(row) for row in database_matches
                ],
                "possible_main_files": [
                    str(path.relative_to(root))
                    for path in possible_main_files
                ],
                "ffprobe": probe,
            }
        )

    connection.close()

    summary = {
        "dash_files": len(dash_files),
        "mapped_to_known_media_id": mapped_count,
        "with_possible_main_file": main_file_exists_count,
        "video_only_streams": video_only_count,
        "files_containing_audio": files_with_audio_count,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = (
        settings.reports_path
        / f"instagram_dash_diagnose_{timestamp}.json"
    )

    report = {
        "mode": "READ_ONLY",
        "summary": summary,
        "files": results,
        "source_modified": False,
        "eagle_library_modified": False,
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "summary": summary,
                "report": str(report_path),
                "source_modified": False,
                "eagle_library_modified": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
