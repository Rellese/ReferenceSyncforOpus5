"""Source adapter definitions for ReferenceSync.

This module contains platform configuration only. Shared runtime code
must obtain platform-specific values from this registry instead of
hard-coding Instagram, Pinterest, or future source names.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


SOURCE_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
NAME_MARKER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")

# Old staging jobs predate source_code and are Instagram jobs.
LEGACY_DEFAULT_SOURCE_CODE = "instagram"


class SourceAdapterError(ValueError):
    """Raised for missing or invalid source adapter configuration."""


def normalized_values(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()

    for raw_value in values:
        value = str(raw_value or "").strip()

        if not value or value in seen:
            continue

        seen.add(value)
        result.append(value)

    return tuple(result)


@dataclass(frozen=True, slots=True)
class SourceAdapter:
    """Immutable configuration for one external source."""

    source_code: str
    display_name: str
    discovery_module: str
    normalizer_module: str
    root_external_id: str
    root_display_name: str
    container_types: tuple[str, ...]
    default_eagle_tags: tuple[str, ...]
    default_eagle_folder_ids: tuple[str, ...]
    default_name_marker: str
    job_prefix: str
    discovery_report_prefix: str

    def __post_init__(self) -> None:
        source_code = self.source_code.strip().lower()
        display_name = self.display_name.strip()
        root_external_id = self.root_external_id.strip()
        root_display_name = self.root_display_name.strip()
        marker = self.default_name_marker.strip().lower()
        job_prefix = self.job_prefix.strip()
        report_prefix = self.discovery_report_prefix.strip()

        if not SOURCE_CODE_RE.fullmatch(source_code):
            raise SourceAdapterError(
                f"Invalid source_code: {self.source_code!r}"
            )

        if not display_name:
            raise SourceAdapterError("display_name cannot be empty")

        if not root_external_id:
            raise SourceAdapterError(
                "root_external_id cannot be empty"
            )

        if not root_display_name:
            raise SourceAdapterError(
                "root_display_name cannot be empty"
            )

        if not NAME_MARKER_RE.fullmatch(marker):
            raise SourceAdapterError(
                f"Invalid name marker: {marker!r}"
            )

        container_types = tuple(
            str(value or "").strip().upper()
            for value in self.container_types
            if str(value or "").strip()
        )

        if not container_types or container_types[0] != "ROOT":
            raise SourceAdapterError(
                "container_types must begin with ROOT"
            )

        if len(container_types) != len(set(container_types)):
            raise SourceAdapterError(
                "container_types contains duplicates"
            )

        if not self.discovery_module.strip():
            raise SourceAdapterError(
                "discovery_module cannot be empty"
            )

        if not self.normalizer_module.strip():
            raise SourceAdapterError(
                "normalizer_module cannot be empty"
            )

        if not job_prefix:
            raise SourceAdapterError(
                "job_prefix cannot be empty"
            )

        if not report_prefix:
            raise SourceAdapterError(
                "discovery_report_prefix cannot be empty"
            )

        object.__setattr__(self, "source_code", source_code)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(
            self,
            "root_external_id",
            root_external_id,
        )
        object.__setattr__(
            self,
            "root_display_name",
            root_display_name,
        )
        object.__setattr__(
            self,
            "container_types",
            container_types,
        )
        object.__setattr__(
            self,
            "default_eagle_tags",
            normalized_values(self.default_eagle_tags),
        )
        object.__setattr__(
            self,
            "default_eagle_folder_ids",
            normalized_values(
                self.default_eagle_folder_ids
            ),
        )
        object.__setattr__(
            self,
            "default_name_marker",
            marker,
        )
        object.__setattr__(
            self,
            "job_prefix",
            job_prefix,
        )
        object.__setattr__(
            self,
            "discovery_report_prefix",
            report_prefix,
        )

    @property
    def staging_relative_path(self) -> str:
        return f"{self.source_code}/incoming"

    @property
    def root_container_type(self) -> str:
        return self.container_types[0]


_ADAPTERS = {
    "instagram": SourceAdapter(
        source_code="instagram",
        display_name="Instagram",
        discovery_module="app.instagram_discover",
        normalizer_module="app.instagram_normalize",
        root_external_id="root:instagram",
        root_display_name="Instagram",
        container_types=(
            "ROOT",
            "ACCOUNT",
            "COLLECTION",
        ),
        default_eagle_tags=("Instagram",),
        # Existing production Eagle folder; retained for compatibility.
        default_eagle_folder_ids=("MRWRIOJO42ER5",),
        default_name_marker="instpoporder",
        job_prefix="instagram",
        discovery_report_prefix="instagram_discovery",
    ),
    "pinterest": SourceAdapter(
        source_code="pinterest",
        display_name="Pinterest",
        discovery_module="app.pinterest_discover",
        normalizer_module="app.pinterest_normalize",
        root_external_id="root:pinterest",
        root_display_name="Pinterest",
        container_types=(
            "ROOT",
            "BOARD",
            "SECTION",
        ),
        default_eagle_tags=("Pinterest",),
        # Folder IDs are resolved from containers/Eagle, not hard-coded.
        default_eagle_folder_ids=(),
        default_name_marker="pinorder",
        job_prefix="pinterest",
        discovery_report_prefix="pinterest_discovery",
    ),
}


def get_source_adapter(source_code: str) -> SourceAdapter:
    normalized = str(source_code or "").strip().lower()

    try:
        return _ADAPTERS[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(_ADAPTERS))
        raise SourceAdapterError(
            f"Unknown source {source_code!r}; "
            f"available sources: {available}"
        ) from exc


def list_source_adapters() -> tuple[SourceAdapter, ...]:
    return tuple(
        _ADAPTERS[key]
        for key in sorted(_ADAPTERS)
    )


def source_codes() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))
