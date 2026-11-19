# encode-the-correction

Every time you correct a coding agent, turn the correction into a deterministic
check instead of another prose rule. One portable command, `check`, enforces
them all. Python 3.8+ standard library only; no Node, package registry, or agent
runtime needed.

## 1. Where the idea comes from

Lauren Tan's talk [All Code Is Slop (Except Mine)](https://www.youtube.com/watch?v=O_zcc6Xl12g)
argues that you earn trust in agents by investing in their environment:
deterministic layers first (APIs, architecture, lint rules, types, tests), prose
last. Comments, rules and `AGENTS.md` lines go stale, cannot be enforced, and
become escape hatches. So whenever you correct an agent, encode the correction
as a check whose message points at the root cause, or remove the class of
mistake in the API or architecture. This tool does that one thing. It is not a
software factory, a formal-verification stack, or a replacement for your linter.

## 2. Start in a new project

```sh
python3 /path/to/encode-the-correction/encode_the_correction.py init my-project
cd my-project
python3 .encode-the-correction/cli.py check
```

`init` creates `.encode-the-correction/` (a copy of the CLI as `cli.py`, the
registry `corrections.json`, a manifest) and an `AGENTS.md` with one tagged
section telling agents to run `check` and to encode corrections as checks. It
refuses to touch a project that already has `AGENTS.md`, `CLAUDE.md` or an
install; use `adopt` there.

Encode a correction, then watch it fail until the root cause is fixed:

```sh
python3 .encode-the-correction/cli.py encode --id no-timer-quick-fixes \
  --forbid '\bsetTimeout\(' --glob 'src/**/*.ts' \
  --message 'Imperative timers patch over a state-flow problem.' \
  --fix 'Drive the update from state and delete the timer.'
python3 .encode-the-correction/cli.py check    # exit 1 with file:line, problem, root-cause fix
```

| Flag | The check fails when |
|---|---|
| `--forbid REGEX` | a line in the `--glob` files matches |
| `--require REGEX` | a `--glob` file has no matching line |
| `--forbid-path GLOB` | a file matching the glob exists |

Exceptions live only in the registry (`--exclude GLOB`), where they are
reviewable; there is no inline ignore comment. Checks are data: nothing in the
registry, your files, or any other text is ever executed. `check` exits 0 on
pass, 1 on a violation, 2 on a configuration error, so it also works in CI or a
pre-commit hook.

## 3. Adopt in an existing project

```sh
python3 /path/to/encode-the-correction/encode_the_correction.py adopt /path/to/project
```

- **Preserves existing files.** `AGENTS.md` gets one tagged block appended; your
  content is untouched. An existing `CLAUDE.md` gets a tagged `@AGENTS.md`
  import (skipped if it already imports or links to `AGENTS.md`), so Claude
  Code reads the same instructions as every other agent. No `CLAUDE.md` is
  created unless you pass `--claude-md always`; `--claude-md never` skips it.
- **Idempotent.** Running `adopt` again refreshes the tagged block and the CLI
  copy in place and changes nothing else. Your registry is never overwritten.
  `--dry-run` shows the plan; an invalid file aborts before anything is written.
- **Optional session hooks**, only for runtimes with a documented hook schema
  ([details](docs/adapters.md)). They are merged into existing hook files
  without touching other hooks, and all call the same `check`:

  | `--hooks` | File written | Event |
  |---|---|---|
  | `claude-code` | `.claude/settings.json` | `SessionStart` |
  | `cursor` | `.cursor/hooks.json` | `sessionStart` |
  | `codex` | `.codex/hooks.json` (trust it once via `/hooks`) | `SessionStart` |
  | `copilot` | `.github/hooks/encode-the-correction.json` | `sessionStart` |

  Example: `adopt . --hooks claude-code,cursor` (or `--hooks all`).
- **Uninstall.** `python3 .encode-the-correction/cli.py uninstall` removes only
  the tagged blocks, this tool's hook entries, and the files it added. A
  registry that holds encoded corrections is kept unless you pass `--purge`.

## 4. Hand it to any coding agent

Paste this to an agent that has filesystem and shell access, replacing
`<REPO>` with a local path or git URL of this repository:

```text
Install the "encode-the-correction" tool from <REPO> into the current project and use it from now on.

1. Get the tool outside this project: if <REPO> is a git URL, clone it into a temporary
   directory; if it is a local path, use it as is. Do not copy the repo into the project.
2. From the project root run:
     python3 <tool>/encode_the_correction.py adopt .
   If you are running in Claude Code, Cursor, OpenAI Codex or GitHub Copilot, add
   --hooks claude-code, --hooks cursor, --hooks codex or --hooks copilot for your own
   runtime only. adopt merges into existing files and preserves their content.
3. Run `python3 .encode-the-correction/cli.py check` and fix any failure at its root cause.
4. From now on follow the "Encode the correction" section in AGENTS.md: run `check` at the
   start of each session and before finishing a task; whenever I correct you, encode the
   correction with `python3 .encode-the-correction/cli.py encode ...` and show me `check`
   failing on the mistake and passing after the fix, instead of adding a rule or comment.
5. Tell me which files were created or changed. Do not commit or push unless I ask.
```

Any agent can install the tool and run the common `check` command. It runs
**automatically at every session start only** on a runtime whose hook you
installed with `--hooks` (Claude Code, Cursor, Codex, Copilot). Everywhere else
it runs when the agent obeys `AGENTS.md`, which is advisory, or when you wire
`check` into CI. Session hooks report results to the agent; they never block a
session.

## Tests

```sh
python3 -m unittest discover -s tests
```
