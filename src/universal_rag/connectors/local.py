"""Local directory connector — ingests files from configured filesystem paths.

Walks each root recursively, extracts text from supported files (the shared
docx/pptx/pdf/text/html extractor), and yields one document per file. Incremental
sync filters by modification time; deletions are handled by reconcile-prune
(`list_external_ids` returns every current file path).

Config (source.options):
    paths:              list[str]  required — root directories (``~`` expanded)
    include_extensions: list[str]  optional — override the default supported set
    exclude_globs:      list[str]  optional — fnmatch patterns (name or full path)
    max_file_mb:        int        optional — skip files larger than this (default 10)
    follow_symlinks:    bool       optional — follow symlinked dirs (default False)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path

from universal_rag.connectors.base import Connector, SourceDocument, SyncCursor
from universal_rag.connectors.extract import DEFAULT_EXTENSIONS, extract_text

# File mtimes are exact; a small buffer avoids missing files written in the same
# second as the stored cursor (re-read files are hash-skipped downstream).
_CURSOR_SAFETY = timedelta(seconds=2)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class LocalDirectoryConnector(Connector):
    provider = "local"

    def _settings(self) -> tuple[list[str], set[str], list[str], int, bool]:
        paths = self.source.opt("paths") or []
        if not paths:
            raise RuntimeError(f"Local source '{self.source.id}' has no 'paths' configured")
        allowed = {e.lower() for e in (self.source.opt("include_extensions") or DEFAULT_EXTENSIONS)}
        excludes = list(self.source.opt("exclude_globs") or [])
        max_bytes = int(self.source.opt("max_file_mb", 10)) * 1024 * 1024
        follow = bool(self.source.opt("follow_symlinks", False))
        return list(paths), allowed, excludes, max_bytes, follow

    def _iter_files(self) -> Iterator[tuple[str, os.stat_result]]:
        """Yield (abspath, stat) for every file passing the ext/exclude/size filters."""
        paths, allowed, excludes, max_bytes, follow = self._settings()
        for root in paths:
            base = os.path.abspath(os.path.expanduser(root))
            for dirpath, _dirnames, filenames in os.walk(base, followlinks=follow):
                for name in filenames:
                    fpath = os.path.join(dirpath, name)
                    if os.path.splitext(name)[1].lower() not in allowed:
                        continue
                    if any(fnmatch(name, p) or fnmatch(fpath, p) for p in excludes):
                        continue
                    try:
                        st = os.stat(fpath)
                    except OSError:
                        continue
                    if max_bytes and st.st_size > max_bytes:
                        continue
                    yield fpath, st

    @staticmethod
    def _since(cursor: SyncCursor | None) -> datetime | None:
        if not cursor or not cursor.value:
            return None
        dt = _parse_iso(cursor.value)
        return dt - _CURSOR_SAFETY if dt else None

    def fetch(self, cursor: SyncCursor | None = None) -> Iterator[SourceDocument]:
        since = self._since(cursor)
        for fpath, st in self._iter_files():
            mtime = datetime.fromtimestamp(st.st_mtime, UTC)
            if since is not None and mtime < since:
                continue
            try:
                data = Path(fpath).read_bytes()
            except OSError:
                continue
            text = extract_text(os.path.basename(fpath), data)
            if not text.strip():
                continue
            yield SourceDocument(
                source_id=self.source.id,
                provider="local",
                external_id=fpath,
                title=os.path.basename(fpath),
                content=text,
                url=f"file://{fpath}",
                updated_at=mtime,
                metadata={
                    "doc_type": "local_file",
                    "path": fpath,
                    "dir": os.path.dirname(fpath),
                    "ext": os.path.splitext(fpath)[1].lower(),
                    "size": st.st_size,
                },
            )

    def list_external_ids(self) -> set[str]:
        return {fpath for fpath, _ in self._iter_files()}

    def healthcheck(self) -> bool:
        paths, *_ = self._settings()
        return all(os.path.isdir(os.path.abspath(os.path.expanduser(p))) for p in paths)
