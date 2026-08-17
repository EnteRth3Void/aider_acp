import logging
import os
from pathlib import Path

from aider.utils import safe_abs_path

from .ignore import WorkspaceIgnore

logger = logging.getLogger(__name__)


def _git_skips_file(rel_path: str, coder) -> bool:
    if not coder.repo:
        return False
    try:
        git_rel = os.path.relpath(
            safe_abs_path(Path(coder.root) / rel_path),
            coder.repo.root,
        )
    except ValueError:
        return True
    if coder.repo.git_ignored_file(git_rel):
        return True
    if coder.repo.ignored_file(git_rel):
        return True
    return False


def iter_workspace_relative_files(
    root: str,
    coder,
    ignore: WorkspaceIgnore | None = None,
) -> list[str]:
    """Return relative file paths under root, applying settings/gitignore."""
    root_path = Path(root)
    ignore = ignore or WorkspaceIgnore.for_root(root)
    files: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        rel_dir = Path(dirpath).relative_to(root_path)
        kept_dirs = []
        for dirname in dirnames:
            rel_subdir = str(rel_dir / dirname) if str(rel_dir) != "." else dirname
            if ignore.skip(rel_subdir, is_dir=True):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            rel_path = str(rel_dir / filename) if str(rel_dir) != "." else filename
            if ignore.skip(rel_path, is_dir=False):
                continue
            if _git_skips_file(rel_path, coder):
                continue
            abs_path = root_path / rel_path
            if not abs_path.is_file():
                continue
            files.append(rel_path)

    return sorted(set(files))


def add_all_workspace_files(coder, root: str) -> list[str]:
    """Add every workspace file to the coder, respecting ignore settings."""
    workspace_files = iter_workspace_relative_files(root, coder)
    logger.info(
        "[paths] add_all_workspace_files root=%s repo=%s file_count=%d",
        root,
        bool(coder.repo),
        len(workspace_files),
    )

    for rel_fname in workspace_files:
        coder.add_rel_fname(rel_fname)

    logger.info("[paths] coder.abs_fnames=%s", coder.abs_fnames)
    logger.info(
        "[paths] coder.get_inchat_relative_files()=%s",
        coder.get_inchat_relative_files(),
    )
    return workspace_files
