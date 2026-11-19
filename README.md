# encode-the-correction

Every time you correct a coding agent, turn the correction into a deterministic
check instead of another prose rule. One portable command, `check`, enforces
them all, locally and in any CI, the way a linter does. Python 3.8+ standard
library only: no runtime dependencies, no Node, no agent runtime needed.

## Install

```sh
pipx install git+https://github.com/somethingwentwell/encode-the-correction.git
# or, into the active environment:
pip install git+https://github.com/somethingwentwell/encode-the-correction.git
```

Either one puts a single command on your PATH, `encode-the-correction`. Run it
from a project root, or any directory below it, and it finds that project's
`.encode-the-correction/corrections.json`.

Installing is optional, because the tool is one file. `python3
encode_the_correction.py <command>` runs it from a clone of this repository, and
`adopt` vendors a copy into each project, so `python3
.encode-the-correction/cli.py <command>` works on any machine that has Python.
All three are the same program and take the same commands. The examples below
use the installed name.

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
encode-the-correction init my-project
cd my-project
encode-the-correction check
```

Without the install, the same two steps are `python3
/path/to/encode-the-correction/encode_the_correction.py init my-project` and
`python3 .encode-the-correction/cli.py check`.

`init` creates `.encode-the-correction/` (a copy of the CLI as `cli.py`, the
registry `corrections.json`, a manifest) and an `AGENTS.md` with one tagged
section telling agents to run `check` and to encode corrections as checks. It
refuses to touch a project that already has `AGENTS.md`, `CLAUDE.md` or an
install; use `adopt` there.

Encode a correction, then watch it fail until the root cause is fixed:

```sh
encode-the-correction encode --id no-timer-quick-fixes \
  --forbid '\bsetTimeout\(' --glob 'src/**/*.ts' \
  --message 'Imperative timers patch over a state-flow problem.' \
  --fix 'Drive the update from state and delete the timer.'
encode-the-correction check    # exit 1 with file:line, problem, root-cause fix
```

| Flag | The check fails when |
|---|---|
| `--forbid REGEX` | a line in the `--glob` files matches |
| `--require REGEX` | a `--glob` file has no matching line |
| `--forbid-path GLOB` | a file matching the glob exists |

Exceptions live only in the registry (`--exclude GLOB`), where they are
reviewable; there is no inline ignore comment. Checks are data: nothing in the
registry, your files, or any other text is ever executed.

## 3. Adopt in an existing project

```sh
encode-the-correction adopt /path/to/project
```

Without the install: `python3 /path/to/encode-the-correction/encode_the_correction.py adopt /path/to/project`.

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
- **Uninstall.** `encode-the-correction uninstall` (or `python3
  .encode-the-correction/cli.py uninstall`), run from the project root, removes
  only the tagged blocks, this tool's hook entries, and the files it added. A
  registry that holds encoded corrections is kept unless you pass `--purge`.

## 4. Hand it to any coding agent

Paste this to an agent that has filesystem and shell access, replacing
`<REPO>` with `https://github.com/somethingwentwell/encode-the-correction.git`
or the path of a local clone:

```text
Install the "encode-the-correction" tool from <REPO> into the current project and use it from now on.

1. Choose how you will run the tool. Use the first option that works here:
   a. `encode-the-correction --version` already works: use `encode-the-correction`.
   b. You may install command-line tools on this machine: run `pipx install git+<REPO>`
      (for a local path: `pipx install <REPO>`; without pipx, `python3 -m pip install`
      the same thing inside a virtual environment), then use `encode-the-correction`.
   c. Otherwise use the tool without installing it: if <REPO> is a git URL, clone it into
      a temporary directory outside this project; if it is a local path, use it as is.
      Run it as `python3 <clone>/encode_the_correction.py`. Do not copy the repo into the project.
2. From the project root run `encode-the-correction adopt .` (option c:
   `python3 <clone>/encode_the_correction.py adopt .`). If you are running in Claude Code,
   Cursor, OpenAI Codex or GitHub Copilot, add --hooks claude-code, --hooks cursor,
   --hooks codex or --hooks copilot for your own runtime only. adopt merges into existing
   files and preserves their content.
3. Run `encode-the-correction check` and fix any failure at its root cause. If the
   `encode-the-correction` command is not installed, run the copy that adopt put in the
   project instead: `python3 .encode-the-correction/cli.py check`. Both take the same arguments.
4. From now on follow the "Encode the correction" section in AGENTS.md: run `check` at the
   start of each session and before finishing a task; whenever I correct you, encode the
   correction with `encode-the-correction encode ...` (or
   `python3 .encode-the-correction/cli.py encode ...`) and show me `check` failing on the
   mistake and passing after the fix, instead of adding a rule or comment.
5. Tell me which files were created or changed. Do not commit or push unless I ask.
```

Any agent can install the tool and run the common `check` command. It runs
**automatically at every session start only** on a runtime whose hook you
installed with `--hooks` (Claude Code, Cursor, Codex, Copilot). Everywhere else
it runs when the agent obeys `AGENTS.md`, which is advisory, or when you wire
`check` into pre-commit or CI as shown next.

## 5. Enforce it: local lint, pre-commit, any CI

Session hooks inform the agent and always exit 0; **they never fail or block a
session**. The gates are `check` itself, the pre-commit hook and a CI job: all
three **fail on a violation**.

**Locally**, from anywhere inside the project:

```sh
encode-the-correction check
```

| Exit code | Meaning |
|---|---|
| 0 | every encoded correction passes |
| 1 | at least one correction is violated |
| 2 | configuration or usage error (no registry, invalid JSON, bad regex, unknown option) |

Findings go to stdout as `path:line: matching text`, relative to the project
root, in a stable order, followed by the problem and the root-cause fix:

```text
FAIL no-timer-quick-fixes (forbid-pattern, 1 finding)
  src/ui/refresh.ts:2: setTimeout(() => render(), 0);
  Problem: Imperative timers patch over a state-flow problem.
  Root-cause fix: Drive the update from state and delete the timer.
```

`--format json` prints the same result for tools; `--only ID` runs one
correction.

**pre-commit.** Add this repository to `.pre-commit-config.yaml` like any other
linter. pre-commit installs the CLI into its own environment:

```yaml
repos:
  - repo: https://github.com/somethingwentwell/encode-the-correction
    rev: <full-commit-sha>  # or a release tag, once one is published
    hooks:
      - id: encode-the-correction
```

**Any CI.** Install the CLI, run the check; the non-zero exit fails the job:

```sh
python3 -m pip install git+https://github.com/somethingwentwell/encode-the-correction.git
encode-the-correction check
```

Ready-made versions: a GitHub Actions workflow
([examples/github-actions.yml](examples/github-actions.yml)), a GitLab CI job
([examples/gitlab-ci.yml](examples/gitlab-ci.yml)) and a POSIX script for
everything else ([examples/ci.sh](examples/ci.sh)). A project that committed
the vendored copy can skip the install and run `python3
.encode-the-correction/cli.py check` in CI instead. [docs/ci.md](docs/ci.md)
covers pinning a version, images without git, a pre-commit hook that needs no
install, and the output format.

## Tests

```sh
python3 -m unittest discover -s tests
```

Two test classes build the package into a throwaway virtual environment with
`pip`, which downloads the build backend (setuptools). On a machine without
network access, set `ETC_SKIP_INSTALL_TESTS=1` to skip them.
