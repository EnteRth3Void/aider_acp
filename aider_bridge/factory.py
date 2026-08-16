import logging
import os

from aider.coders import Coder
from aider.models import Model
from aider.utils import safe_abs_path

from .file_mentions import patch_coder_file_mentions
from .io_bridge import ACPIO
from .shell_commands import patch_run_cmd
from .workspace_files import add_all_workspace_files

logger = logging.getLogger(__name__)


def create_coder(io: ACPIO, model_name: str = "gpt-4o", cwd: str | None = None):
    patch_run_cmd(io)
    # Note: Aider will try to load the model.
    # Ensure the environment variables for the provider are set.
    model = Model(model_name)

    logger.info(
        "[paths] create_coder start cwd=%r io.root=%r getcwd=%s",
        cwd,
        io.root,
        os.getcwd(),
    )

    # We use Coder.create to get the appropriate coder class based on the model/edit format
    coder = Coder.create(
        main_model=model,
        io=io,
        auto_commits=False,
        dirty_commits=False,
        use_git=True,
        map_tokens=0,
    )
    patch_coder_file_mentions(coder)

    # ACP session cwd is the base for relative paths (not derived from git).
    root = safe_abs_path(cwd or io.root or os.getcwd())
    coder.root = root
    coder.abs_root_path_cache.clear()

    add_all_workspace_files(coder, root)

    logger.info(
        "[paths] create_coder done coder.root=%s io.root=%s getcwd=%s coder.repo=%s inchat_files=%s",
        coder.root,
        io.root,
        os.getcwd(),
        coder.repo,
        coder.get_inchat_relative_files(),
    )

    return coder
