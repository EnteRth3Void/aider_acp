import logging
from functools import lru_cache
from pathlib import Path

import pathspec

from .settings import WorkspaceSettings, load_workspace_settings

logger = logging.getLogger(__name__)


def _read_pattern_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        logger.warning("Could not read ignore file %s: %s", path, exc)
        return []


def _posix(rel_path: str) -> str:
    posix = rel_path.replace("\\", "/")
    while posix.startswith("./"):
        posix = posix[2:]
    return posix


class WorkspaceIgnore:
    """gitignore-style matcher for workspace ingest and mention search."""

    def __init__(
        self,
        spec: pathspec.PathSpec,
        skip_dotfiles: bool = True,
    ):
        self.spec = spec
        self.skip_dotfiles = skip_dotfiles

    @classmethod
    def from_settings(
        cls,
        settings: WorkspaceSettings,
        project_root: str | Path,
    ) -> "WorkspaceIgnore":
        root = Path(project_root)
        patterns = list(settings.ignore)
        if settings.honor_gitignore:
            patterns.extend(_read_pattern_file(root / ".gitignore"))
        if settings.honor_aiderignore:
            patterns.extend(_read_pattern_file(root / ".aiderignore"))
        spec = pathspec.PathSpec.from_lines("gitignore", patterns)
        return cls(spec=spec, skip_dotfiles=settings.skip_dotfiles)

    @classmethod
    def for_root(cls, project_root: str | Path) -> "WorkspaceIgnore":
        root = str(Path(project_root))
        return _cached_for_root(root)

    def skip(self, rel_path: str, is_dir: bool = False) -> bool:
        posix = _posix(rel_path)
        if not posix:
            return False
        if self.skip_dotfiles and Path(posix).name.startswith("."):
            return True
        if self.spec.match_file(posix):
            return True
        if is_dir and self.spec.match_file(posix + "/"):
            return True
        return False


@lru_cache(maxsize=32)
def _cached_for_root(project_root: str) -> WorkspaceIgnore:
    settings = load_workspace_settings(project_root)
    return WorkspaceIgnore.from_settings(settings, project_root)


def clear_ignore_cache() -> None:
    _cached_for_root.cache_clear()
