# Aider ACP

Adapter that runs [Aider](https://aider.chat) in Zed as an External Agent over ACP (stdin/stdout JSON-RPC).

Edits do not go straight to disk. They are sent with `fs/write_text_file` into Zed's review multibuffer. Accept or reject happens in the editor.

## Setup

Python 3.12 and API keys for the LLM providers you want to use.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

In Zed: Agent Settings → External Agents → Add Custom Agent, or in `settings.json`:

```json
{
  "agent_servers": {
    "aider": {
      "type": "custom",
      "command": "/absolute/path/to/aider_acp/.venv/bin/python",
      "args": ["/absolute/path/to/aider_acp/main.py"],
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "AIDER_ACP_MODELS": "gpt-4o,anthropic/claude-sonnet-4-20250514"
      }
    }
  }
}
```

`AIDER_ACP_MODELS` is a comma-separated list of LiteLLM/Aider model IDs. Zed's model picker shows only entries from that list whose API key environment variable is set (for example `gpt-4o` needs `OPENAI_API_KEY`). There is no default model — without `AIDER_ACP_MODELS` the agent cannot start a session.

Open the agent thread in the **project you want to edit**, not in this adapter repo. Restart the agent in Zed after changing adapter code.

Logs live next to the adapter (`aider_acp.log`), not in the user project. ACP traffic: command palette → `dev: open acp logs`.

## Ignore

Which files enter the chat is controlled by `aider_acp.toml` (gitignore syntax). The open project also applies:

- `.gitignore` (even without a git repo)
- `.aiderignore`
- `.aider_acp.toml` for extra patterns

To use a different settings file: `AIDER_ACP_SETTINGS=/path/to/file.toml`.

## Tests

```bash
.venv/bin/python -m unittest test_overlay.py test_models.py -v
```

## Internal notes

- [`zed-acp-luecken.md`](zed-acp-luecken.md) — ACP/Zed gaps not wired up yet
- [`zed-acp-git-und-v2.md`](zed-acp-git-und-v2.md) — design notes for git mode and ACP v2
