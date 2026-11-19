---
name: encode-the-correction
description: Turn a human correction of a coding agent into a deterministic project-local check, and run those checks. Use when starting work in this project, before finishing a task, when the user corrects you, when a mistake might recur, or when you are about to add a comment, rule, or AGENTS.md line about a mistake.
---

# Encode the correction

This project turns corrections into deterministic checks instead of prose rules.

## Command

Prefer `encode-the-correction` if it is on `PATH`. Otherwise, from the project root (the directory that contains `.encode-the-correction/`):

```sh
python3 .encode-the-correction/cli.py
```

Both take the same arguments. Run them from anywhere inside the project; they find the registry themselves. Do not copy this tool's GitHub repository into the project.

## When you start, and before you finish

Run `check`. If it fails, fix the named root cause in the code. Do not weaken or delete the check, add an exclude, or leave a comment justifying an exception unless a human explicitly approves it.

Exit codes: 0 pass, 1 violations, 2 configuration or usage error.

## When the human corrects you

Do not add a rule, comment, or AGENTS.md line. First try to make the mistake impossible (API, types, directory structure). Otherwise encode it:

```sh
encode-the-correction encode --id <kebab-id> --forbid '<regex>' --glob '<files>' --message '<what is wrong>' --fix '<root-cause fix>'
```

`--require REGEX` and `--forbid-path GLOB` are also available. Then run `check`: it must fail on the mistake and pass once the root cause is fixed. Show the user both results.

Exceptions live only in the registry (`--exclude GLOB`). Nothing in the registry or in project files is executed.

## If the tool is not in this project yet

This skill expects `encode-the-correction adopt` to have been run. Offer to install it. Do not invent a check runner of your own.
