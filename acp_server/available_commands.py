from acp.schema import AvailableCommand, AvailableCommandInput, UnstructuredCommandInput

# Git-only or host-only (clipboard, $EDITOR, process exit, mic). Everything
# else Aider offers is advertised and executed.
DENIED_COMMANDS = frozenset(
    {
        "commit",
        "diff",
        "undo",
        "git",
        "lint",
        "report",
        "voice",
        "copy",
        "paste",
        "copy_context",
        "edit",
        "editor",
        "multiline_mode",
        "exit",
        "quit",
    }
)


def _cmd(name: str, description: str, hint: str | None = None) -> AvailableCommand:
    inp = None
    if hint is not None:
        inp = AvailableCommandInput(root=UnstructuredCommandInput(hint=hint))
    return AvailableCommand(name=name, description=description, input=inp)


def curated_available_commands() -> list[AvailableCommand]:
    return [
        _cmd("model", "Switch the main model", "model name"),
        _cmd("weak-model", "Switch the weak model", "model name"),
        _cmd("editor-model", "Switch the editor model", "model name"),
        _cmd("models", "Search Aider/LiteLLM model names", "search"),
        _cmd("ask", "Ask without editing files", "message"),
        _cmd("code", "Switch to code/edit mode", "message"),
        _cmd("architect", "Architect/editor two-model mode", "message"),
        _cmd("chat-mode", "Switch chat mode", "ask|code|architect|help|context"),
        _cmd("context", "Identify files that would need editing", "message"),
        _cmd("help", "Ask questions about Aider", "question"),
        _cmd("add", "Add files to the chat", "path"),
        _cmd("drop", "Remove files from the chat", "path"),
        _cmd("read-only", "Add files as read-only", "path"),
        _cmd("ls", "List files in the chat"),
        _cmd("clear", "Clear chat history"),
        _cmd("reset", "Drop files and clear history"),
        _cmd("tokens", "Show token usage of the current context"),
        _cmd("settings", "Show current Aider settings"),
        _cmd("reasoning-effort", "Set reasoning effort", "low|medium|high"),
        _cmd("think-tokens", "Set thinking token budget", "8k"),
        _cmd("run", "Run a shell command", "command"),
        _cmd("test", "Run a command and keep output on failure", "command"),
        _cmd("web", "Scrape a URL into the chat", "url"),
    ]
