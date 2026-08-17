# Zed ACP: Lücken, Fehler, Ungenutztes

Stand: 2026-08-16. Abgleich gegen Zed als ACP-Client und Python-SDK `agent-client-protocol` (Schema v0.12.2, Wire-Version 1). Der Server startet mit `use_unstable_protocol=True`.

Zed ruft optionale Methoden nur auf, wenn der Agent sie in `initialize` advertised. Fehlt die Capability, bleibt die UI-Funktion tot — auch wenn der Handler im Code existiert.

---

## 1. Fehlt

Agent-Methoden ohne Handler. Zed kann sie nicht nutzen.

### Für Zed zentral

| Methode | Was Zed damit macht |
|---|---|
| ~~`session/cancel`~~ | ~~Stop-Button. Ohne Handler läuft Aider weiter.~~ **Erledigt:** `cancel` setzt Session-Event, bricht `confirm_ask` ab, überspringt Flush. |
| `session/load` | Thread-History und „Import Threads“. Nur bei `loadSession: true`. |
| `authenticate` | Login im Agent Panel. Keys gehen aktuell nur über Env. |
| `session/set_mode` | Mode-Picker (Ask / Code / Architect o.ä.). |
| `session/set_model` | Modellwahl. Hart auf `gpt-4o`. |
| `session/set_config_option` | Session-Optionen in der Zed-UI. |

### Optional / Unstable

| Methode | Anmerkung |
|---|---|
| `session/resume` | Session ohne History-Replay wiederaufnehmen. |
| `session/fork` | Session abzweigen. |
| `logout` | Auth-Zustand beenden. |
| MCP-Verbindung | `mcpServers` von Zed werden gespeichert, nie gestartet. |
| `nes/*` | Next Edit Suggestions. Für Aider meist irrelevant. |
| `document/didOpen` u.a. | Document-Sync, vor allem für NES. |
| `providers/*` | Provider-Konfiguration. Für Aider meist irrelevant. |
| Elicitation | Formulare / URL-Auth über den Client. |

### Session-Updates, die nie gesendet werden

Zed rendert diese Typen im Agent Panel, der Adapter erzeugt sie nicht:

- `tool_call` mit `content` vom Typ `diff` — reviewbare File-Edits
- `available_commands_update` — Slash-Commands (`/add`, `/commit`, …)
- `current_mode_update`
- `config_option_update`
- `plan` / `agent_plan`
- `usage_update` — Tokenverbrauch
- `session_info_update`
- `user_message_chunk`

### Prompt-Inhalt, den Zed schicken kann

Ohne passende `promptCapabilities` schickt Zed sie oft gar nicht. Wenn doch, werden sie ignoriert:

- Images
- Audio
- Embedded Resource **Inhalt** (aktuell nur der Dateiname)

---

## 2. Falsch oder unvollständig

Vorhanden, aber semantisch falsch, lückenhaft oder für Zed unsichtbar.

### Prompt-Lifecycle (v1)

~~`session/prompt` muss den Turn **blockieren** und erst danach `stopReason` liefern.~~

**Erledigt:** `session/prompt` blockiert bis `run_prompt` fertig ist; `stopReason` ist `end_turn` oder `cancelled`. `userMessageId` wird nur zurückgegeben, wenn Zed `messageId` mitschickt.

Offen: Token-`usage` in `PromptResponse` fehlt weiterhin.

### Capabilities nicht advertised

`initialize` liefert nur `protocolVersion` und `agentInfo`. Kein `agentCapabilities`, keine `authMethods`.

Defaults damit:

- `loadSession: false`
- kein Image / Audio / Embedded Context
- kein MCP (`http`/`sse`)
- leere `sessionCapabilities`

`session/list` und `session/close` sind implementiert, Zed darf sie laut Spec nicht aufrufen, solange `sessionCapabilities.list` / `.close` fehlen.

`additionalDirectories` wird ausgewertet, die Capability dafür fehlt.

`clientCapabilities` (fs, terminal, …) werden empfangen und nicht gespeichert oder genutzt.

### `session/new` ohne UI-State

Antwort enthält nur `sessionId`. Es fehlen:

- `modes` — kein Mode-Picker
- `models` — keine Modellwahl
- `configOptions`

Slash-Commands werden auch später nicht per `available_commands_update` nachgereicht.

### Prompt-Blöcke

