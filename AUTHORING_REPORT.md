# Authoring report

Date: 2026-11-19. Repo: `/workspace/encode-the-correction`, published at
<https://github.com/somethingwentwell/encode-the-correction>.
Authoring model, as reported by the session environment: `claude-fable-5-2`. The
authoritative proof is the `canonicalModel` field in the invoking CLI's JSON
output, which is not visible from inside the session.

The report has two parts: the original toolkit (version 0.1.0), and the
packaging of it as an installable, lint-like CLI (version 0.2.0), described in
[Packaging as an installable CLI](#packaging-as-an-installable-cli-020). The
packaging commit is local only; it was not pushed.

## What was built

A portable "encode the correction" toolkit after Lauren Tan's talk
[All Code Is Slop (Except Mine)](https://www.youtube.com/watch?v=O_zcc6Xl12g):
each correction given to an agent becomes a deterministic, project-local check
with a root-cause-oriented message, instead of another prose rule.

| Path | Purpose |
|---|---|
| `encode_the_correction.py` | The whole CLI: one file, Python standard library only |
| `pyproject.toml` | Packaging: one module, console script `encode-the-correction`, no runtime dependencies |
| `.pre-commit-hooks.yaml` | Hook manifest, so the repo works as a pre-commit linter |
| `.github/workflows/tests.yml` | This repo's CI: unit tests on pushes to `main` and on pull requests |
| `examples/` | GitHub Actions workflow, GitLab CI job and a POSIX script that install the CLI and run `check` |
| `README.md` | Install, idea, new project, existing project, pasteable agent instruction, lint/pre-commit/CI |
| `docs/adapters.md` | Verified hook schemas, sources, and limitations |
| `docs/ci.md` | Exit codes, output format, project discovery, pre-commit and CI recipes |
| `tests/` | 141 `unittest` tests, one untrusted-text fixture, a strict YAML-subset parser for validation |
| `.encode-the-correction/corrections.json` | Six corrections this repo enforces on itself |

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

Every command below also works as `encode-the-correction <command> ...` once the
package is installed (`pipx install` or `pip install` of
`git+https://github.com/somethingwentwell/encode-the-correction.git`).

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

## Packaging as an installable CLI (0.2.0)

Goal: make the tool installable and usable locally and in any CI the way a
linter is, without breaking the zero-install paths.

What changed:

- **Package.** `pyproject.toml` with setuptools as the build backend only,
  `dependencies = []`, `requires-python = ">=3.8"`, and one console script,
  `encode-the-correction = encode_the_correction:main`. No short alias was
  added. The version has one source, `VERSION` in the module. `py-modules`
  lists the single module, so `tests/`, `docs/` and `examples/` are never
  packaged. `python -m encode_the_correction` works as well.
- **Zero-install paths unchanged.** `python3 encode_the_correction.py` and the
  vendored `.encode-the-correction/cli.py` are the same file as the installed
  module. `adopt` run from the installed CLI vendors a byte-identical copy, and
  re-adopting through any of the three entry points changes nothing.
- **Project discovery.** `check`, `encode` and `list` without a project
  argument use the current directory or the nearest parent that has
  `.encode-the-correction/corrections.json`, stopping at the root of the
  enclosing git repository. An explicit path is used exactly. `init`, `adopt`
  and `uninstall` do not search. The vendored copy still always uses the
  project it lives in.
- **Lint contract.** Exit 0 pass, 1 violation, 2 configuration or usage error;
  findings as `path:line: text` on stdout in a stable order; errors on stderr.
  Two cases that used to end in a traceback with exit status 1, which CI would
  read as a violation, are fixed: output the terminal cannot encode (including a
  file name that is not UTF-8) is now escaped, and an unreadable registry is now
  exit 2.
- **Hints name a command that exists.** Messages point at
  `python3 .encode-the-correction/cli.py` when the project has the vendored
  copy and at `encode-the-correction` when it has only a registry.
- **pre-commit.** `.pre-commit-hooks.yaml` with one hook,
  `id: encode-the-correction`, `language: python`, `pass_filenames: false`,
  `always_run: true`.
- **CI.** `examples/github-actions.yml`, `examples/gitlab-ci.yml` and
  `examples/ci.sh` (any runner with a POSIX shell) all install the CLI and run
  `encode-the-correction check`. `.github/workflows/tests.yml` runs this repo's
  unit tests on Python 3.8 to 3.15, then installs the package and lints the
  repo with the installed CLI.
- **Docs.** README gained an install section and a section on local lint,
  pre-commit and CI. Its four original sections are kept. The pasteable agent
  instruction now prefers the installed CLI and falls back to the vendored
  command.
- **A sixth self-enforced correction**, `package-has-no-runtime-dependencies`,
  fails when `pyproject.toml` stops declaring `dependencies = []`.

Not changed: the `AGENTS.md` block, the hook wrapper and all four session
adapters. They keep calling the vendored copy, which every adopted project has.
Session hooks still always exit 0 and never fail a session; `check`, the
pre-commit hook and CI jobs are what fail on a violation.

Verified by hand in throwaway directories, in addition to the test suite:

- `pip install git+file://<snapshot of this tree>` (also pinned to a commit),
  `pip install` of a tarball laid out like a GitHub archive, `pipx install`
  and `pipx run --spec` all produced a working `encode-the-correction 0.2.0`.
- pre-commit 4.7.0: `pre-commit validate-manifest` accepted the manifest. With
  a config pointing at a snapshot of this tree, `pre-commit run` passed on a
  clean project, failed with exit 1 on a violation, and blocked a real
  `git commit`. The documented `repo: local` variant behaved the same.
- actionlint 1.7.12 reported no problems in either GitHub workflow.
- The test-only YAML parser gave the same result as PyYAML on all four YAML
  files.
- `actions/checkout@v7` and `actions/setup-python@v7` exist, and the
  `ubuntu-24.04` runner image has every Python from 3.8 to 3.15.

## Tests

Command, run from the repo root:

```sh
python3 -m unittest discover -s tests
```

Result on Python 3.13.5: **141 tests run, 140 passed, 0 failed, 1 skipped.** The
skipped test runs the Copilot `powershell` command under `pwsh`, which is not
installed on this machine. Before the packaging work the suite had 84 tests
(83 passed, 1 skipped); all of them still pass, and 57 were added.

| Python | Run | Passed | Failed | Skipped |
|---|---|---|---|---|
| 3.8.20 | 141 | 132 | 0 | 9 |
| 3.9.25 | 141 | 132 | 0 | 9 |
| 3.10.22 | 141 | 133 | 0 | 8 |
| 3.11.18, 3.12.16, 3.13.5, 3.14.10, 3.15.2 | 141 | 140 | 0 | 1 |

The extra skips on older versions are the seven tests that read
`pyproject.toml` with `tomllib` (Python 3.11+) and one that needs
`sys.stdlib_module_names` (3.10+). The interpreters other than 3.13.5 were
throwaway copies under the temp directory and were removed afterwards.

Seven tests build the package for real: five `pip install` this tree into a
fresh virtual environment and drive the generated console script, two run
`examples/ci.sh`. pip downloads setuptools for the build, so they need network
access. With `ETC_SKIP_INSTALL_TESTS=1` they are skipped: 136 run, 132 passed,
4 skipped.

Coverage against the original brief:

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

Coverage added for the packaging work:

- **Packaging and entry point** (`tests/test_packaging.py`): the declared
  metadata; the module run the way the console-script launcher runs it; a real
  install into a virtual environment, checking the script, version, absence of
  dependencies and that only one module is shipped; `adopt`, re-`adopt` and
  `uninstall` through the installed script preserve a project exactly; the
  pre-commit hook's `entry` resolves and runs inside that environment.
- **Lint behaviour** (`tests/test_lint.py`): exit codes 0, 1 and 2 for text and
  JSON output; a full golden report; stable ordering; no absolute paths or
  control characters; stdout and stderr separation; discovery from the project
  root and from subdirectories, its repository boundary, and the commands that
  must not search.
- **pre-commit manifest and CI files** (`tests/test_ci_files.py`): the manifest
  against pre-commit's documented keys; both workflows and the GitLab job for
  structure and for the install-then-check order; unquoted versions, swallowed
  failures and bad indentation are rejected; every command line and YAML
  snippet in the README and docs is parsed with the CLI's own argument parser
  or the YAML parser.

All tests work in temporary directories. The only real repo they read is this
one: to run its own six corrections, to read its CI and packaging files, and to
copy it (without `.git`) into a temp directory for `pip` to build, so no build
files are written into the repo. A mutation pass with 15 deliberate bugs
(duplicate block on re-adopt, dropped user hooks, unguarded overwrite, shell
execution of a message, and others) was caught by the original suite in every
case; one gap it exposed in the git-mode scan test was closed. A second pass
for the packaging work, 22 deliberate bugs (a runtime dependency, a wrong entry
point, an unpackaged module, a hook that passes file names, a workflow that
swallows the failure, discovery that crosses a repository boundary, and
others), was also caught in every case.

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

## Packaging limitations

- **Nothing here is on GitHub yet.** Every `git+https://github.com/...` install,
  the pre-commit snippet and the example CI files need the packaging commit to
  be pushed first; until then the published tree has no `pyproject.toml`. They
  were verified only against a local snapshot of this tree.
- **No release tag and no PyPI release exist.** pre-commit needs a pinned
  `rev`, so the docs show a commit SHA placeholder. `pip install
  encode-the-correction` from PyPI does not work.
- **The CI files were not run on a CI service.** The GitHub workflows passed
  actionlint and the structural tests. The GitLab job was only parsed.
  `examples/ci.sh` was executed locally by the tests.
- **Pushing `.github/workflows/tests.yml`** needs credentials that are allowed
  to change workflows (for a personal access token, the `workflow` scope).
- **Installing from a git URL needs git on the machine.** `docs/ci.md` shows the
  archive URL to use on images without it.
- **The installed CLI and a project's vendored copy can be different versions.**
  Both read the same registry format; `adopt` refreshes the vendored copy to
  the version that ran it.
- **`check` scans the working tree**, including untracked files that are not
  ignored, also under pre-commit. It does not scan only the staged content.
- **YAML validation in the tests uses a strict subset parser**, not a full YAML
  implementation, because the tests take no dependencies. The one-off
  comparison with PyYAML and the pre-commit and actionlint runs are not
  repeated by the suite.
- The console script, pipx and pre-commit paths were exercised on Linux only.

## Git and remote

- Branch `main`, tracking `origin/main` at
  `https://github.com/somethingwentwell/encode-the-correction.git`. The remote
  was already configured; it was not changed.
- Commits use the identity already set in this repo only
  (`Something Went Well <somethingwentwell@users.noreply.github.com>`). Global
  git configuration was not touched.
- **The packaging commit was not pushed.** No tag was created, no remote was
  added or changed, and nothing was published to GitHub or PyPI.
- No licence file was added; that choice is left to the owner.
