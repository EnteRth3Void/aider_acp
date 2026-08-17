import os

from acp.schema import ModelInfo, SessionModelState
from aider.models import Model

AIDER_ACP_MODELS_ENV = "AIDER_ACP_MODELS"


def parse_catalog_env() -> list[str]:
    raw = os.environ.get(AIDER_ACP_MODELS_ENV, "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def model_api_key_status(model_id: str) -> tuple[bool, list[str]]:
    model = Model(model_id)
    env = model.validate_environment()
    missing = list(env.get("missing_keys") or model.missing_keys or [])
    keys_ok = bool(env.get("keys_in_environment") or model.keys_in_environment)
    if keys_ok and not missing:
        return True, []
    return False, missing


def filter_available_models(model_ids: list[str]) -> list[str]:
    return [m for m in model_ids if model_api_key_status(m)[0]]


def catalog_error_message(configured: list[str]) -> str:
    if not configured:
        return (
            f"{AIDER_ACP_MODELS_ENV} is not set or empty. "
            "Add it to agent_servers.env with comma-separated LiteLLM/Aider model IDs "
            "(e.g. gpt-4o,anthropic/claude-sonnet-4-20250514) and set the matching API key "
            "environment variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)."
        )

    details: list[str] = []
    for model_id in configured:
        ok, missing = model_api_key_status(model_id)
        if not ok:
            if missing:
                details.append(f"{model_id} requires {', '.join(missing)}")
            else:
                details.append(f"{model_id} has no API key configured")

    return (
        f"No models from {AIDER_ACP_MODELS_ENV} have API keys configured. "
        f"Configured models: {', '.join(configured)}. "
        + "; ".join(details)
        + ". Set the required API key environment variables in agent_servers.env."
    )


def build_session_model_state(
    model_ids: list[str], current_model_id: str
) -> SessionModelState:
    return SessionModelState(
        available_models=[ModelInfo(model_id=m, name=m) for m in model_ids],
        current_model_id=current_model_id,
    )
