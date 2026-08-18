# Missing and planned work

As of 2026-08-18. Checked against Zed as an ACP client and the Python SDK `agent-client-protocol` (schema v0.12.2, wire version 1). The server starts with `use_unstable_protocol=True`.

Zed only calls optional methods if the agent advertises them in `initialize`. Without the capability, the UI control stays dead even when a handler exists in code.

---

## 1. Missing

Agent methods with no handler. Zed cannot use them.

### Central for Zed

| Method | What Zed uses it for |
|---|---|
| ~~`session/cancel`~~ | ~~Stop button. Without a handler Aider keeps running.~~ **Done:** `cancel` sets a session event, aborts `confirm_ask`, skips the overlay flush. |
| `session/load` | Thread history and “Import Threads”. Only if `loadSession: true`. |
| `authenticate` | Login in the Agent Panel. Keys currently come from env only. |
| `session/set_mode` | Mode picker (Ask / Code / Architect, etc.). |
| ~~`session/set_model`~~ | ~~Model choice hardcoded to `gpt-4o`.~~ **Done:** list from `AIDER_ACP_MODELS` in `agent_servers.env`; default = first list entry; switch via `set_session_model`. Zed does not render the picker ([zed#59197](https://github.com/zed-industries/zed/issues/59197)) — workaround `/model`. |
| `session/set_config_option` | Session options in the Zed UI. |

### Optional / unstable

| Method | Note |
|---|---|
| `session/resume` | Resume a session without replaying history. |
| `session/fork` | Branch a session. |
| `logout` | End auth state. |
| MCP | Zed’s `mcpServers` are stored, never started. |
| `nes/*` | Next Edit Suggestions. Mostly irrelevant for Aider. |
| `document/didOpen` etc. | Document sync, mainly for NES. |
| `providers/*` | Provider configuration. Mostly irrelevant for Aider. |
| Elicitation | Forms / URL auth via the client. |

### Session updates never sent

Zed renders these types in the Agent Panel; the adapter does not emit all of them yet:

- ~~`tool_call` with `content` of type `diff` — reviewable file edits~~ **Done:** on overlay flush, `start_tool_call(kind="edit")` + `tool_diff_content`, then `fs/write_text_file`.
- ~~`available_commands_update` — slash commands (`/add`, `/commit`, …)~~ **Done:** curated set after `session/new` (no git/host-only commands; `/run` is included).
- `current_mode_update`
- `config_option_update`
- `plan` / `agent_plan`
- ~~`usage_update` — token usage~~ **Done:** after each prompt (sent vs. context window + session cost). Not in `PromptResponse.usage`.
- `session_info_update`
- `user_message_chunk`

### Prompt content Zed can send

Without matching `promptCapabilities`, Zed often does not send these. If it does, they are ignored:

- Images
- Audio
- Embedded resource **body** (filename only today)

---

## 2. Wrong or incomplete

Present, but semantically wrong, incomplete, or invisible to Zed.

### Prompt lifecycle (v1)

~~`session/prompt` must **block** the turn and only then return `stopReason`.~~

**Done:** `session/prompt` blocks until `run_prompt` finishes; `stopReason` is `end_turn` or `cancelled`. `userMessageId` is echoed only when Zed sent `messageId`.

Open: token `usage` on `PromptResponse` is still missing (the UI gets `usage_update`).

### Capabilities not advertised

~~`initialize` returns only `protocolVersion` and `agentInfo`. No `agentCapabilities`, no `authMethods`.~~

**Done:** `initialize` advertises `sessionCapabilities.list`, `.close`, and `.additionalDirectories`. Still not advertised:

- `loadSession: false` (no `session/load` handler)
- no `authMethods`
- no image / audio / embedded context (`promptCapabilities`)
- no MCP (`http`/`sse`)
- no `sessionCapabilities.resume` / `.fork` / modes

~~`session/list` and `session/close` are implemented; per the spec Zed must not call them until `sessionCapabilities.list` / `.close` are set.~~ **Done.**

~~`additionalDirectories` is applied; the capability for it is missing.~~ **Done.**

~~`clientCapabilities` (fs, terminal, …) are received and neither stored nor used.~~ **Partial:** stored; `fs.writeTextFile` / `fs.readTextFile` choose overlay vs. disk. Terminal capability unused.

### `session/new` without UI state

The response is only `sessionId`. Missing:

- `modes` — no mode picker
- ~~`models` — no model choice~~ **Done:** `models` with `availableModels` + `currentModelId` from `AIDER_ACP_MODELS` (default = first entry; entries without an API key are dropped). Zed does not render the picker.
- `configOptions`

Slash commands are advertised after `session/new` via `available_commands_update` (curated, no git).

**Model-picker workaround:** until Zed renders the external-agent picker ([zed#59197](https://github.com/zed-industries/zed/issues/59197), caused by [PR #58308](https://github.com/zed-industries/zed/pull/58308)), switch with `/model`, `/weak-model`, `/editor-model`. The intended path is ACP `configOptions` in `session/new` — a separate slice.

### Prompt blocks

- Text: yes
- `ResourceContentBlock`: `name` only, not URI/body
- `EmbeddedResourceContentBlock`: derived filename only, not the embedded text
- Image / audio: silently dropped

### Permissions

Only Aider `confirm_ask` goes through `session/request_permission`. File writes go through `fs/write_text_file` into the review multibuffer, **not** through permission. Shell (`/run`) is executed by Aider itself; output is chat text only. Options are `allow_once` / `reject_once` only — no Always / Always-Reject.

### Session lifecycle

- ~~`close_session` shuts the executor with `wait=False` without cancelling the in-flight prompt.~~ **Partial:** `close_session` now signals cancel before executor shutdown; LLM HTTP may still run.
- `list_sessions` ignores `cwd`, `cursor`, `additional_directories`. No pagination, no `updatedAt`.
- Sessions live in process memory. After restart the ID is gone — `session/load` is missing anyway.
- Global `os.chdir` in the session: risky with several parallel Zed threads.

### Coder / workspace

- ~~Model fixed to `gpt-4o`, independent of Zed.~~ **Done:** model from `AIDER_ACP_MODELS`; default = first list entry; no implicit default model. Picker in Zed is dead; switch with `/model`.
- `map_tokens=0` — no repo map.
- ~~Every workspace file is added to chat context (expensive/slow on large repos).~~ **Done:** chat starts empty; files only via `@` (implicit `/add`) or `/add` / `/read-only`.
- `auto_commits=False` — Aider does not commit; ACP has no alternative either.

### Logging (planned)

Always-on `INFO` to `aider_acp.log` (next to the adapter) and stderr. Full prompts and assistant text are written. There is no user-facing switch, so bug reports cannot opt into a verbose log, and everyday use keeps a growing file with chat content.

**Planned:** `AIDER_ACP_LOG_LEVEL` in Zed `agent_servers.env` (`off` / `warning` / `debug`), default `warning`. Repro: set `debug`, restart the agent, attach `aider_acp.log`. Complementary to Zed’s `dev: open acp logs` (wire vs. adapter internals).

Also noisy today:

- Lots of `[paths]` lines at INFO; duplicate logging in `server.py` (module logger + instance logger).
- `ACPIO._send_update` logs the same update twice (before send + done callback).
- `agent/aider_agent.py` has debug `print`s that can pollute stdout (the ACP stream). Unused (`main.py` uses `acp_server.server.AiderAgent`).

### Tests

`test_acp_server.py` is a manual subprocess run, not unittest. Permissions are always approved.

**Done in `test_overlay.py` / `test_commands.py` / `test_models.py`:** overlay flush + diff `tool_call`, `fs/read_text_file`, cancel before flush, `confirm_ask` on cancel, blocking prompt, `usage_update`, slash commands, model catalog.

Open: load, modes, `PromptResponse.usage`.

---

## 3. Unused

Things that exist (protocol, SDK, or our code) but are not wired up.

### Client APIs Zed offers

| API | If unused |
|---|---|
| ~~`fs/read_text_file`~~ | **Done:** when Zed advertises `readTextFile`; otherwise disk. No read `tool_call` in the UI. |
| ~~`fs/write_text_file`~~ | **Done:** overlay flush → review multibuffer + diff `tool_call`. Disk fallback if the capability is missing. |
| `terminal/create` … `kill` | Shell is chat text only, no Zed terminal. |
| `elicitation/create` | No structured follow-ups. |

### SDK helpers not called

`acp.helpers`: `start_edit_tool_call`, `start_read_tool_call`, `tool_terminal_ref`, `update_plan`, `update_current_mode`, image/audio/resource blocks.

In use: `start_tool_call`, `update_tool_call`, `tool_diff_content`, `update_available_commands`.

### Received but unused parameters

- `mcp_servers` on `AiderSession` — stored only
- ~~`client_capabilities` / `client_info` in `initialize`~~ **Partial:** `client_capabilities.fs` is used; `client_info` still ignored
- `list_sessions`: `cwd`, `cursor`, `additional_directories`
- `prompt`: image, audio, embedded body; ~~`message_id` not returned as `userMessageId`~~ **Done** (echo only when sent)

### Dead / disconnected code of our own

| Where | Problem |
|---|---|
| `agent/aider_agent.py` | Old direct wrapper. `main.py` uses `acp_server.server.AiderAgent`. Also broken: `create_coder()` with no `io`. |
| `AgentCapabilities` etc. in `server.py` | Imported, never set on `InitializeResponse`. Unused request types (`InitializeRequest`, `NewSessionRequest`, …) too. |
| Module logger `logger` in `server.py` | Unused after the switch to `self.logger`; extra console handler remains. |
| `ext_method` / `ext_notification` | Empty stubs. |

### Aider features without an ACP surface

Aider can do more internally; slash commands expose most of it. Review mode only blocks git and host-only commands (`/commit`, `/diff`, `/undo`, `/git`, `/lint`, clipboard, `$EDITOR`, `/quit`, `/voice`):

- Slash commands — `/model`, `/add`, `/ls`, `/run`, `/web`, …; `SwitchCoder` is caught and the coder rebuilt
- Edit formats / modes — `/ask`, `/code`, `/architect`, `/chat-mode`, `/context`, `/help`
- Model switch — `/model`, `/weak-model`, `/editor-model` (until the Zed picker works)
- `/undo`, `/commit`, `/diff` — git, disabled in review mode
- Repo map — internally `map_tokens=0`; `/map` not in the menu
- Auth against LLM providers
