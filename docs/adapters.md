# Session adapters

Adapters are optional. Each one registers a single session-start hook in the
runtime's documented project-level config file. Every hook runs the same wrapper,
`.encode-the-correction/hooks/session-start.sh <runtime>`, which runs
`cli.py session-hook`: the same checks as `cli.py check`, printed in the output
shape that runtime documents. The wrapper always exits 0, so a hook can inform
an agent but never block a session; use `check` in pre-commit or CI when you
need a gate ([ci.md](ci.md)).

The schemas below were re-verified against the official documentation on
2026-11-19. Re-verify before changing an adapter, and do not add a runtime whose
schema you cannot confirm.

| Runtime | File | What is written | How the result reaches the agent |
|---|---|---|---|
| Claude Code | `.claude/settings.json` | `hooks.SessionStart[]` matcher group (no `matcher`, so every source) with `{"type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}\"/.encode-the-correction/hooks/session-start.sh claude-code", "timeout": 60}` | plain-text stdout on exit 0 is added to context |
| Cursor | `.cursor/hooks.json` | `"version": 1`, `hooks.sessionStart[]` entry `{"command": "sh .encode-the-correction/hooks/session-start.sh cursor", "timeout": 60}` | stdout JSON `{"additional_context": ...}` |
| OpenAI Codex | `.codex/hooks.json` | `hooks.SessionStart[]` matcher group with `{"type": "command", "command": "sh \"$(git rev-parse --show-toplevel 2>/dev/null \|\| pwd)/.encode-the-correction/hooks/session-start.sh\" codex", "timeout": 60}` | plain-text stdout is added as developer context |
| GitHub Copilot CLI / cloud agent | `.github/hooks/encode-the-correction.json` | `"version": 1`, `hooks.sessionStart[]` entry with `type`, `bash`, `powershell`, `cwd: "."`, `timeoutSec: 60` | stdout JSON `{"additionalContext": ...}` |

Sources:

- Claude Code: <https://code.claude.com/docs/en/hooks> (hook locations, `SessionStart`, shell-form `command`, quoted `${CLAUDE_PROJECT_DIR}`), <https://code.claude.com/docs/en/memory> (`@AGENTS.md` import in `CLAUDE.md`)
- Cursor: <https://cursor.com/docs/hooks>
- OpenAI Codex: <https://learn.chatgpt.com/docs/hooks>
- GitHub Copilot: <https://docs.github.com/en/copilot/concepts/customizations/hooks>, <https://docs.github.com/en/copilot/reference/hooks-reference>

## Limitations

- **Claude Code.** Only the shareable project file is written, never
  `~/.claude/settings.json` or `.claude/settings.local.json`. The hook uses the
  shell form, so it needs `sh` (on Windows: Git Bash). Recent Claude Code reads
  `AGENTS.md` directly when the project has no `CLAUDE.md`; when a `CLAUDE.md`
  exists, `adopt` adds an `@AGENTS.md` import to it.
- **Cursor.** `sessionStart` is documented as unsupported for cloud agents and
  runs fire-and-forget. Needs `sh` on the PATH.
- **Codex.** Project hooks load only from trusted projects and are skipped until
  reviewed and trusted with `/hooks`; a changed hook must be trusted again. The
  command resolves the project from the git top level (falling back to the
  session directory), so the project must be the git root, or the session must
  start in the project directory. No `commandWindows` variant is written.
- **Copilot.** The tool owns its own file in `.github/hooks/` and leaves other
  hook files alone. The `powershell` command calls `python` directly and has not
  been exercised on Windows.
- **All.** The existing hook file must be strict JSON; a file with comments or a
  different schema version is left untouched and `adopt` stops before writing
  anything. Merging re-serializes the file with its detected indentation, so
  unusual whitespace is normalized. Hooks need Python 3.8+ as `python3` or
  `python`; without it the wrapper prints a note to stderr and exits 0.
- **Every other runtime** relies on the advisory `AGENTS.md` section.
