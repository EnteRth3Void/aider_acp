import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ADAPTER_SETTINGS_NAME = "aider_acp.toml"
PROJECT_SETTINGS_NAME = ".aider_acp.toml"
SETTINGS_ENV = "AIDER_ACP_SETTINGS"


@dataclass
class WorkspaceSettings:
    skip_dotfiles: bool = True
    honor_gitignore: bool = True
    honor_aiderignore: bool = True
    ignore: list[str] = field(default_factory=list)


def adapter_settings_path() -> Path:
    env_path = os.environ.get(SETTINGS_ENV)
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parent.parent / ADAPTER_SETTINGS_NAME


def _section(data: dict) -> dict:
    section = data.get("workspace")
    return section if isinstance(section, dict) else {}


def _from_mapping(data: dict) -> WorkspaceSettings:
    section = _section(data)
    ignore = section.get("ignore", [])
    if not isinstance(ignore, list):
        ignore = []
    return WorkspaceSettings(
        skip_dotfiles=bool(section.get("skip_dotfiles", True)),
        honor_gitignore=bool(section.get("honor_gitignore", True)),
        honor_aiderignore=bool(section.get("honor_aiderignore", True)),
        ignore=[str(item) for item in ignore],
    )


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Could not read settings %s: %s", path, exc)
        return {}


def load_workspace_settings(project_root: str | os.PathLike[str]) -> WorkspaceSettings:
    """Adapter defaults, then extra ignore patterns from the project file."""
    adapter_path = adapter_settings_path()
    settings = _from_mapping(_read_toml(adapter_path))
    if not adapter_path.is_file():
        logger.warning("Adapter settings file missing: %s", adapter_path)

    project_path = Path(project_root) / PROJECT_SETTINGS_NAME
    project_data = _read_toml(project_path)
    section = _section(project_data)
    if project_path.is_file():
        extra = section.get("ignore", [])
        if isinstance(extra, list):
            settings.ignore.extend(str(item) for item in extra)
        if "skip_dotfiles" in section:
            settings.skip_dotfiles = bool(section["skip_dotfiles"])
        if "honor_gitignore" in section:
            settings.honor_gitignore = bool(section["honor_gitignore"])
        if "honor_aiderignore" in section:
            settings.honor_aiderignore = bool(section["honor_aiderignore"])

    logger.info(
        "[paths] workspace settings skip_dotfiles=%s honor_gitignore=%s "
        "honor_aiderignore=%s ignore_count=%d adapter=%s project=%s",
        settings.skip_dotfiles,
        settings.honor_gitignore,
        settings.honor_aiderignore,
        len(settings.ignore),
        adapter_path,
        project_path if project_path.is_file() else None,
    )
    return settings
