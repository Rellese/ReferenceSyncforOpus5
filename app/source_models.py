from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceContainer:
    """Platform-independent folder, board, collection or section."""

    platform: str
    kind: str
    source_id: str
    name: str
    parent_source_id: str | None = None
    item_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceMedia:
    """One physical media component belonging to a publication."""

    source_media_id: str
    index: int
    media_type: str
    url: str | None = None
    extension: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    local_filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def identity(self) -> tuple[str, str, int]:
        return (
            self.source_media_id,
            self.url or "",
            self.index,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourcePublication:
    """Normalized publication shared by all platform adapters."""

    platform: str
    source_id: str
    container_id: str | None
    section_id: str | None
    title: str
    description: str
    canonical_url: str
    publication_type: str
    created_at: str | None = None
    media: list[SourceMedia] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
    container_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NormalizedSourceBundle:
    """Containers and publications returned by a source adapter."""

    platform: str
    containers: list[SourceContainer] = field(default_factory=list)
    publications: list[SourcePublication] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
