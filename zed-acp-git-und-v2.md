# Konzeptplan: Git-Variante und ACP v2

Kein Code in diesem Dokument. Die **Review-Variante** (Overlay → `fs/write_text_file` → Zeds Review Changes) ist umgesetzt. Hier steht, wie eine zweite Variante und der Wegfall der FS-APIs später aussehen sollen, ohne die Architektur neu zu diskutieren.

Stand: 2026-08-17. Bezogen auf ACP v1 (Zed) und die Ankündigung, dass ACP v2 die Client-FS-Methoden entfernt.

---

## 1. Warum zwei Varianten

Review (jetzt) vs. Git (dieses Dokument):

- **Schreiben:** Zed-Buffer via `fs/write_text_file` vs. Aider auf Disk
- **Accept/Reject:** Zed Review-Multibuffer pro Datei vs. kein Inline-Reject
- **Undo:** Zed-lokal (Buffer) vs. Aider-Commit plus `/undo`
- **Shell/Lint im selben Turn:** alte Disk vs. neue Dateien
- **Zed-UI:** viel (Review) vs. wenig (Chat, optional Anzeige-Diff)

Review ist richtig, wenn der User Cursor-artig Dateien abnicken will. Git ist richtig, wenn Aider wie die CLI arbeiten soll: sofort final, Tests laufen, Undo über Git.

Die beiden Pfade teilen Session, Coder, Mentions, Permissions. Unterschied nur: wo `write_text` landet und ob Aider committet.

---

## 2. Git-Variante — Zielverhalten

Aider bleibt Git-Agent.

1. `ACPIO.write_text` ruft wie früher `open()` / `InputOutput.write_text` (kein Overlay bis Turn-Ende).
2. `Coder.create(..., auto_commits=True, dirty_commits=True, use_git=True)` wenn ein Repo existiert.
3. Kein `fs/write_text_file`. Optional ein Edit-`tool_call` mit `type: "diff"` ohne Permission-Gate, nur Anzeige im Thread.
4. `/undo` = Aider `cmd_undo`: letzter Aider-Commit, Dateien per `git checkout HEAD~1`. Später als ACP Slash-Command (`available_commands_update`).
5. Shell, Lint, Tests im selben Turn sehen die neuen Dateien.

Ohne Git-Repo: Disk-Writes ohne Commit; `/undo` bleibt tot (Aider sagt das schon). Nicht still auf Review zurückfallen — der Mode ist explizit.

### 2.1 Wann einschalten

- User will keine Review-Klicks.
- Client advertised kein `fs.writeTextFile` (v2 oder Nicht-Zed).
- Shell/Tests sollen im selben Prompt gegen den neuen Stand laufen.

Default-Idee später: Capability-Detect. `writeTextFile` → Review, sonst Git. Umschalten über `session/set_mode` (`review` / `git`).

### 2.2 Trade-offs

- Edits sind sofort auf Disk. Zed „Reject“ in der Datei gibt es nicht.
- Review-Multibuffer bleibt leer.
- User-Änderungen in ungespeicherten Buffern können überschrieben werden, wenn nicht `fs/read_text_file` vor dem Write genutzt wird. Git-Modus kann Reads trotzdem über ACP machen (lesen ja, schreiben nein).
- Auto-Commit erzeugt Aider-Commits im User-Repo. Das ist Absicht und muss in der Mode-Beschreibung stehen.

### 2.3 Overlay in der Git-Variante

Mehrere SEARCH/REPLACE auf derselben Datei brauchen den Zwischenstand. Dafür muss nicht das Review-Overlay bis Turn-Ende stehen:

- Einfach: kein Overlay; jeder `write_text` geht auf Disk. Aider liest dazwischen von Disk. Das ist heutiges CLI-Verhalten.
- Nicht: Review-Overlay bis nach `coder.run()` halten — dann sehen Lint/Shell den alten Stand, das widerspricht dem Sinn von Git-Mode.

Empfehlung: Git-Mode ohne Turn-Ende-Overlay. `write_via_client=False`, sofort `super().write_text`.

### 2.4 Konkrete Umbauten (wenn es so weit ist)

Dateien:

- `aider_bridge/io_bridge.py` — Flag oder Strategie `write_via_client`; bei Git `write_text` → `super().write_text`. `flush_pending_writes` no-op oder ungenutzt.
- `aider_bridge/factory.py` — Parameter `auto_commits` / `dirty_commits` von der Session, nicht hart `False`.
- `acp_server/session.py` — Mode speichern; Coder mit passenden Flags erzeugen; nach `coder.run()` nur im Review-Mode flushen.
- `acp_server/server.py` — `new_session` liefert `modes`; `set_session_mode` implementieren; Mode-Wechsel: Overlay leeren, Coder neu oder Flags umlegen.

