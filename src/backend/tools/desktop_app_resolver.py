"""Bounded, read-only resolution of local desktop entries and Start Menu shortcuts."""

import ctypes
import json
import os
import re
import subprocess
import unicodedata
from itertools import islice
from pathlib import Path

MAX_SEARCH_ENTRIES = 1024
MAX_START_MENU_DEPTH = 3
MAX_REGISTERED_APPS = 512
_WINDOWS_ABSOLUTE = re.compile(r"^[a-z]:[/\\]", re.IGNORECASE)
_APP_SUFFIXES = {".exe", ".lnk", ".url"}
_SKIP_DIRECTORIES = {"venv", "node_modules", ".git", "target", "build", "scratch"}
_APP_URI = re.compile(
    r"(?:spotify|discord|steam|roblox-player|minecraft|com\.epicgames\.launcher|whatsapp|tg|ms-settings):[^\s]*",
    re.IGNORECASE,
)


class ApplicationResolutionError(ValueError):
    """A name cannot be safely or uniquely resolved within the search budget."""


def validate_application_name(name: str, *, allow_posix_absolute: bool = False) -> bool:
    """Validate a literal name or shallow desktop path; return whether it is an app URI."""
    if len(name) > 240 or re.search(r'[\x00-\x1f\x7f"&|;<>`$%*?]', name):
        raise ApplicationResolutionError("app_invalid_name")
    if _APP_URI.fullmatch(name):
        if ".." in name or "\\" in name:
            raise ApplicationResolutionError("app_invalid_name")
        return True
    windows_absolute = bool(_WINDOWS_ABSOLUTE.match(name))
    posix_absolute = allow_posix_absolute and name.startswith("/") and not name.startswith("//") and "\\" not in name
    if windows_absolute:
        tail = name[3:].removesuffix("\\").removesuffix("/")
        parts = re.split(r"[/\\]", tail) if tail else []
    else:
        parts = name.split("/")[1:] if posix_absolute else re.split(r"[/\\]", name)
    if (
        (len(parts) > 2 and not (posix_absolute or windows_absolute))
        or ":" in (name[2:] if windows_absolute else name)
        or re.search(r"(?:^|\s)-", name)
        or re.search(r"\.(?:exe|lnk|url)\s+\S", name, re.IGNORECASE)
        or re.match(r"(?:cmd|powershell|pwsh|bash|sh)(?:\.exe)?\s", name, re.IGNORECASE)
        or any(not part or part in {".", ".."} or part != part.strip()
               or part.startswith(("~", "'")) or part.endswith((".", "'")) for part in parts)
        or Path(parts[-1] if parts else "").suffix.lower() in {".bat", ".cmd", ".ps1", ".vbs", ".js", ".py", ".sh"}
    ):
        raise ApplicationResolutionError("app_invalid_name")
    return False


def _is_local_windows_drive(anchor: str) -> bool:
    try:
        get_drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        return get_drive_type(anchor) in {2, 3, 5, 6}
    except (AttributeError, OSError):
        return False


def _resolve_absolute_application(name: str) -> Path | None:
    requested = Path(name)
    if not requested.is_absolute() or not _is_local_windows_drive(requested.anchor):
        raise ApplicationResolutionError("app_invalid_name")
    try:
        target = requested.resolve(strict=True)
        if not _WINDOWS_ABSOLUTE.match(str(target)) or not _is_local_windows_drive(target.anchor):
            raise ApplicationResolutionError("app_invalid_name")
        if target.is_dir() or (target.is_file() and target.suffix.lower() in _APP_SUFFIXES):
            return target
        raise ApplicationResolutionError("app_invalid_name")
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError) as exc:
        raise ApplicationResolutionError("app_search_unavailable") from exc


def _normalized_name(name: str) -> str:
    """Ignore word separators, retaining accents and other meaningful punctuation."""
    return "".join(
        char for char in unicodedata.normalize("NFC", name.casefold())
        if not (char.isspace() or char == "_" or unicodedata.category(char) == "Pd")
    )


def _name_rank(name: str, requested: str) -> int | None:
    name, requested = name.casefold(), requested.casefold()
    if name == requested:
        return 0
    stem = name.rsplit(".", 1)[0] if Path(name).suffix in _APP_SUFFIXES else name
    if stem == requested:
        return 1
    normalized = _normalized_name(requested)
    if normalized and normalized in {_normalized_name(name), _normalized_name(stem)}:
        return 2
    words = [word for word in requested.split() if len(word) > 1]
    if len(requested) > 1 and (requested in stem or (words and all(word in stem for word in words))):
        return 3
    return None


def application_search_roots() -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    configured = os.environ.get("JARVIS_DESKTOP_HOME", "").strip()
    desktops = [Path(configured)] if configured else [home / "Desktop", home / "Escritorio"]
    desktops.append(Path(os.environ.get("PUBLIC") or r"C:\Users\Public") / "Desktop")
    shortcuts = (
        Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData")
        / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    )
    return tuple(desktops), shortcuts


