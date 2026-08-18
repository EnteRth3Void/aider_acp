# Aider ACP

**Preview** — not production-ready; API and behavior may change.

Adapter that runs [Aider](https://aider.chat) as an External Agent in [Zed](https://zed.dev) over the [Agent Client Protocol](https://agentclientprotocol.com) (JSON-RPC on stdin/stdout).

Aider is a CLI pair-programming agent: you put files in context, it proposes edits, you iterate. This adapter keeps that loop, but hosts it in Zed's Agent Panel instead of a terminal.

The point is Cursor-style review inside Zed. Aider still plans and applies edits, but they do not hit disk immediately. They go into Zed's review multibuffer so you can accept or reject each file in the editor. Token usage shows up in the agent UI. Slash commands work in the thread; `@` mentions add the file to chat context (same as `/add`).

This is the **review** variant. Aider does not auto-commit. A later git variant (write to disk, commit like the CLI) is sketched in [`zed-acp-git-und-v2.md`](zed-acp-git-und-v2.md), not implemented.

## What works

**Review in Zed.** During a turn, writes stay in an overlay. Disk is unchanged until the turn finishes. Then each file is sent with `fs/write_text_file` plus a diff `tool_call`. Zed opens Review Changes; accept or reject happens per file in the editor. If Zed advertised `fs.readTextFile`, Aider reads unsaved buffers instead of disk. SEARCH/REPLACE blocks are stripped from chat so you see the diff, not the raw edit markup.

**Token usage.** After each prompt the adapter sends `usage_update` (tokens sent vs. the model's context window, plus session cost in USD). Zed shows that in the agent UI. Aider's own `Tokens: …` lines are suppressed so they do not duplicate the meter.

**Models.** The list comes from `AIDER_ACP_MODELS` (comma-separated LiteLLM/Aider IDs). Entries without a matching API key are dropped from the list. There is no implicit default — without that env var the agent cannot start a session. On `session/new` the first model in `AIDER_ACP_MODELS` is selected. The ACP model picker is implemented (`models` in the session response, `session/set_model`), but Zed currently does not render it for this agent ([zed#59197](https://github.com/zed-industries/zed/issues/59197)). Switch with `/model`, `/weak-model`, or `/editor-model`. After init or a switch, the thread posts a line like `Model: gpt-4o  (weak: gpt-4o-mini, editor: gpt-4o)`.

**Empty context.** A thread starts with no files. `@path` (basename search walks the project) and Zed file attachments add the file to the chat automatically — same as `/add`. `/read-only` adds without allowing edits. Without files, Aider may still reply in chat instead of finishing silently.

**Slash commands.** After `session/new` (and again on the first prompt) Zed gets `available_commands_update`, so the `/` menu lists Aider commands that work without git. Git-only commands (`/commit`, `/diff`, `/undo`, `/git`, `/lint`) and host-only ones (clipboard, `$EDITOR`, `/voice`, `/quit`) are omitted and rejected if typed. `/run` stdout lands in the thread.

**Stop and permissions.** The Agent Panel stop button maps to `session/cancel` (drops the overlay, unblocks `confirm_ask`). Aider yes/no questions go through Zed's permission UI (`allow once` / `reject once`). Session list, close, and extra directories are advertised in `initialize` so Zed can call those handlers.

If the client does not advertise `fs.writeTextFile`, the overlay flushes to disk instead of the review buffer.

## Limitations

Zed's model picker does not render for this agent ([zed#59197](https://github.com/zed-industries/zed/issues/59197)). There is no thread import, no auth UI, and no git mode. Parallel Zed threads are risky because each session uses `os.chdir`.

## Setup

Python 3.12+ (see [`.python-version`](.python-version)) and API keys for the LLM providers you want to use.

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

`AIDER_ACP_MODELS` is a comma-separated list of LiteLLM/Aider model IDs. The first entry is the session default; change it with `/model`. Entries whose API key environment variable is not set are unusable (for example `gpt-4o` needs `OPENAI_API_KEY`).

Open the agent thread in the **project you want to edit**, not in this adapter repo. Restart the agent in Zed after changing adapter code.

Zed intercepts messages that start with `/` until it has the command list. If you still see `Available commands: none`, put a space before the slash so Zed treats it as a normal prompt, for example ` /model gpt-4.1`.

## Logging

Default is `warning`: `aider_acp.log` (next to the adapter, not the project) has no chat prompts or assistant replies. stdout stays reserved for ACP JSON-RPC.

For bug reports, set `AIDER_ACP_LOG_LEVEL=debug` in the agent env, restart the agent, and attach `aider_acp.log`. That complements Zed's `dev: open acp logs` (wire protocol vs adapter internals). `off` disables file and stderr logging. Invalid values fall back to `warning`.

## @ mentions

Chat context stays empty until you `@` or `/add` a file. An explicit path is added as-is. A bare `@filename` walks the project and skips dotfiles plus the project's `.gitignore` and `.aiderignore` so the search does not descend into `.venv` or `node_modules`. That walk does not put those files in chat.

## Tests

```bash
.venv/bin/python -m unittest test_overlay.py test_models.py test_commands.py test_initialize.py test_logging.py -v
```

## License

Apache-2.0. This is a **preview**; API and behavior may change.

Design notes (not a public roadmap): [`zed-acp-planned.md`](zed-acp-planned.md), [`zed-acp-git-und-v2.md`](zed-acp-git-und-v2.md).
