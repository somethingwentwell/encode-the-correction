# Running `check` as a linter: local, pre-commit, CI

`encode-the-correction check` behaves like a linter. It reads
`.encode-the-correction/corrections.json`, scans the project, prints findings,
and exits with a status a script can act on. It needs Python 3.8+ and nothing
else; it never executes anything it reads.

## The contract

| Exit code | Meaning |
|---|---|
| 0 | every encoded correction passes (also when none are encoded yet) |
| 1 | at least one correction is violated |
| 2 | configuration or usage error: no registry, invalid JSON, invalid regex or glob, unknown option, unreadable registry |

Findings and the summary go to stdout. Errors (exit 2) go to stderr and stdout
stays empty.

```text
FAIL <id> (<type>, <n> findings)
  <path>:<line>: <the matching line, trimmed>
  <path>: <reason>
  Problem: <what is wrong>
  Root-cause fix: <how to fix the root cause>

encode-the-correction: 1 of 3 encoded corrections violated (2 findings, 41 files scanned).
```

- `<path>` is relative to the project root and uses `/` on every platform. The
  second form, without a line, is used by `--forbid-path` and `--require`
  checks, which are about a whole file.
- The order is stable: corrections in registry order, then paths sorted, then
  line numbers. Nothing in the output depends on the machine, the time, or the
  directory the command was started from.
- No colours or control characters are written. Characters the terminal cannot
  encode are written as backslash escapes instead of stopping the run.
- Text output shows 20 findings per correction (`--max-findings N`).
  `--format json` prints every finding as data.

## Which project is checked

- `encode-the-correction check <dir>` checks exactly `<dir>`.
- Without an argument, the installed CLI checks the current directory, or the
  nearest parent directory that has `.encode-the-correction/corrections.json`.
  The search stops at the root of the repository you are in (the first
  directory with a `.git` entry), so it never picks up a registry from outside
  it. `encode` and `list` find the project the same way.
- The vendored copy, `python3 .encode-the-correction/cli.py check`, always
  checks the project it lives in, from any directory.
- `init`, `adopt` and `uninstall` never search: without an argument they act on
  the current directory (or, for the vendored copy, on its own project).

In a git repository the scan covers tracked files and untracked files that are
not ignored. Elsewhere, or when `git` is not installed, it walks the directory
and skips `.git`, `node_modules`, `.venv`, `venv` and similar directories.

## pre-commit

This repository ships a hook manifest (`.pre-commit-hooks.yaml`), so it works
like any other [pre-commit](https://pre-commit.com) linter:

```yaml
repos:
  - repo: https://github.com/somethingwentwell/encode-the-correction
    rev: <full-commit-sha>  # or a release tag, once one is published
    hooks:
      - id: encode-the-correction
```

pre-commit builds an isolated environment for the hook and runs
`encode-the-correction check` from the repository root on every commit. The
check is about the whole project, so it takes no file names and also runs when
a commit touches only files that no correction mentions.

A project that committed the vendored copy can use it directly, with nothing to
download:

```yaml
repos:
  - repo: local
    hooks:
      - id: encode-the-correction
        name: encode-the-correction check
        entry: python3 .encode-the-correction/cli.py check
        language: system
        pass_filenames: false
        always_run: true
```

## CI

The pattern is the same everywhere: install the CLI, run the check, let the
exit status fail the job.

```sh
python3 -m pip install git+https://github.com/somethingwentwell/encode-the-correction.git
encode-the-correction check
```

| CI | Ready-made file |
|---|---|
| GitHub Actions | [examples/github-actions.yml](../examples/github-actions.yml), copy to `.github/workflows/` |
| GitLab CI | [examples/gitlab-ci.yml](../examples/gitlab-ci.yml), merge into `.gitlab-ci.yml` |
| Anything with a POSIX shell | [examples/ci.sh](../examples/ci.sh), run as `sh ci.sh` from the project root |

Things that differ between runners:

- **Pin what you install.** Append `@<commit-sha-or-tag>` to the git URL so a
  CI run does not change when this repository does.
- **No git in the image** (for example `python:3-slim`). Install from an
  archive instead:
  `python3 -m pip install https://github.com/somethingwentwell/encode-the-correction/archive/<commit-sha>.tar.gz`
- **A system Python that refuses `pip install`** ("externally managed
  environment"). Install into a virtual environment outside the checkout, as
  `examples/ci.sh` does, or use
  `pipx run --spec git+https://github.com/somethingwentwell/encode-the-correction.git encode-the-correction check`.
- **Nothing to install at all.** If the project committed
  `.encode-the-correction/cli.py`, the job can be the single line
  `python3 .encode-the-correction/cli.py check`.

## Session hooks are not a gate

The optional session-start hooks ([adapters.md](adapters.md)) run the same
checks and hand the result to the agent as context. They always exit 0 and
never fail or block a session, even when a correction is violated or the
registry is broken. Only `check`, the pre-commit hook and a CI job fail on a
violation.