def resolve_local_application(name: str) -> Path | None:
    """Prefer exact names, unique normalized names, then apps over fuzzy folders.

    Root priority applies to literal and fuzzy matches, never normalized collisions.
    Distinct executables in the same root retain ambiguity, including GUI/console pairs.
    """
    if validate_application_name(name):
        return None
    if _WINDOWS_ABSOLUTE.match(name):
        return _resolve_absolute_application(name)
    desktops, shortcut_roots = application_search_roots()
    parts = re.split(r"[/\\]", name)
    requested = parts[-1]
    directory_query = Path(requested).stem if Path(requested).suffix.lower() in _APP_SUFFIXES else requested
    remaining = MAX_SEARCH_ENTRIES
    matches: dict[Path, tuple[int, int, int]] = {}

    def entries(directory: Path, root: Path) -> list[Path]:
        nonlocal remaining
        if not directory.resolve().is_relative_to(root):
            return []
        children = list(islice(directory.iterdir(), remaining + 1))
        if len(children) > remaining:
            raise ApplicationResolutionError("app_search_limit")
        remaining -= len(children)
        return sorted(children, key=lambda item: (item.name.casefold(), item.name))

    def add(entry: Path, root: Path, scope: int, *, folders: bool = False, parent_rank: int = 0) -> None:
        resolved = entry.resolve()
        if not resolved.is_relative_to(root):
            return
        is_directory = entry.is_dir()
        if not ((folders and is_directory) or (entry.is_file() and entry.suffix.lower() in _APP_SUFFIXES)):
            return
        rank = _name_rank(entry.name, requested)
        if rank is not None:
            normalized_match = rank == 2 or parent_rank == 2
            rank = max(rank, parent_rank)
            score = (rank, int(rank == 3 and is_directory), 0 if normalized_match else scope)
            matches[resolved] = min(score, matches.get(resolved, score))

    try:
        for scope, desktop in enumerate(dict.fromkeys(desktops)):
            if not desktop.is_dir():
                continue
            root = desktop.resolve()
            for entry in entries(root, root):
                if len(parts) == 2:
                    parent_rank = _name_rank(entry.name, parts[0])
                    if entry.is_dir() and parent_rank in {0, 2}:
                        for child in entries(entry, root):
                            add(child, root, scope, folders=True, parent_rank=parent_rank)
                    continue
                add(entry, root, scope, folders=True)
                if (entry.is_dir() and not entry.name.startswith(".")
                        and entry.name.casefold() not in _SKIP_DIRECTORIES
                        and entry.name.casefold() != requested.casefold()
                        and _name_rank(entry.name, directory_query) is not None):
                    for child in entries(entry, root):
                        add(child, root, scope)

        if len(parts) == 1:
            for scope, shortcut_root in enumerate(dict.fromkeys(shortcut_roots), start=len(desktops)):
                if not shortcut_root.is_dir():
                    continue
                root = shortcut_root.resolve()
                pending = [(root, 0)]
                while pending:
                    directory, depth = pending.pop()
                    for entry in entries(directory, root):
                        if entry.is_file() and entry.suffix.lower() in {".lnk", ".url"}:
                            add(entry, root, scope)
                        elif (entry.is_dir() and depth < MAX_START_MENU_DEPTH
                              and not entry.name.startswith(".")
                              and entry.name.casefold() not in _SKIP_DIRECTORIES):
                            pending.append((entry, depth + 1))
    except (OSError, RuntimeError) as exc:
        raise ApplicationResolutionError("app_search_unavailable") from exc

    if not matches:
        return None
    best_rank = min(matches.values())
    best = [path for path, rank in matches.items() if rank == best_rank]
    if len(best) != 1:
        raise ApplicationResolutionError("app_ambiguous")
    return best[0]


def resolve_registered_application(name: str) -> str | None:
    """Resolve an exact or unique normalized packaged-app name without shell interpolation."""
    if validate_application_name(name) or re.search(r"[/\\:]", name):
        return None
    script = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        f"Get-StartApps | Select-Object -First {MAX_REGISTERED_APPS + 1} -Property Name,AppID "
        "| ConvertTo-Json -Compress"
    )
    try:
        response = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", timeout=5, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        payload = json.loads(response.stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    records = [payload] if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return None
    if len(records) > MAX_REGISTERED_APPS:
        raise ApplicationResolutionError("app_search_limit")
    candidates = [
        record for record in records
        if isinstance(record, dict) and isinstance(record.get("Name"), str)
        and isinstance(record.get("AppID"), str)
        and re.fullmatch(r"[a-zA-Z0-9._-]+![a-zA-Z0-9._-]+", record["AppID"])
    ]
    exact = {record["AppID"] for record in candidates if record["Name"].casefold() == name.casefold()}
    normalized = _normalized_name(name)
    ids = exact or {
        record["AppID"] for record in candidates
        if normalized and _normalized_name(record["Name"]) == normalized
    }
    if len(ids) > 1:
        raise ApplicationResolutionError("app_ambiguous")
    return f"shell:AppsFolder\\{next(iter(ids))}" if ids else None
