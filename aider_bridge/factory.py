import logging
import os

from aider.coders import Coder
from aider.models import Model, sanity_check_model
from aider.utils import safe_abs_path

from .file_mentions import patch_coder_file_mentions
from .io_bridge import ACPIO
from .shell_commands import patch_run_cmd

logger = logging.getLogger(__name__)

CODER_CREATE_KWARGS = {
    "auto_commits": False,
    "dirty_commits": False,
    "use_git": True,
    "map_tokens": 0,
    "stream": False,
    "suggest_shell_commands": False,
    "auto_lint": False,
    "auto_test": False,
}


def check_model_keys(io: ACPIO, model_name: str) -> bool:
    """Cheap per-turn guard: block the prompt if the model's API key is missing."""
    model = Model(model_name)
    model.validate_environment()
    if model.missing_keys:
        keys = ", ".join(model.missing_keys)
        io.tool_error(
            f"Cannot use model {model_name}: missing environment variable(s): {keys}"
        )
        return False
    return True


def _finalize_coder(
    coder: Coder, io: ACPIO, cwd: str | None, from_coder: Coder | None
) -> Coder:
    patch_coder_file_mentions(coder)

    root = safe_abs_path(cwd or io.root or os.getcwd())
    coder.root = root
    coder.abs_root_path_cache.clear()

    logger.debug(
        "[paths] create_coder done coder.root=%s io.root=%s getcwd=%s coder.repo=%s inchat_files=%s",
        coder.root,
        io.root,
        os.getcwd(),
        coder.repo,
        coder.get_inchat_relative_files(),
    )
    return coder


def create_coder(
    io: ACPIO,
    model_name: str,
    cwd: str | None = None,
    from_coder: Coder | None = None,
) -> Coder:
    patch_run_cmd(io)
    model = Model(model_name)
    # Emit "unknown context window", "did you mean", etc. once per coder build
    # (initial init or model switch), not on every prompt.
    sanity_check_model(io, model)

    logger.debug(
        "[paths] create_coder start cwd=%r io.root=%r getcwd=%s from_coder=%s",
        cwd,
        io.root,
        os.getcwd(),
        from_coder is not None,
    )

    coder = Coder.create(
        main_model=model,
        io=io,
        from_coder=from_coder,
        **CODER_CREATE_KWARGS,
    )
    return _finalize_coder(coder, io, cwd, from_coder)


def switch_coder(
    io: ACPIO, old_coder: Coder, cwd: str | None = None, **switch_kwargs
) -> Coder:
    """Rebuild the coder after Aider raises SwitchCoder.

    Matches Aider CLI: start from the current coder, then let switch.kwargs
    override (including from_coder for /ask|/help one-shot modes).
    """
    patch_run_cmd(io)
    kwargs = dict(io=io, from_coder=old_coder, **CODER_CREATE_KWARGS)
    kwargs.update(switch_kwargs)
    # SwitchCoder meta flag, not a Coder.create argument (see aider/main.py).
    kwargs.pop("show_announcements", None)
    from_coder = kwargs.get("from_coder")
    coder = Coder.create(**kwargs)
    return _finalize_coder(coder, io, cwd, from_coder=from_coder)
