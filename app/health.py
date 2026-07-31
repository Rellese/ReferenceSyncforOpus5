from __future__ import annotations

import shutil
import sys
from pathlib import Path

from app.eagle import check_eagle
from app.settings import Settings


def command_version(command: str) -> str | None:
    path = shutil.which(command)
    return path


def directory_status(path: Path, writable: bool = False) -> dict:
    result = {
        "path": str(path),
        "exists": path.exists(),
        "is_directory": path.is_dir(),
    }

    if writable and path.exists():
        probe = path / ".referencesync_write_test"

        try:
            probe.write_text("test", encoding="utf-8")
            probe.unlink()
            result["writable"] = True
        except Exception as exc:
            result["writable"] = False
            result["error"] = str(exc)

    return result


def build_health_report(settings: Settings) -> dict:
    return {
        "application": {
            "name": settings.project_name,
            "version": "0.1.0",
            "python": sys.version.split()[0],
        },
        "dependencies": {
            "gallery_dl": command_version("gallery-dl"),
            "yt_dlp": command_version("yt-dlp"),
            "ffmpeg": command_version("ffmpeg"),
        },
        "protected_paths_read_only": {
            "eagle_library": directory_status(
                settings.eagle_library_path
            ),
            "existing_instagram": directory_status(
                settings.existing_instagram_source
            ),
        },
        "working_paths": {
            "downloads": directory_status(
                settings.download_path, writable=True
            ),
            "temporary": directory_status(
                settings.temporary_path, writable=True
            ),
            "logs": directory_status(
                settings.logs_path, writable=True
            ),
            "reports": directory_status(
                settings.reports_path, writable=True
            ),
        },
        "eagle": check_eagle(settings.eagle_api_url),
    }
