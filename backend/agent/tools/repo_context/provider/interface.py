from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FileReadResult:
    path: str
    lines: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


@dataclass
class SearchMatch:
    file: str
    line: int
    snippet: str


@dataclass
class SearchResult:
    matches: list[SearchMatch] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


@dataclass
class FileEntry:
    path: str


@dataclass
class FileListResult:
    entries: list[FileEntry] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


@dataclass
class ManifestEntry:
    path: str
    content: str | None = None
    entries: list[str] | None = None
    is_directory: bool = False
    truncated: bool = False


@dataclass
class ManifestResult:
    manifests: dict[str, list[ManifestEntry]] = field(default_factory=dict)


class RepoProvider(ABC):
    @property
    @abstractmethod
    def repo_root(self) -> str: ...

    @abstractmethod
    async def read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_bytes: int | None = None,
    ) -> FileReadResult: ...

    @abstractmethod
    async def search_code(
        self,
        query: str,
        globs: list[str] | None = None,
        max_results: int = 50,
    ) -> SearchResult: ...

    @abstractmethod
    async def list_files(
        self,
        globs: list[str] | None = None,
        max_results: int = 100,
    ) -> FileListResult: ...

    @abstractmethod
    async def get_manifest(self) -> ManifestResult: ...

    @abstractmethod
    def verify(self) -> dict[str, Any]: ...
