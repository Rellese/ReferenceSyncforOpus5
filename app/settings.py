from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "local.json"


@dataclass(frozen=True)
class Settings:
    project_name: str
    database_path: Path
    download_path: Path
    temporary_path: Path
    logs_path: Path
    reports_path: Path
    eagle_api_url: str
    eagle_library_path: Path
    existing_instagram_source: Path
    instagram_tag: str
    browser: str

    @classmethod
    def load(cls) -> "Settings":
        if not CONFIG_PATH.exists():
            raise RuntimeError(f"Configuration not found: {CONFIG_PATH}")

        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        settings = cls(
            project_name=raw["project_name"],
            database_path=Path(raw["database_path"]).expanduser().resolve(),
            download_path=Path(raw["download_path"]).expanduser().resolve(),
            temporary_path=Path(raw["temporary_path"]).expanduser().resolve(),
            logs_path=Path(raw["logs_path"]).expanduser().resolve(),
            reports_path=Path(raw["reports_path"]).expanduser().resolve(),
            eagle_api_url=raw["eagle_api_url"].rstrip("/"),
            eagle_library_path=Path(
                raw["eagle_library_path"]
            ).expanduser().resolve(),
            existing_instagram_source=Path(
                raw["existing_instagram_source"]
            ).expanduser().resolve(),
            instagram_tag=raw.get("instagram_tag", "Instagram"),
            browser=raw.get("browser", "firefox"),
        )

        settings.validate_safety()
        return settings

    def validate_safety(self) -> None:
        writable_paths = (
            self.database_path.parent,
            self.download_path,
            self.temporary_path,
            self.logs_path,
            self.reports_path,
        )

        protected_paths = (
            self.eagle_library_path,
            self.existing_instagram_source,
        )

        for writable in writable_paths:
            for protected in protected_paths:
                if writable == protected or protected in writable.parents:
                    raise RuntimeError(
                        "Unsafe configuration: a ReferenceSync writable path "
                        f"is inside a protected directory: {writable}"
                    )

        for path in writable_paths:
            path.mkdir(parents=True, exist_ok=True)
