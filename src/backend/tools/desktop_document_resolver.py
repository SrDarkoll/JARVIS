"""Resolve local document names using metadata only, without opening their contents."""

import os
import re
import stat
import unicodedata
from pathlib import Path

from tools.desktop_app_resolver import _is_local_windows_drive

MAX_SEARCH_ENTRIES = 1024
MAX_DOCUMENT_ROOTS = 8
MAX_SEARCH_DEPTH = 1
DOCUMENT_SUFFIXES = frozenset({".pptx", ".pdf", ".docx", ".xlsx", ".txt"})
_WINDOWS_ABSOLUTE = re.compile(r"^[a-z]:[/\\]", re.IGNORECASE)
_UNSAFE_NAME = re.compile(r'[\x00-\x1f\x7f"&|;<>`$%*?]')
_SKIP_DIRECTORIES = {"venv", "node_modules", ".git", "target", "build", "scratch"}


class DocumentResolutionError(ValueError):
    """Controlled resolver failure; both diagnostic_code and args[0] carry the code."""

    def __init__(self, diagnostic_code: str) -> None:
        self.diagnostic_code = diagnostic_code
        super().__init__(diagnostic_code)


def _local_absolute_path(path: Path) -> bool:
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        return False
    return os.name != "nt" or _is_local_windows_drive(path.anchor)


def _validate_document_name(name: str) -> Path:
    if not isinstance(name, str) or not name or len(name) > 240 or _UNSAFE_NAME.search(name):
        raise DocumentResolutionError("document_invalid_name")
    windows_absolute = bool(_WINDOWS_ABSOLUTE.match(name))
    if name.startswith(("\\", "//")) or ":" in (name[2:] if windows_absolute else name):
        raise DocumentResolutionError("document_invalid_name")
    normalized = name.replace("\\", "/")
    tail = normalized[3:] if windows_absolute else normalized.removeprefix("/")
    parts = tail.split("/")
    requested = Path(normalized)
    if (
        (not requested.is_absolute() and len(parts) > 2)
        or (windows_absolute and not requested.is_absolute())
        or any(
            not part or part in {".", ".."} or part != part.strip()
            or part.startswith(("~", "'")) or part.endswith((".", "'"))
            for part in parts
        )
        or (requested.suffix and requested.suffix.casefold() not in DOCUMENT_SUFFIXES)
    ):
        raise DocumentResolutionError("document_invalid_name")
    return requested


def document_search_roots() -> tuple[Path, ...]:
    """Nonblank explicit roots replace the user Desktop/Escritorio defaults entirely."""
    configured = os.environ.get("JARVIS_DOCUMENT_ROOTS", "").strip()
    if configured:
        names = [name.strip() for name in configured.split(os.pathsep)]
    else:
        desktop = os.environ.get("JARVIS_DESKTOP_HOME", "").strip()
        home = Path(os.environ.get("USERPROFILE") or Path.home())
        names = [desktop] if desktop else [str(home / "Desktop"), str(home / "Escritorio")]
    if not names or any(not name for name in names):
        raise DocumentResolutionError("document_invalid_roots")
    if len(names) > MAX_DOCUMENT_ROOTS:
        raise DocumentResolutionError("document_search_limit")
    roots = tuple(dict.fromkeys(Path(name) for name in names))
    if any(not _local_absolute_path(root) for root in roots):
        raise DocumentResolutionError("document_invalid_roots")
    return roots


def _is_link(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _resolve_root(root: Path) -> Path:
    for component in (*reversed(root.parents), root):
        metadata = component.lstat()
        if _is_link(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise DocumentResolutionError("document_invalid_roots")
    resolved = root.resolve(strict=True)
    if not _local_absolute_path(resolved):
        raise DocumentResolutionError("document_invalid_roots")
    return resolved


def _fold_name(name: str) -> str:
    return unicodedata.normalize("NFC", name.casefold())


def _name_rank(name: str, requested: str) -> int | None:
    candidate = Path(name)
    if candidate.suffix.casefold() not in DOCUMENT_SUFFIXES:
        return None
    name, requested = _fold_name(name), _fold_name(requested)
    if name == requested:
        return 0
    if Path(requested).suffix:
        return None
    stem = _fold_name(candidate.stem)
    if stem == requested:
        return 1
    return 2 if stem.startswith(requested) else None


def resolve_local_document(name: str) -> Path | None:
    """Return one allowed document, None for no match, or a controlled diagnostic.

    Search at most eight local roots and 1024 total directory entries, one level
    below each root. Explicit paths must remain in that same shallow scope.
    Exact filenames outrank stems, which outrank unique prefixes. Every tie is
    ambiguous, independent of root order. Links and Windows reparse points are
    excluded; no document content is inspected and no launch is performed.
    """
    requested = _validate_document_name(name)
    matches: dict[Path, int] = {}
    remaining = MAX_SEARCH_ENTRIES
    roots: dict[Path, None] = {}
    try:
        for root in document_search_roots():
            try:
                resolved = _resolve_root(root)
            except FileNotFoundError:
                continue
            roots[resolved] = None

        queries = []
        for root in roots:
            if requested.is_absolute():
                if not requested.is_relative_to(root):
                    continue
                parts = requested.relative_to(root).parts
                if not 1 <= len(parts) <= MAX_SEARCH_DEPTH + 1:
                    continue
            else:
                parts = requested.parts
            queries.append((root, parts))
        if requested.is_absolute() and not queries:
            raise DocumentResolutionError("document_invalid_name")

        for root, parts in queries:
            search_depth = 0 if requested.is_absolute() and len(parts) == 1 else MAX_SEARCH_DEPTH
            pending = [(root, 0)]
            while pending:
                directory, depth = pending.pop()
                with os.scandir(directory) as entries:
                    for entry in entries:
                        remaining -= 1
                        if remaining < 0:
                            raise DocumentResolutionError("document_search_limit")
                        try:
                            metadata = entry.stat(follow_symlinks=False)
                            if _is_link(metadata) or entry.name.startswith("."):
                                continue
                            path = Path(entry.path)
                            if stat.S_ISDIR(metadata.st_mode):
                                if (depth >= search_depth or entry.name.casefold() in _SKIP_DIRECTORIES
                                        or (len(parts) == 2 and _fold_name(entry.name) != _fold_name(parts[0]))):
                                    continue
                                target = path.resolve(strict=True)
                                if target.is_relative_to(root) and _local_absolute_path(target):
                                    pending.append((target, depth + 1))
                            elif stat.S_ISREG(metadata.st_mode) and (len(parts) == 1 or depth == 1):
                                rank = _name_rank(entry.name, parts[-1])
                                if rank is None:
                                    continue
                                target = path.resolve(strict=True)
                                if (target.is_relative_to(root) and _local_absolute_path(target)
                                        and target.suffix.casefold() in DOCUMENT_SUFFIXES):
                                    matches[target] = min(rank, matches.get(target, rank))
                        except FileNotFoundError:
                            continue
    except (OSError, RuntimeError) as exc:
        raise DocumentResolutionError("document_search_unavailable") from exc

    if not matches:
        return None
    best_rank = min(matches.values())
    best = [path for path, rank in matches.items() if rank == best_rank]
    if len(best) != 1:
        raise DocumentResolutionError("document_ambiguous")
    return best[0]
