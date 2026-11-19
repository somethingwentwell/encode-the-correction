# Authoring report

Date: 2026-11-19. Repo: `/workspace/encode-the-correction` (local only).
Authoring model, as reported by the session environment: `claude-fable-5-2`. The
authoritative proof is the `canonicalModel` field in the invoking CLI's JSON
output, which is not visible from inside the session.

## What was built

A portable "encode the correction" toolkit after Lauren Tan's talk
[All Code Is Slop (Except Mine)](https://www.youtube.com/watch?v=O_zcc6Xl12g):
each correction given to an agent becomes a deterministic, project-local check
with a root-cause-oriented message, instead of another prose rule.

| Path | Purpose |
|---|---|
| `encode_the_correction.py` | The whole CLI: one file, Python standard library only |
| `README.md` | Idea, new project, existing project, pasteable agent instruction |
| `docs/adapters.md` | Verified hook schemas, sources, and limitations |
| `tests/` | 84 `unittest` tests plus one untrusted-text fixture |
| `.encode-the-correction/corrections.json` | Five corrections this repo enforces on itself |

Installed into a target project:

- `.encode-the-correction/cli.py` (copy of the CLI), `corrections.json`
  (registry), `manifest.json` (what the tool added, for exact uninstall), and
  `hooks/session-start.sh` when a hook adapter is requested.
- One tagged block in `AGENTS.md`. Existing content is preserved, and the block
  is refreshed in place on re-adopt.
- A tagged `@AGENTS.md` import in an existing `CLAUDE.md` or
  `.claude/CLAUDE.md`, only when it does not already import or link to
  `AGENTS.md`. No `CLAUDE.md` is created by default, because current Claude Code
  reads `AGENTS.md` directly when no `CLAUDE.md` exists.
- Optional session-start hooks for Claude Code, Cursor, OpenAI Codex and GitHub
  Copilot, merged into the documented project-level files.

Design decisions worth knowing:

- **The registry is JSON, not YAML.** The standard library has no YAML parser.
- **Checks are data only**: `forbid-pattern`, `require-pattern`, `forbid-path`
  (regexes and globs). There is no "run this command" check type, so nothing in
  the registry, project files, hook stdin or any transcript can be executed. The
  only subprocess is a fixed `git ls-files` argv, used to respect `.gitignore`.
- **No inline ignore comments.** Exceptions exist only as `exclude` globs in the
  registry, following the talk's point that comments become escape hatches.
- **Install plans first, then writes.** Any invalid target (bad JSON, unbalanced
  markers, a symlink leaving the project) aborts before a single file changes.
- **`uninstall` keeps a registry that holds encoded corrections** unless
  `--purge` is passed. This deviates from "uninstall removes the registry" in
  favour of "no destructive writes without an explicit option": the corrections
  are the only user-authored data the tool holds. An empty registry is removed.
- **Session hooks always exit 0.** They put the check result into the agent's
  context and never block a session. `check` itself exits 1 on a violation, so
  it can gate CI.

## How to invoke the CLI

```sh
# install (from this repo)
python3 encode_the_correction.py init  <new-project-dir>  [--hooks RUNTIMES] [--claude-md auto|always|never]
python3 encode_the_correction.py adopt <existing-project> [--hooks RUNTIMES] [--claude-md ...] [--force] [--dry-run]

# inside an installed project
python3 .encode-the-correction/cli.py check   [--only ID] [--format text|json] [--max-findings N]
python3 .encode-the-correction/cli.py encode  --id ID (--forbid REGEX | --require REGEX | --forbid-path GLOB) \
        [--glob GLOB]... [--exclude GLOB]... [--ignore-case] --message TEXT --fix TEXT [--why TEXT] [--replace]
python3 .encode-the-correction/cli.py list
python3 .encode-the-correction/cli.py uninstall [--purge] [--dry-run]
```

`RUNTIMES` is a comma-separated list of `claude-code`, `cursor`, `codex`,
`copilot`, or `all`. The default is no hooks. Every command takes an optional
project path. Exit codes: 0 ok, 1 a check failed, 2 usage or configuration
error.

Flags that guard destructive paths, all off by default: `--force` (overwrite an
edited tool file on adopt), `--replace` (overwrite a correction with the same
id), `--purge` (delete the registry and edited tool files on uninstall).

## Tests

Command, run from the repo root:

```sh
python3 -m unittest discover -s tests
```

Result on Python 3.13.5: **84 tests run, 83 passed, 0 failed, 1 skipped.** The
skipped test runs the Copilot `powershell` command under `pwsh`, which is not
installed on this machine. The suite also passed on throwaway CPython 3.8.20
(82 passed, 2 skipped) and 3.10.22 (83 passed, 1 skipped) interpreters, which
were removed afterwards.

Coverage against the brief:

- `init` on a new project and `adopt` on an existing one.
- Repeated `adopt` leaves a byte-identical tree and preserves existing
  `AGENTS.md`, `CLAUDE.md`, hook JSON and user files.
- `uninstall` returns a pre-existing project to its original bytes and removes
  everything a fresh `init` added.
- `check` fails on a seeded anti-pattern and passes after the fix; an encoded
  correction is enforced on the next `check`.
- Each adapter's written config is validated against field lists taken from the
  vendor docs, then its hook command is executed directly with a documented
  stdin payload. The context each hook produces equals the output of `check`.
- Destructive paths are shown not to run without their flag.
- A fixture of hostile "transcript" text is fed through every free-form input,
  through hook stdin and through project files, and a canary proves nothing was
  executed. A control test proves the canary would fire.

All tests work in temporary directories. The only real repo they read is this
one, to run its own five corrections. A mutation pass with 15 deliberate bugs
(duplicate block on re-adopt, dropped user hooks, unguarded overwrite, shell
execution of a message, and others) was caught by the suite in every case; one
gap it exposed in the git-mode scan test was closed.

## Adapter limitations

Schemas were re-verified against the official docs on 2026-11-19; all four were
confirmed, so none was skipped. Details and sources are in `docs/adapters.md`.

- **No live runtime was exercised.** The hooks were tested by executing the
  written commands directly, not inside real Claude Code, Cursor, Codex or
  Copilot sessions.
- **Claude Code**: writes only project `.claude/settings.json`. The hook uses
  the shell form and needs `sh` (Git Bash on Windows).
- **Cursor**: `sessionStart` is documented as unsupported for cloud agents and
  as fire-and-forget. Needs `sh`.
- **Codex**: project hooks are skipped until trusted via `/hooks`, and again
  after any change. The command resolves the project from the git top level, so
  the project must be the git root or the session must start in it. No
  `commandWindows` variant is written.
- **Copilot**: the `powershell` command is written as the schema requires but
  was not run under PowerShell or on Windows.
- **All**: existing hook files must be strict JSON (files with comments are
  left untouched and `adopt` stops). Merging re-serializes with the detected
  indentation. Hooks need Python 3.8+ as `python3` or `python`.
- **Other runtimes** get no hook and rely on the advisory `AGENTS.md` section.

Other limits: patterns match line by line (no multi-line regexes), files over
2 MB and binary files are skipped, and nothing was tested on Windows or macOS.

## Git and remote

- Local repository on branch `main`, with identity set in this repo only
  (`Something Went Well <somethingwentwell@users.noreply.github.com>`).
- **Nothing was pushed.** No remote was added and no GitHub repository was
  created.
- No licence file was added; that choice is left to the owner.