- Text: ja
- `ResourceContentBlock`: nur `name`, nicht URI/Inhalt
- `EmbeddedResourceContentBlock`: nur abgeleiteter Dateiname, nicht der eingebettete Text
- Image / Audio: stillschweigend verworfen

### Permissions

Nur Aider-`confirm_ask` geht über `session/request_permission`. File-Writes und Shell laufen an Zed vorbei (Aider schreibt/führt selbst aus). Optionen nur `allow_once` / `reject_once`, kein Always/Always-Reject.

### Session-Lebenszyklus

- ~~`close_session` fährt den Executor mit `wait=False` herunter, ohne den laufenden Prompt zu canceln.~~ **Teilweise:** `close_session` signalisiert jetzt Cancel vor Executor-Shutdown; LLM-HTTP kann trotzdem noch laufen.
- `list_sessions` ignoriert `cwd`, `cursor`, `additional_directories`. Keine Pagination, kein `updatedAt`.
- Sessions leben nur im Prozessspeicher. Nach Restart ist die ID weg — `session/load` fehlt sowieso.
- Globales `os.chdir` in der Session: bei mehreren parallelen Zed-Threads riskant.

### Coder / Workspace

- Modell fest `gpt-4o`, unabhängig von Zed.
- `map_tokens=0` — kein Repo-Map.
- Alle Workspace-Dateien landen im Chat-Context (kann bei großen Repos teuer/langsam sein).
- `auto_commits=False` — Aider committet nicht; ACP-seitig gibt es dafür auch keine Alternative.

### Logging / Robustheit

- Sehr viele `[paths]`-Debug-Logs, doppeltes Logging in `server.py` (Modul-Logger + Instanz-Logger).
- `main.py` importiert `logging` doppelt.
- `ACPIO._send_update` loggt denselben Update zweimal.
- `workspace_files.py` und `agent/aider_agent.py` enthalten Debug-`print`s, die stdout (ACP-Stream) verschmutzen können.

### Tests

`test_acp_server.py` ist ein manueller Subprocess-Lauf, kein pytest. Permissions werden immer approved. Kein Test für Cancel, Load, Modes, Diffs, blockierendes Prompt.

---

## 3. Nicht genutzt

Dinge, die existieren (Protokoll, SDK oder eigener Code), aber nicht angebunden sind.

### Client-APIs, die Zed anbietet

| API | Folge, wenn ungenutzt |
|---|---|
| `fs/read_text_file` | Aider liest selbst, Zed sieht keine Read-Tool-Calls. |
| `fs/write_text_file` | Writes außerhalb des Editors, keine Zed-Diffs. |
| `terminal/create` … `kill` | Shell nur als Chat-Text, kein Zed-Terminal. |
| `elicitation/create` | Keine strukturierten Rückfragen. |

### SDK-Helfer ohne Aufruf

`acp.helpers`: `start_edit_tool_call`, `start_read_tool_call`, `tool_diff_content`, `tool_terminal_ref`, `update_plan`, `update_available_commands`, `update_current_mode`, Image/Audio/Resource-Blocks.

### Empfangene, aber ungenutzte Parameter

- `mcp_servers` an `AiderSession` — nur abgelegt
- `client_capabilities` / `client_info` in `initialize`
- `list_sessions`: `cwd`, `cursor`, `additional_directories`
- `prompt`: Image-, Audio-, Embedded-Inhalt; ~~`message_id` wird nicht als `userMessageId` zurückgegeben~~ **Erledigt** (nur Echo wenn gesendet)

### Toter / unverbundener eigener Code

| Stelle | Problem |
|---|---|
| `agent/aider_agent.py` | Alter Direkt-Wrapper. `main.py` nutzt `acp_server.server.AiderAgent`. Zusätzlich kaputt: `create_coder()` ohne `io`. |
| `AgentCapabilities` u.a. in `server.py` | Importiert, nie in `InitializeResponse` gesetzt. Ebenso ungenutzte Request-Typen (`InitializeRequest`, `NewSessionRequest`, …). |
| Modul-Logger `logger` in `server.py` | Nach Umstellung auf `self.logger` ungenutzt; Extra-Console-Handler bleibt. |
| `ext_method` / `ext_notification` | Leere Stubs. |

### Aider-Funktionen ohne ACP-Oberfläche

Aider kann intern mehr, Zed sieht davon nichts:

- Slash-Commands
- Edit-Formate / Modes
- Modellwechsel
- `/undo`, `/commit`, `/diff`
- Repo-Map
- Auth gegenüber LLM-Providern
