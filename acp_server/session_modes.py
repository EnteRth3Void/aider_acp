from acp.schema import SessionMode, SessionModeState
from aider.models import Model

SESSION_MODES = [
    SessionMode(
        id="ask",
        name="Ask",
        description="Ask questions about your code without making any changes.",
    ),
    SessionMode(
        id="code",
        name="Code",
        description="Ask for changes to your code (using the best edit format).",
    ),
    SessionMode(
        id="architect",
        name="Architect",
        description=(
            "Work with an architect model to design code changes, and an editor to make them."
        ),
    ),
]

VALID_MODE_IDS = frozenset(m.id for m in SESSION_MODES)

_SLASH_ONLY_EDIT_FORMATS = frozenset({"help", "context"})


def build_session_mode_state(current_mode_id: str) -> SessionModeState:
    return SessionModeState(
        available_modes=SESSION_MODES,
        current_mode_id=current_mode_id,
    )


def aider_edit_format(mode_id: str, model_name: str) -> str:
    if mode_id == "ask":
        return "ask"
    if mode_id == "architect":
        return "architect"
    if mode_id == "code":
        return Model(model_name).edit_format
    raise ValueError(f"Unknown mode: {mode_id}")


def infer_mode_id(coder) -> str | None:
    edit_format = getattr(coder, "edit_format", None)
    if not edit_format:
        return None
    if edit_format == "ask":
        return "ask"
    if edit_format == "architect":
        return "architect"
    if edit_format in _SLASH_ONLY_EDIT_FORMATS:
        return None
    return "code"
