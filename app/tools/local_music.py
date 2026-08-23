from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.observability.logging import get_logger

log = get_logger(__name__)

LOCAL_AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".ogg"})
_ARTIST_TITLE_SEP = re.compile(r"\s[-–]\s", re.UNICODE)
_TRACK_NUMBER_RE = re.compile(r"^\d{1,3}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]

_scan_cache: dict[str, tuple[tuple[int, float], list["LocalTrack"]]] = {}


@dataclass(frozen=True)
class LocalTrack:
    artist: str
    title: str
    path: Path
    stem: str

    @property
    def search_text(self) -> str:
        parts = [self.title, self.artist, self.stem]
        return " ".join(p for p in parts if p)


def music_local_root(configured: str) -> Path | None:
    raw = (configured or "").strip()
    if not raw:
        return None
    root = Path(raw)
    if not root.is_absolute():
        root = (_REPO_ROOT / root).resolve()
    else:
        root = root.resolve()
    return root


def _is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_local_music_path(root: Path, candidate: str | Path) -> Path | None:
    """Return an absolute path under *root*, or None if traversal is blocked."""
    root = root.resolve()
    raw = Path(candidate)
    resolved = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not _is_under_root(resolved, root):
        return None
    if not resolved.is_file():
        return None
    if resolved.suffix.casefold() not in LOCAL_AUDIO_EXTENSIONS:
        return None
    return resolved


def _folder_artist(path: Path, root: Path) -> str:
    parent = path.parent.resolve()
    root = root.resolve()
    if parent == root:
        return ""
    name = parent.name.strip()
    if name.startswith("."):
        return ""
    return name


def parse_local_filename(path: Path, *, root: Path | None = None) -> tuple[str, str]:
    """Parse ``Artist - Title.ext`` (or title-only) into (artist, title)."""
    stem = path.stem.replace("_", " ").strip()
    music_root = (root or path.parent).resolve()
    folder_artist = _folder_artist(path, music_root)

    parts = _ARTIST_TITLE_SEP.split(stem, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        if _TRACK_NUMBER_RE.match(left):
            return folder_artist, right or stem
        if left and right:
            return left, right

    if folder_artist:
        return folder_artist, stem
    return "", stem


def _dir_signature(root: Path) -> tuple[int, float]:
    if not root.is_dir():
        return 0, 0.0
    count = 0
    max_mtime = root.stat().st_mtime
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.casefold() not in LOCAL_AUDIO_EXTENSIONS:
            continue
        count += 1
        try:
            max_mtime = max(max_mtime, path.stat().st_mtime)
        except OSError:
            continue
    return count, max_mtime


def scan_local_music(root: Path, *, force: bool = False) -> list[LocalTrack]:
    """Discover audio files under *root*, using a lightweight mtime cache."""
    root = root.resolve()
    key = str(root)
    sig = _dir_signature(root)
    cached = _scan_cache.get(key)
    if not force and cached is not None and cached[0] == sig:
        return list(cached[1])

    tracks: list[LocalTrack] = []
    if not root.is_dir():
        _scan_cache[key] = (sig, tracks)
        return tracks

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.casefold() not in LOCAL_AUDIO_EXTENSIONS:
            continue
        resolved = path.resolve()
        if not _is_under_root(resolved, root):
            log.warning("music.local_skipped", path=str(path), reason="outside_root")
            continue
        artist, title = parse_local_filename(resolved, root=root)
        if not title:
            continue
        tracks.append(
            LocalTrack(
                artist=artist,
                title=title,
                path=resolved,
                stem=resolved.stem.replace("_", " ").strip(),
            )
        )

    _scan_cache[key] = (sig, tracks)
    log.info("music.local_scan", root=str(root), count=len(tracks))
    return tracks


def invalidate_local_music_cache(root: Path | None = None) -> None:
    if root is None:
        _scan_cache.clear()
        return
    _scan_cache.pop(str(root.resolve()), None)
