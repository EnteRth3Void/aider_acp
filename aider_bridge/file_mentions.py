import logging
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from aider.utils import safe_abs_path

from .ignore import WorkspaceIgnore

logger = logging.getLogger(__name__)

# @ must follow whitespace or start of string (avoids matching user@example.com)
AT_MENTION_RE = re.compile(r"(?<!\S)@([^\s@]+)")
# Do not strip "." — that is part of file extensions (e.g. notes.md)
TRAILING_PUNCT = ",;:!?)]}\"'"


def _clean_path_ref(path_ref: str) -> str:
    path_ref = path_ref.rstrip(TRAILING_PUNCT)
    # Strip one trailing sentence period: "@notes.md." -> "notes.md"
    if path_ref.endswith(".") and path_ref.count(".") > 1:
        path_ref = path_ref[:-1]
    return path_ref


def extract_at_mentions(text: str) -> list[str]:
    mentions = []
    for match in AT_MENTION_RE.finditer(text):
        path_ref = _clean_path_ref(match.group(1))
        if path_ref:
            mentions.append(path_ref)
    return mentions


def uri_to_abs_path(uri: str) -> Optional[str]:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = str(safe_abs_path(unquote(parsed.path)))
        if Path(path).is_file():
            return path
    if not parsed.scheme and Path(uri).is_absolute():
        path = Path(uri)
        if path.is_file():
            return str(safe_abs_path(path))
    return None


def _is_basename_only(path_ref: str) -> bool:
    return "/" not in path_ref and "\\" not in path_ref


def find_file_by_basename(
    name: str,
    search_roots: list[Path],
) -> tuple[Optional[str], list[str]]:
    matches: list[str] = []
    for root in search_roots:
        if not root.is_dir():
            continue
        ignore = WorkspaceIgnore.for_root(root)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            rel_dir = Path(dirpath).relative_to(root)
            dirnames[:] = [
                d
                for d in dirnames
                if not ignore.skip(
                    str(rel_dir / d) if str(rel_dir) != "." else d,
                    is_dir=True,
                )
            ]
            if name not in filenames:
                continue
            matches.append(str(safe_abs_path(Path(dirpath) / name)))

    if not matches:
        return None, []

    matches = sorted(set(matches), key=lambda p: (p.count(os.sep), len(p)))
    chosen = matches[0]
    if len(matches) > 1:
        logger.warning(
            "[paths] find_file_by_basename ambiguous name=%r matches=%s using=%s",
            name,
            matches,
            chosen,
        )
    else:
        logger.info("[paths] find_file_by_basename name=%r -> %s", name, chosen)
    return chosen, matches


def resolve_to_abs_path(
    path_ref: str,
    cwd: str,
    additional_directories: Optional[list[str]] = None,
) -> tuple[Optional[str], list[str]]:
    """Return (absolute path, ambiguous matches) for a @-mention path reference."""
    path_ref = path_ref.strip().strip("\"'`")
    if not path_ref:
        return None, []

    candidate = Path(path_ref)
    if candidate.is_absolute() and candidate.is_file():
        abs_path = str(safe_abs_path(candidate))
        logger.info(
            "[paths] resolve_to_abs_path absolute hit path_ref=%r -> %s",
            path_ref,
            abs_path,
        )
        return abs_path, []

    search_roots = [Path(cwd)] + [Path(d) for d in (additional_directories or [])]
    logger.info(
        "[paths] resolve_to_abs_path path_ref=%r cwd=%s search_roots=%s",
        path_ref,
        cwd,
        [str(r) for r in search_roots],
    )
    for root in search_roots:
        resolved = root / path_ref
        if resolved.is_file():
            abs_path = str(safe_abs_path(resolved))
            logger.info(
                "[paths] resolve_to_abs_path hit root=%s -> %s",
                root,
                abs_path,
            )
            return abs_path, []

    if _is_basename_only(path_ref):
        found, matches = find_file_by_basename(path_ref, search_roots)
        if found:
            return found, matches

    logger.info("[paths] resolve_to_abs_path miss path_ref=%r cwd=%s", path_ref, cwd)
    return None, []


def resolve_resource_path(
    name: str,
    uri: str,
    cwd: str,
    additional_directories: Optional[list[str]] = None,
) -> Optional[str]:
    """Prefer explicit filename from the client over URI (URI may point at stale git paths)."""
    if name:
        resolved, _ = resolve_to_abs_path(name, cwd, additional_directories)
        if resolved:
            return resolved
    return uri_to_abs_path(uri)