ACP:

- Modes hängen an der Session, nicht an `initialize`.
- `NewSessionResponse.modes`: `review` (Edits in Zed, Accept/Reject in der Datei) und `git` (schreibt und committet wie die CLI). `currentModeId` entsprechend.
- `review` nur listen, wenn `clientCapabilities.fs.writeTextFile`. Sonst nur `git`.

Slash-Commands (optional, eigener Slice):

- `available_commands_update` mit `undo` → `coder.commands.cmd_undo("")`.
- Weitere Aider-Commands (`/commit`, `/diff`) analog.

Anzeige-Diff im Git-Mode (optional):

- Nach jedem Disk-Write oder einmal pro Datei am Turn-Ende: `start_tool_call(kind="edit", content=[diff])` mit `status="completed"`. Kein `request_permission`, kein `fs/write_text_file`.

### 2.5 Tests für die Git-Variante

- `write_text` ändert die Datei auf Disk sofort; `pending_writes()` leer.
- Mit Fake-Repo: nach einem Edit existiert ein Aider-Commit; Undo stellt den Inhalt zurück (wenn Slash da ist).
- `initialize` ohne `writeTextFile` → Session startet in `git`, kein `fs/write_text_file` auf dem Wire.
- Mode-Wechsel `review` → `git` leert Overlay und schreibt nicht nachträglich via ACP.

Manuell: Zed Mode-Picker, Prompt der zwei Dateien ändert, Git-Log zeigt Aider-Commit, Review Changes bleibt leer.

---

## 3. ACP v2

### 3.1 Was Zed gesagt hat

Maintainer (Diskussion „Accepting/rejecting edits made by external agents“, Juni 2026):

- Inline-Review für External Agents hing an den ACP-Filesystem-APIs.
- Wenig Adoption; Diffs oft ungenau.
- ACP v2 entfernt diese APIs; anderer Ansatz (FS-Watch / Checkpoints) ohne Zeitplan.

PR, der `fs/write_text_file` bis Accept/Reject blockiert hätte, wurde abgelehnt: das sei nicht der Accept/Reject-Flow.

Heutiges Zed (v1) advertised weiter `writeTextFile`. Der Review-Pfad bleibt gültig, solange die Capability kommt.

### 3.2 Adapter-Regel

Nicht `protocolVersion == 2` hart verdrahten.

Wenn der Client `fs.writeTextFile` schickt: Review-Pfad (Overlay plus Flush via ACP). Sonst Git-Pfad (Disk plus Commits), sobald implementiert. Bis dahin gilt der aktuelle Fallback: Overlay auf Disk flushen und warnen.

v1-Client ohne FS-Flag und v2-Client ohne FS-APIs laufen gleich in den Else-Zweig.

### 3.3 Wenn FS-Write weg ist

Git-Variante ist der natürliche Fallback:

- Disk plus Auto-Commit.
- User reviewed in Zeds Git-Diff / Source Control, nicht im Agent-Review-Multibuffer.
- `/undo` statt Reject-Button.

Falls Zed später Agent-Edits über File-Watch oder Checkpoints trackt:

- Overlay behalten (Hunk-Kohärenz, kein vorzeitiger Disk-Write).
- Nur den Flush-Transport tauschen (`fs/write_text_file` → neuer Mechanismus).
- `oldText`/`newText` bleiben; der Edit-`tool_call` im Thread ebenfalls.

Nicht das Overlay an `write_text_file` koppeln. Heute: `flush_pending_writes()` wählt Client vs. Disk. Später eine dritte Senke.

### 3.4 Prompt-Lifecycle v2

v2 macht `session/prompt` nicht-blockierend; Fertig über `session/update`. Der aktuelle Server antwortet schon sofort mit `end_turn` (v1-widrig, v2-ähnlich). Beim v2-Umstieg Prompt-Semantik und `session/cancel` extra planen, unabhängig von Review vs. Git.

---

## 4. Reihenfolge, wenn beides gebaut wird

1. Review (erledigt): Overlay, ACP-Write, Capability-Fallback auf Disk.
2. Git-Mode hinter einem Flag/Mode, Default weiter Review wenn Capability da.
3. Slash `/undo` nur im Git-Mode.
4. v2: Else-Zweig wird Default; Review-Mode ausblenden wenn keine Write-Capability.

Kein zweiter Adapter-Prozess. Ein `AiderSession.mode`.