def ignore_conflicting_mentions(coder, resolved_abs_paths: list[str]) -> None:
    """Ignore git-tracked ghost files that share a stem but differ in extension."""
    resolved_rels = set()
    for abs_path in resolved_abs_paths:
        try:
            resolved_rels.add(coder.get_rel_fname(abs_path))
        except Exception:
            resolved_rels.add(str(Path(abs_path).relative_to(coder.root)))

    resolved_stems = {Path(rel).stem for rel in resolved_rels}
    for addable in coder.get_addable_relative_files():
        if Path(addable).stem in resolved_stems and addable not in resolved_rels:
            coder.ignore_mentions.add(addable)


def add_files_to_coder(coder, abs_paths: list[str]) -> list[str]:
    added = []
    for abs_path in abs_paths:
        if abs_path in coder.abs_fnames:
            continue
        if not Path(abs_path).is_file():
            coder.io.tool_warning(f"File not found, skipping: {abs_path}")
            continue
        coder.abs_fnames.add(abs_path)
        added.append(abs_path)
    if added:
        coder.check_added_files()
        names = []
        for abs_path in added:
            try:
                names.append(coder.get_rel_fname(abs_path))
            except Exception:
                names.append(abs_path)
        coder.io.tool_output(
            "\n".join(f"Added {name} to the chat" for name in names)
        )
        ignore_conflicting_mentions(coder, added)
    return added


def resolve_command_file_args(
    resource_names: list[str],
    cwd: str,
    additional_directories: Optional[list[str]] = None,
    io=None,
) -> list[str]:
    """Resolve Zed file attachments to abs paths for slash commands (/add, /drop, /read-only).

    Slash commands are dispatched as raw text to Aider's command parser, which
    never sees ACP resource blocks. This lets attachments work as if the user
    had typed the path after the command.
    """
    abs_paths = []
    for name in resource_names:
        if not name:
            continue
        abs_path, ambiguous = resolve_to_abs_path(name, cwd, additional_directories)
        if abs_path:
            abs_paths.append(abs_path)
            if len(ambiguous) > 1 and io is not None:
                rel_matches = [os.path.relpath(m, cwd) for m in ambiguous]
                io.tool_warning(
                    f"Multiple files named {name}: {', '.join(rel_matches)}. "
                    f"Using {os.path.relpath(abs_path, cwd)}."
                )
        elif io is not None:
            io.tool_warning(f"Could not resolve attachment {name}")
    return abs_paths


def apply_at_mentions(
    coder,
    prompt_text: str,
    cwd: str,
    additional_directories: Optional[list[str]] = None,
    extra_mentions: Optional[list[str]] = None,
) -> list[str]:
    mentions = extract_at_mentions(prompt_text)
    for name in extra_mentions or []:
        if name and name not in mentions:
            mentions.append(name)

    if not mentions:
        return []

    logger.info(
        "[paths] apply_at_mentions cwd=%s coder.root=%s mentions=%r additional_directories=%r",
        cwd,
        coder.root,
        mentions,
        additional_directories,
    )

    abs_paths = []
    for path_ref in mentions:
        abs_path, ambiguous = resolve_to_abs_path(
            path_ref, cwd, additional_directories
        )
        if abs_path:
            abs_paths.append(abs_path)
            if len(ambiguous) > 1:
                rel_matches = [os.path.relpath(m, cwd) for m in ambiguous]
                coder.io.tool_warning(
                    f"Multiple files named {path_ref}: {', '.join(rel_matches)}. "
                    f"Using {os.path.relpath(abs_path, cwd)}."
                )
        else:
            coder.io.tool_warning(f"Could not resolve @{path_ref}")

    return add_files_to_coder(coder, abs_paths)


def patch_coder_file_mentions(coder) -> None:
    """Skip aider prompts for missing files and @ mentions we already handle."""
    original = coder.check_for_file_mentions

    def check_for_file_mentions(content):
        for rel_fname in coder.get_file_mentions(content):
            abs_path = coder.abs_root_path(rel_fname)
            if not Path(abs_path).is_file():
                coder.ignore_mentions.add(rel_fname)
        return original(content)

    coder.check_for_file_mentions = check_for_file_mentions
