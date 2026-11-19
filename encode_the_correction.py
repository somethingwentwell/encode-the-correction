#!/usr/bin/env python3
"""encode-the-correction: turn agent corrections into deterministic checks.

Whenever you correct a coding agent, encode the correction as a check that fails
on the mistake (or remove the class of mistake in the API/architecture) instead
of adding another unenforceable comment, rule, or AGENTS.md line.

Single file, Python 3.8+ standard library only. Checks are data (regexes and
globs in a project-local JSON registry); this tool never executes registry
text, file content, or anything else it reads.

The same file runs three ways: installed (`pip install`, console script
`encode-the-correction`), straight from a checkout (`python3
encode_the_correction.py`), and vendored into a project by `adopt`
(`python3 .encode-the-correction/cli.py`).

Exit codes: 0 ok, 1 a check failed, 2 usage/configuration error.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

VERSION = "0.3.0"
TOOL = "encode-the-correction"
TOOL_DIR = ".encode-the-correction"
CLI_REL = TOOL_DIR + "/cli.py"
REGISTRY_REL = TOOL_DIR + "/corrections.json"
MANIFEST_REL = TOOL_DIR + "/manifest.json"
HOOK_REL = TOOL_DIR + "/hooks/session-start.sh"
SKILL_NAME = "encode-the-correction"
SKILL_PATHS = (
    ".agents/skills/%s/SKILL.md" % SKILL_NAME,
    ".claude/skills/%s/SKILL.md" % SKILL_NAME,
)
RUN = "python3 " + CLI_REL

BEGIN_MARK = (
    "<!-- BEGIN encode-the-correction: managed block, refreshed by `adopt`."
    " Keep your own notes outside it. -->"
)
END_MARK = "<!-- END encode-the-correction -->"
BEGIN_RE = re.compile(r"<!-- BEGIN encode-the-correction\b[^\n]*?-->")

AGENTS_BODY = """\
## Encode the correction

This project turns corrections into deterministic checks instead of prose rules.

- **Run the checks** when you start a session and before you call a task done:
  `{run} check`
- **A failing check names a root cause. Fix that root cause in the code.** Do not
  weaken or delete the check, add an exclude, or leave a comment justifying an
  exception unless a human explicitly approves it.
- **When a human corrects you** (or you catch yourself repeating a mistake), do not
  add another rule, comment, or line to this file. First try to make the mistake
  impossible (API, types, directory structure). Otherwise encode it as a check:
  `{run} encode --id <kebab-id> --forbid '<regex>' --glob '<files>' --message '<what is wrong>' --fix '<root-cause fix>'`
  Then run `check` again: it must fail on the mistake and pass once the root cause is fixed.
- Prose in this file is the last resort, for judgment calls that cannot be checked.
- A project skill is also installed at `.agents/skills/encode-the-correction/` (and
  `.claude/skills/encode-the-correction/` for Claude Code). Agents that support
  Agent Skills should load it and follow it automatically.""".format(run=RUN)

HOOK_SH = """\
#!/bin/sh
# encode-the-correction session adapter (managed file, refreshed by `adopt`).
# Every supported runtime's session-start hook calls this one wrapper, which runs
# the same portable check as `{run} check`.
# Usage: session-start.sh <claude-code|cursor|codex|copilot|plain>
# Always exits 0: a session hook informs the agent, it never blocks the session.
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 0
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1 &&
    "$py" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    "$py" "$dir/../cli.py" session-hook --runtime "${{1:-plain}}"
    exit 0
  fi
done
echo "encode-the-correction: Python 3.8+ not found; session check skipped." >&2
exit 0
""".format(run=RUN)

SKILL_BODY = """\
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
"""

# Session adapters. Each schema below was re-verified against the vendor's
# official documentation (see docs/adapters.md); do not add a runtime here
# without confirming its documented schema first.
ADAPTERS = {
    "claude-code": {
        "file": ".claude/settings.json",
        "event": "SessionStart",
        "shape": "grouped",
        "handler": {
            "type": "command",
            "command": 'sh "${CLAUDE_PROJECT_DIR}"/' + HOOK_REL + " claude-code",
            "timeout": 60,
        },
        "docs": "https://code.claude.com/docs/en/hooks",
        "note": "project-scoped SessionStart hook; Claude Code runs it once the workspace is trusted",
    },
    "cursor": {
        "file": ".cursor/hooks.json",
        "event": "sessionStart",
        "shape": "flat",
        "version": 1,
        "handler": {"command": "sh " + HOOK_REL + " cursor", "timeout": 60},
        "docs": "https://cursor.com/docs/hooks",
        "note": "sessionStart is not supported by Cursor cloud agents",
    },
    "codex": {
        "file": ".codex/hooks.json",
        "event": "SessionStart",
        "shape": "grouped",
        "handler": {
            "type": "command",
            "command": 'sh "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/' + HOOK_REL + '" codex',
            "timeout": 60,
        },
        "docs": "https://learn.chatgpt.com/docs/hooks",
        "note": "Codex skips project hooks until you review and trust them with /hooks",
    },
    "copilot": {
        "file": ".github/hooks/encode-the-correction.json",
        "event": "sessionStart",
        "shape": "flat",
        "version": 1,
        "handler": {
            "type": "command",
            "bash": "sh " + HOOK_REL + " copilot",
            "powershell": "python " + CLI_REL + " session-hook --runtime copilot",
            "cwd": ".",
            "timeoutSec": 60,
        },
        "docs": "https://docs.github.com/en/copilot/concepts/customizations/hooks",
        "note": "used by Copilot CLI and Copilot cloud agent",
    },
}
ADAPTER_ALIASES = {"claude": "claude-code", "claudecode": "claude-code", "github-copilot": "copilot"}
OURS_MARK = TOOL_DIR + "/"

CHECK_TYPES = ("forbid-pattern", "require-pattern", "forbid-path")
SKIP_DIRS = frozenset(
    [".git", ".hg", ".svn", TOOL_DIR, "node_modules", ".venv", "venv", "__pycache__",
     ".tox", ".mypy_cache", ".pytest_cache"]
)
# This tool's own skill folders are instructions, not project source.
SKIP_PREFIXES = tuple(path.rsplit("/", 1)[0] for path in SKILL_PATHS)


def skipped_rel(rel):
    rel = rel.replace(chr(92), "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if SKIP_DIRS.intersection(rel.split("/")[:-1]):
        return True
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in SKIP_PREFIXES)


MAX_FILE_BYTES = 2 * 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ToolError(Exception):
    """A usage or configuration problem. The message says what to fix."""


# --------------------------------------------------------------------------
# Project-scoped file access
# --------------------------------------------------------------------------

class Project:
    """All reads and writes go through here so nothing escapes the project root."""

    def __init__(self, root):
        self.root = os.path.realpath(root)

    def path(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def real(self, rel):
        """Real path for `rel`, refusing symlinks that lead outside the project."""
        real = os.path.realpath(self.path(rel))
        if real != self.root and not real.startswith(self.root + os.sep):
            raise ToolError(
                "%s resolves outside the project (%s). Refusing to touch it; "
                "replace the symlink with a real file inside the project." % (rel, real)
            )
        return real

    def exists(self, rel):
        return os.path.exists(self.path(rel))

    def read(self, rel):
        try:
            with open(self.real(rel), "r", encoding="utf-8", newline="") as handle:
                return handle.read()
        except UnicodeDecodeError:
            raise ToolError("%s is not valid UTF-8; refusing to modify it." % rel)

    def write(self, rel, text):
        real = self.real(rel)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        tmp = real + ".etc-tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if os.path.exists(real):
            shutil.copymode(real, tmp)
        os.replace(tmp, real)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def own_source():
    with open(os.path.abspath(__file__), "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def find_project(start):
    """The nearest directory at or above `start` that holds a registry, else None.

    The search never leaves the repository `start` is in: a directory with a
    `.git` entry is the last one looked at.
    """
    current = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(current, *REGISTRY_REL.split("/"))):
            return current
        parent = os.path.dirname(current)
        if parent == current or os.path.lexists(os.path.join(current, ".git")):
            return None
        current = parent


def default_project(discover=False):
    """The vendored copy defaults to the project it lives in; otherwise the cwd.

    With `discover`, an installed or checked-out CLI finds the project the way a
    linter finds its config: the cwd, or the nearest parent holding a registry.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(here) == TOOL_DIR:
        return os.path.dirname(here)
    cwd = os.getcwd()
    return (find_project(cwd) if discover else None) or cwd


def run_command(project):
    """How to invoke the tool in `project`: its vendored copy when it has one, else the installed CLI."""
    return RUN if project.exists(CLI_REL) else TOOL


# --------------------------------------------------------------------------
# Manifest: what this tool added, so uninstall removes exactly that
# --------------------------------------------------------------------------

def new_manifest():
    return {"version": 1, "tool": TOOL, "tool_version": VERSION, "files": {},
            "blocks": {}, "json": {}, "created_dirs": [], "adapters": []}


def load_manifest(project):
    manifest = new_manifest()
    if project.exists(MANIFEST_REL):
        try:
            data = json.loads(project.read(MANIFEST_REL))
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key, default in manifest.items():
                if isinstance(data.get(key), type(default)):
                    manifest[key] = data[key]
    manifest["tool_version"] = VERSION
    return manifest


def dump_json(doc, indent="  ", sort_keys=False):
    return json.dumps(doc, indent=indent, ensure_ascii=False, sort_keys=sort_keys) + "\n"


WOULD = {"created": "would create", "merged": "would merge",
         "refreshed": "would refresh", "removed": "would remove"}


class Plan:
    """Everything is validated and planned first, then applied; an error writes nothing."""

    def __init__(self, project, manifest):
        self.project = project
        self.manifest = manifest
        self.ops = []
        self.report = []

    def note(self, status, rel, detail=""):
        self.report.append((status, rel, detail))

    def write(self, rel, text, status, detail=""):
        self.project.real(rel)
        parts = rel.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            parent = "/".join(parts[:depth])
            if not self.project.exists(parent) and parent not in self.manifest["created_dirs"]:
                self.manifest["created_dirs"].append(parent)
        self.ops.append(("write", rel, text))
        self.note(status, rel, detail)

    def delete(self, rel, detail=""):
        self.project.real(rel)
        self.ops.append(("delete", rel, None))
        self.note("removed", rel, detail)

    def rmdir_if_empty(self, rel):
        self.ops.append(("rmdir", rel, None))

    def rmtree(self, rel):
        self.project.real(rel)
        self.ops.append(("rmtree", rel, None))

    def changed(self):
        return any(op[0] in ("write", "delete", "rmtree") for op in self.ops)

    def apply(self):
        for kind, rel, text in self.ops:
            if kind == "write":
                self.project.write(rel, text)
                continue
            target = self.project.path(rel)
            if kind == "delete":
                if os.path.lexists(target):
                    os.remove(target)
            elif kind == "rmtree":
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
            elif kind == "rmdir":
                if os.path.isdir(target) and not os.path.islink(target) and not os.listdir(target):
                    os.rmdir(target)

    def show(self, title, dry_run):
        print("%s %s: %s%s" % (TOOL, title, self.project.root, " (dry run, nothing written)" if dry_run else ""))
        for status, rel, detail in self.report:
            if dry_run:
                status = WOULD.get(status, status)
            print("  %-14s %s%s" % (status, rel, " (%s)" % detail if detail else ""))


# --------------------------------------------------------------------------
# Tagged blocks in Markdown instruction files
# --------------------------------------------------------------------------

def find_block(text, rel):
    begins = list(BEGIN_RE.finditer(text))
    ends = [m.start() for m in re.finditer(re.escape(END_MARK), text)]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0].end():
        raise ToolError(
            "%s: the %s markers are unbalanced or duplicated. Repair them by hand "
            "(exactly one BEGIN line followed by one END line) and re-run; nothing was changed." % (rel, TOOL)
        )
    return begins[0].start(), ends[0] + len(END_MARK)


def plan_block(plan, rel, body):
    """Insert or refresh this tool's tagged block in `rel`, leaving all other content as is."""
    project, manifest = plan.project, plan.manifest
    existed = os.path.exists(project.real(rel))
    text = project.read(rel) if existed else ""
    nl = "\r\n" if "\r\n" in text else "\n"
    block = BEGIN_MARK + nl + body.replace("\n", nl) + nl + END_MARK
    previous = manifest["blocks"].get(rel) or {}
    info = {"created_file": bool(previous.get("created_file")) if existed else True,
            "prefix": previous.get("prefix")}
    span = find_block(text, rel)
    if span:
        new = text[:span[0]] + block + text[span[1]:]
        status, detail = "refreshed", "tagged block updated in place"
    else:
        if not text or text.endswith(nl + nl):
            prefix = ""
        elif text.endswith(nl):
            prefix = nl
        else:
            prefix = nl + nl
        info["prefix"] = prefix
        new = text + prefix + block + nl
        status, detail = ("merged", "tagged block added; existing content preserved") if existed else ("created", "")
    manifest["blocks"][rel] = info
    if new == text:
        plan.note("unchanged", rel)
    else:
        plan.write(rel, new, status, detail)


def plan_unblock(plan, rel):
    project = plan.project
    if not os.path.exists(project.real(rel)):
        return
    text = project.read(rel)
    span = find_block(text, rel)
    if not span:
        return
    info = plan.manifest["blocks"].get(rel) or {}
    nl = "\r\n" if "\r\n" in text else "\n"
    before, after = text[:span[0]], text[span[1]:]
    if after.startswith(nl):
        after = after[len(nl):]
    prefix = info.get("prefix")
    if prefix is None:
        prefix = nl if before.endswith(nl + nl) else ""
    if prefix and before.endswith(prefix):
        before = before[:len(before) - len(prefix)]
    remaining = before + after
    if info.get("created_file") and not remaining.strip():
        plan.delete(rel, "this tool created it and nothing else is in it")
    else:
        plan.write(rel, remaining, "removed", "tagged block only; your content kept")


def has_agents_import(text):
    """True when a CLAUDE.md already imports AGENTS.md (imports in code are not evaluated)."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    return re.search(r"(?<![\w`])@(?:\.{1,2}/)*AGENTS\.md\b", text) is not None


def plan_claude_md(plan, mode):
    """Thin CLAUDE.md pointer, only when it stops Claude Code diverging from AGENTS.md."""
    project = plan.project
    if mode == "never":
        return
    target = next((rel for rel in ("CLAUDE.md", ".claude/CLAUDE.md") if project.exists(rel)), None)
    if target is None:
        if mode != "always":
            return
        target = "CLAUDE.md"
    try:
        real = project.real(target)
    except ToolError:
        plan.note("skipped", target, "symlink to a file outside the project; add `@AGENTS.md` to it yourself")
        return
    if os.path.exists(real) and real == project.real("AGENTS.md"):
        plan.note("unchanged", target, "same file as AGENTS.md")
        return
    text = project.read(target) if os.path.exists(real) else ""
    if find_block(text, target) is None and has_agents_import(text):
        plan.note("unchanged", target, "already imports AGENTS.md")
        return
    plan_block(plan, target, "@AGENTS.md" if target == "CLAUDE.md" else "@../AGENTS.md")


# --------------------------------------------------------------------------
# Session adapters: merge one hook into a documented JSON config file
# --------------------------------------------------------------------------

def load_json_object(project, rel):
    text = project.read(rel)
    try:
        doc = json.loads(text) if text.strip() else {}
    except ValueError as exc:
        raise ToolError(
            "%s is not valid JSON (%s). Fix or remove it, or re-run without that "
            "adapter; nothing was changed." % (rel, exc)
        )
    if not isinstance(doc, dict):
        raise ToolError("%s must contain a JSON object at the top level; nothing was changed." % rel)
    match = re.search(r"\n([ \t]+)\"", text)
    return doc, (match.group(1) if match else "  ")


def is_ours(handler):
    return isinstance(handler, dict) and any(
        isinstance(handler.get(key), str) and OURS_MARK in handler[key]
        for key in ("command", "bash", "powershell")
    )


def replace_ours(entries, shape, handler):
    """Put `handler` where this tool's handler is (or append it); None removes it.

    Entries that are not ours are never modified, reordered, or dropped.
    """
    placed = handler is None
    out = []
    for entry in entries:
        if shape == "flat":
            if not is_ours(entry):
                out.append(entry)
            elif not placed:
                out.append(copy.deepcopy(handler))
                placed = True
            continue
        inner = entry.get("hooks") if isinstance(entry, dict) else None
        if not isinstance(inner, list) or not any(is_ours(h) for h in inner):
            out.append(entry)
            continue
        kept = []
        for item in inner:
            if not is_ours(item):
                kept.append(item)
            elif not placed:
                kept.append(copy.deepcopy(handler))
                placed = True
        if kept:
            entry["hooks"] = kept
            out.append(entry)
    if not placed:
        out.append(copy.deepcopy(handler) if shape == "flat" else {"hooks": [copy.deepcopy(handler)]})
    entries[:] = out


def plan_adapter(plan, name):
    project, manifest, spec = plan.project, plan.manifest, ADAPTERS[name]
    rel, event = spec["file"], spec["event"]
    existed = os.path.exists(project.real(rel))
    doc, indent = load_json_object(project, rel) if existed else ({}, "  ")
    original = copy.deepcopy(doc)
    flags = {"created_file": not existed, "created_version": False,
             "created_hooks": False, "created_event": False}
    if "version" in spec:
        if "version" not in doc:
            doc = dict([("version", spec["version"])] + list(doc.items()))
            flags["created_version"] = True
        elif doc["version"] != spec["version"]:
            raise ToolError(
                "%s declares version %r, but only the documented version %r schema is "
                "supported (%s); nothing was changed." % (rel, doc["version"], spec["version"], spec["docs"])
            )
    hooks = doc.get("hooks")
    if hooks is None:
        hooks = doc["hooks"] = {}
        flags["created_hooks"] = True
    elif not isinstance(hooks, dict):
        raise ToolError('%s: "hooks" must be an object (%s); nothing was changed.' % (rel, spec["docs"]))
    entries = hooks.get(event)
    if entries is None:
        entries = hooks[event] = []
        flags["created_event"] = True
    elif not isinstance(entries, list):
        raise ToolError('%s: hooks.%s must be an array (%s); nothing was changed.' % (rel, event, spec["docs"]))
    replace_ours(entries, spec["shape"], spec["handler"])
    previous = manifest["json"].get(rel)
    manifest["json"][rel] = previous if (existed and isinstance(previous, dict)) else flags
    if name not in manifest["adapters"]:
        manifest["adapters"].append(name)
    if doc == original and list(doc) == list(original):
        plan.note("unchanged", rel, "%s adapter" % name)
    elif existed:
        plan.write(rel, dump_json(doc, indent), "merged", "%s %s hook; other settings preserved" % (name, event))
    else:
        plan.write(rel, dump_json(doc, indent), "created", "%s %s hook" % (name, event))


def plan_unadapter(plan, name):
    project, spec = plan.project, ADAPTERS[name]
    rel, event = spec["file"], spec["event"]
    if not os.path.exists(project.real(rel)):
        return
    try:
        doc, indent = load_json_object(project, rel)
    except ToolError:
        plan.note("skipped", rel, "not valid JSON; remove any %s hook from it by hand" % TOOL)
        return
    original = copy.deepcopy(doc)
    info = plan.manifest["json"].get(rel) or {}
    hooks = doc.get("hooks")
    entries = hooks.get(event) if isinstance(hooks, dict) else None
    if isinstance(entries, list):
        had = len(entries)
        replace_ours(entries, spec["shape"], None)
        if not entries and had and info.get("created_event", True):
            del hooks[event]
        if not hooks and info.get("created_hooks"):
            del doc["hooks"]
    if info.get("created_file"):
        if doc != original and not [key for key in doc if key != "version"]:
            plan.delete(rel, "this tool created it and nothing else is in it")
            return
    elif info.get("created_version") and doc.get("version") == spec.get("version"):
        del doc["version"]
    if doc != original:
        plan.write(rel, dump_json(doc, indent), "removed", "%s hook only; other settings kept" % name)


# --------------------------------------------------------------------------
# init / adopt / uninstall
# --------------------------------------------------------------------------

def parse_adapters(values):
    names = []
    for value in values or []:
        for raw in value.split(","):
            name = ADAPTER_ALIASES.get(raw.strip().lower(), raw.strip().lower())
            if not name or name == "none":
                continue
            if name == "all":
                names.extend(ADAPTERS)
            elif name in ADAPTERS:
                names.append(name)
            else:
                raise ToolError(
                    "Unknown --hooks runtime %r. Supported (documented) runtimes: %s. "
                    "Other runtimes rely on AGENTS.md." % (raw, ", ".join(ADAPTERS))
                )
    return list(dict.fromkeys(names))


def plan_owned(plan, rel, content, force):
    """A file this tool owns outright. Never overwrites content it did not write, unless forced."""
    project, manifest = plan.project, plan.manifest
    if not os.path.exists(project.real(rel)):
        plan.write(rel, content, "created")
    else:
        current = project.read(rel)
        recorded = (manifest["files"].get(rel) or {}).get("sha256")
        if current == content:
            plan.note("unchanged", rel)
            manifest["files"].setdefault(rel, {"sha256": sha256(content)})
            return
        if recorded != sha256(current) and not force:
            raise ToolError(
                "%s exists with content this tool did not write (or it was edited). "
                "Re-run with --force to overwrite it; nothing was changed." % rel
            )
        plan.write(rel, content, "refreshed")
    manifest["files"][rel] = {"sha256": sha256(content)}


def install(args, fresh):
    root = os.path.abspath(args.project or default_project())
    requested = parse_adapters(args.hooks)
    if fresh:
        if os.path.exists(root) and not os.path.isdir(root):
            raise ToolError("%s is not a directory." % root)
        probe = Project(root)
        taken = [rel for rel in [TOOL_DIR, "AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md"]
                 + list(SKILL_PATHS)
                 + [ADAPTERS[name]["file"] for name in requested] if probe.exists(rel)]
        if taken:
            raise ToolError(
                "%s already has %s. `init` is for new projects and never touches existing "
                "files; use `adopt`, which merges into them and preserves their content."
                % (root, ", ".join(taken))
            )
    elif not os.path.isdir(root):
        raise ToolError("%s is not an existing directory. Use `init` to start a new project." % root)

    project = Project(root)
    manifest = load_manifest(project)
    plan = Plan(project, manifest)
    adapters = list(dict.fromkeys(manifest["adapters"] + requested))

    plan_owned(plan, CLI_REL, own_source(), args.force)
    if project.exists(REGISTRY_REL):
        plan.note("unchanged", REGISTRY_REL, "your encoded corrections are never overwritten")
    else:
        plan.write(REGISTRY_REL, dump_json({"version": 1, "corrections": []}), "created")
    if adapters:
        plan_owned(plan, HOOK_REL, HOOK_SH, args.force)
    plan_block(plan, "AGENTS.md", AGENTS_BODY)
    for rel in SKILL_PATHS:
        plan_owned(plan, rel, SKILL_BODY, args.force)
    plan_claude_md(plan, args.claude_md)
    for name in adapters:
        plan_adapter(plan, name)
    manifest["adapters"] = [name for name in ADAPTERS if name in adapters]
    manifest["created_dirs"] = sorted(set(manifest["created_dirs"]))
    manifest_text = dump_json(manifest, sort_keys=True)
    if not project.exists(MANIFEST_REL) or project.read(MANIFEST_REL) != manifest_text:
        plan.write(MANIFEST_REL, manifest_text, "refreshed" if project.exists(MANIFEST_REL) else "created")

    if not args.dry_run:
        os.makedirs(root, exist_ok=True)
        plan.apply()
    plan.show("init" if fresh else "adopt", args.dry_run)
    for name in adapters:
        print("  note: %s - %s (%s)" % (name, ADAPTERS[name]["note"], ADAPTERS[name]["docs"]))
    print("  note: project skill installed at %s so agents that support "
          "Agent Skills can follow these instructions automatically." % ", ".join(SKILL_PATHS))
    if not adapters:
        print("  note: no session hook installed; agents run the check when they follow "
              "AGENTS.md or the installed skill. Add a hook with --hooks %s." % "|".join(ADAPTERS))
    print("Next: %s check" % RUN)
    return 0


def cmd_init(args):
    return install(args, fresh=True)


def cmd_adopt(args):
    return install(args, fresh=False)


def registry_is_empty(project):
    try:
        data = json.loads(project.read(REGISTRY_REL))
    except (ValueError, ToolError):
        return False
    return isinstance(data, dict) and data.get("corrections") == [] and set(data) <= {"version", "corrections"}


def cmd_uninstall(args):
    root = os.path.abspath(args.project or default_project())
    if not os.path.isdir(root):
        raise ToolError("%s is not an existing directory." % root)
    project = Project(root)
    manifest = load_manifest(project)
    plan = Plan(project, manifest)

    problems = 0
    steps = [(plan_unblock, rel) for rel in
             dict.fromkeys(["AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md"] + list(manifest["blocks"]))]
    steps += [(plan_unadapter, name) for name in ADAPTERS]
    for step, target in steps:
        try:
            step(plan, target)
        except ToolError as exc:
            problems += 1
            plan.note("skipped", ADAPTERS[target]["file"] if target in ADAPTERS else target, str(exc))

    kept = []
    running_vendored = os.path.realpath(__file__) == os.path.realpath(project.path(CLI_REL))
    owned = [(CLI_REL, None if running_vendored else own_source()), (HOOK_REL, HOOK_SH)]
    owned.extend((rel, SKILL_BODY) for rel in SKILL_PATHS)
    for rel, pristine in owned:
        if not project.exists(rel):
            continue
        current = project.read(rel)
        recorded = (manifest["files"].get(rel) or {}).get("sha256")
        if args.purge or recorded == sha256(current) or (recorded is None and current == pristine):
            plan.delete(rel)
        else:
            kept.append(rel)
            plan.note("kept", rel, "edited since install; --purge deletes it")
    if project.exists(REGISTRY_REL):
        if args.purge or registry_is_empty(project):
            plan.delete(REGISTRY_REL)
        else:
            kept.append(REGISTRY_REL)
            plan.note("kept", REGISTRY_REL, "holds your encoded corrections; --purge deletes it")
    if project.exists(MANIFEST_REL):
        plan.delete(MANIFEST_REL)
    for rel in (TOOL_DIR + "/__pycache__", TOOL_DIR + "/hooks/__pycache__"):
        if project.exists(rel):
            plan.rmtree(rel)
    skill_dirs = []
    for rel in SKILL_PATHS:
        parts = rel.split("/")
        skill_dirs.extend("/".join(parts[:i]) for i in range(len(parts) - 1, 0, -1))
    for rel in [TOOL_DIR + "/hooks", TOOL_DIR] + sorted(set(list(manifest["created_dirs"]) + skill_dirs), key=len, reverse=True):
        plan.rmdir_if_empty(rel)

    if not plan.report:
        print("%s uninstall: nothing from this tool found in %s" % (TOOL, root))
        return 0
    if not args.dry_run:
        plan.apply()
    plan.show("uninstall", args.dry_run)
    if not args.dry_run and os.path.isdir(project.path(TOOL_DIR)):
        extra = sorted(set(os.listdir(project.path(TOOL_DIR))) - {rel.split("/")[1] for rel in kept})
        if extra:
            print("  note: left files this tool did not add in %s/: %s" % (TOOL_DIR, ", ".join(extra)))
    return 1 if problems else 0


# --------------------------------------------------------------------------
# Registry of encoded corrections
# --------------------------------------------------------------------------

def glob_to_regex(glob):
    """Translate a path glob: `**` spans directories, `*` and `?` stay in one segment.

    A glob without a slash (`*.py`) matches at any depth; `dir/` means everything under it.
    """
    pattern = glob.strip().replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    if not pattern:
        raise ToolError("Empty glob.")
    anchored = "/" in pattern.rstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    if not anchored:
        pattern = "**/" + pattern
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[" and "]" in pattern[i + 2:]:
            end = pattern.index("]", i + 2)
            body = pattern[i + 1:end].replace("\\", "\\\\")
            out.append("[" + ("^" + body[1:] if body[0] in "!^" else body) + "]")
            i = end
        else:
            out.append(re.escape(char))
        i += 1
    try:
        return re.compile("".join(out) + r"\Z")
    except re.error as exc:
        raise ToolError("Invalid glob %r (%s)." % (glob, exc))


class Correction:
    def __init__(self, raw, where):
        if not isinstance(raw, dict):
            raise ToolError("%s: each correction must be a JSON object." % where)
        self.id = raw.get("id")
        if not isinstance(self.id, str) or not ID_RE.match(self.id):
            raise ToolError("%s: \"id\" must be kebab-case (a-z, 0-9, -), got %r." % (where, self.id))
        where = "%s: correction '%s'" % (where, self.id)
        self.type = raw.get("type")
        if self.type not in CHECK_TYPES:
            raise ToolError("%s: \"type\" must be one of %s, got %r." % (where, ", ".join(CHECK_TYPES), self.type))
        for key in ("message", "fix"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise ToolError(
                    "%s: \"%s\" is required. A check must say what is wrong and how to fix the root cause." % (where, key)
                )
        self.message, self.fix = raw["message"].strip(), raw["fix"].strip()
        self.why = raw["why"].strip() if isinstance(raw.get("why"), str) else ""
        self.include = self._globs(raw.get("include", ["**"] if self.type == "forbid-pattern" else None), where, "include")
        self.exclude = self._globs(raw.get("exclude", []), where, "exclude")
        if not self.include:
            raise ToolError("%s: \"include\" needs at least one glob for a %s check." % (where, self.type))
        self.regex = None
        if self.type != "forbid-path":
            pattern = raw.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ToolError("%s: \"pattern\" (a regular expression) is required." % where)
            try:
                self.regex = re.compile(pattern, re.IGNORECASE if raw.get("ignore_case") else 0)
            except re.error as exc:
                raise ToolError("%s: \"pattern\" is not a valid regular expression (%s)." % (where, exc))

    @staticmethod
    def _globs(value, where, key):
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ToolError("%s: \"%s\" must be a list of glob strings." % (where, key))
        return [(item, glob_to_regex(item)) for item in value]

    def applies(self, rel):
        return any(rx.match(rel) for _, rx in self.include) and not any(rx.match(rel) for _, rx in self.exclude)


def load_registry_raw(project):
    if not project.exists(REGISTRY_REL):
        raise ToolError(
            "No registry at %s. This project has not adopted %s yet; run `adopt` "
            "(existing project) or `init` (new project) first." % (project.path(REGISTRY_REL), TOOL)
        )
    try:
        data = json.loads(project.read(REGISTRY_REL))
    except ValueError as exc:
        raise ToolError("%s is not valid JSON (%s). Fix the syntax; no checks were run." % (REGISTRY_REL, exc))
    if not isinstance(data, dict) or not isinstance(data.get("corrections"), list):
        raise ToolError('%s must be an object with a "corrections" list.' % REGISTRY_REL)
    return data


def load_corrections(project):
    corrections = [Correction(raw, REGISTRY_REL) for raw in load_registry_raw(project)["corrections"]]
    seen = set()
    for correction in corrections:
        if correction.id in seen:
            raise ToolError("%s: duplicate correction id '%s'. Ids must be unique." % (REGISTRY_REL, correction.id))
        seen.add(correction.id)
    return corrections


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------

def list_files(root):
    """Project files as sorted posix-style relative paths (git-aware, never follows symlinks)."""
    names = None
    if os.path.exists(os.path.join(root, ".git")):
        try:
            done = subprocess.run(
                ["git", "-C", root, "ls-files", "-c", "-o", "--exclude-standard", "-z"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
            )
            if done.returncode == 0:
                names = [name for name in done.stdout.decode("utf-8", "surrogateescape").split("\0") if name]
        except (OSError, subprocess.SubprocessError):
            names = None
    if names is None:
        names = []
        for base, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(base, d)))
            for name in files:
                names.append(os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/"))
    result = []
    for rel in sorted(set(names)):
        full = os.path.join(root, *rel.split("/"))
        if skipped_rel(rel) or os.path.islink(full) or not os.path.isfile(full):
            continue
        result.append(rel)
    return result


def read_lines(root, rel, cache):
    """Lines of a text file, or None for binary, oversized, or unreadable files."""
    if rel not in cache:
        lines = None
        full = os.path.join(root, *rel.split("/"))
        try:
            if os.path.getsize(full) <= MAX_FILE_BYTES:
                with open(full, "rb") as handle:
                    data = handle.read()
                if b"\0" not in data[:8192]:
                    lines = data.decode("utf-8", "replace").splitlines()
        except OSError:
            lines = None
        cache[rel] = lines
    return cache[rel]


def run_checks(project, corrections):
    files = list_files(project.root)
    cache, results = {}, []
    for correction in corrections:
        findings = []
        for rel in files:
            if not correction.applies(rel):
                continue
            if correction.type == "forbid-path":
                findings.append({"path": rel, "line": None, "text": "this path must not exist"})
                continue
            lines = read_lines(project.root, rel, cache)
            if lines is None:
                continue
            if correction.type == "forbid-pattern":
                for number, line in enumerate(lines, 1):
                    if correction.regex.search(line):
                        findings.append({"path": rel, "line": number, "text": line.strip()[:160]})
            elif not any(correction.regex.search(line) for line in lines):
                findings.append({"path": rel, "line": None,
                                 "text": "missing required pattern /%s/" % correction.regex.pattern})
        results.append({"id": correction.id, "type": correction.type, "ok": not findings,
                        "message": correction.message, "fix": correction.fix, "why": correction.why,
                        "findings": findings})
    return results, len(files)


def render_report(results, file_count, max_findings, run=RUN):
    failed = [result for result in results if not result["ok"]]
    if not results:
        return ("%s: no corrections are encoded yet (0 checks). When a human corrects you, encode "
                "the correction as a check instead of a prose rule: %s encode --help" % (TOOL, run))
    if not failed:
        return "%s: OK - all %d encoded corrections pass (%d files scanned)." % (TOOL, len(results), file_count)
    lines = []
    total = 0
    for result in failed:
        findings = result["findings"]
        total += len(findings)
        lines.append("FAIL %s (%s, %d finding%s)" % (result["id"], result["type"], len(findings),
                                                    "" if len(findings) == 1 else "s"))
        for finding in findings[:max_findings]:
            where = finding["path"] + (":%d" % finding["line"] if finding["line"] else "")
            lines.append("  %s: %s" % (where, finding["text"]))
        if len(findings) > max_findings:
            lines.append("  ... and %d more" % (len(findings) - max_findings))
        lines.append("  Problem: %s" % result["message"])
        lines.append("  Root-cause fix: %s" % result["fix"])
        if result["why"]:
            lines.append("  Why this check exists: %s" % result["why"])
        lines.append("")
    lines.append("%s: %d of %d encoded corrections violated (%d finding%s, %d files scanned)."
                 % (TOOL, len(failed), len(results), total, "" if total == 1 else "s", file_count))
    lines.append("Fix the root cause in the code. Do not weaken a check, add an exclude, or justify "
                 "an exception in a comment unless a human approves it.")
    return "\n".join(lines)


def cmd_check(args):
    project = Project(args.project or default_project(discover=True))
    corrections = load_corrections(project)
    if args.only:
        unknown = sorted(set(args.only) - {c.id for c in corrections})
        if unknown:
            raise ToolError("No encoded correction with id: %s. See `list`." % ", ".join(unknown))
        corrections = [c for c in corrections if c.id in args.only]
    results, file_count = run_checks(project, corrections)
    ok = all(result["ok"] for result in results)
    if args.format == "json":
        print(json.dumps({"ok": ok, "files_scanned": file_count, "corrections": results}, indent=2))
    else:
        print(render_report(results, file_count, args.max_findings, run_command(project)))
    return 0 if ok else 1


def cmd_list(args):
    project = Project(args.project or default_project(discover=True))
    corrections = load_corrections(project)
    if not corrections:
        print("%s: no corrections encoded yet. Add one with: %s encode --help" % (TOOL, run_command(project)))
        return 0
    for c in corrections:
        target = "/%s/ in " % c.regex.pattern if c.regex else ""
        scope = ", ".join(glob for glob, _ in c.include)
        if c.exclude:
            scope += " (except %s)" % ", ".join(glob for glob, _ in c.exclude)
        print("%s [%s] %s%s\n    %s\n    fix: %s" % (c.id, c.type, target, scope, c.message, c.fix))
    return 0


# --------------------------------------------------------------------------
# encode
# --------------------------------------------------------------------------

def cmd_encode(args):
    project = Project(args.project or default_project(discover=True))
    data = load_registry_raw(project)
    entry = {"id": args.id}
    if args.forbid is not None:
        entry.update(type="forbid-pattern", pattern=args.forbid)
    elif args.require is not None:
        entry.update(type="require-pattern", pattern=args.require)
    else:
        entry["type"] = "forbid-path"
        args.glob = [args.forbid_path] + (args.glob or [])
    if args.glob:
        entry["include"] = args.glob
    elif entry["type"] == "require-pattern":
        raise ToolError("--require needs at least one --glob naming the files that must contain the pattern.")
    if args.exclude:
        entry["exclude"] = args.exclude
    if args.ignore_case:
        entry["ignore_case"] = True
    entry["message"], entry["fix"] = args.message, args.fix
    if args.why:
        entry["why"] = args.why
    correction = Correction(entry, "encode")

    existing = [i for i, raw in enumerate(data["corrections"]) if isinstance(raw, dict) and raw.get("id") == args.id]
    if existing and not args.replace:
        raise ToolError(
            "A correction with id '%s' already exists. Choose a new id, or pass --replace "
            "to overwrite the existing check; the registry was not changed." % args.id
        )
    if existing:
        data["corrections"][existing[0]] = entry
    else:
        data["corrections"].append(entry)
    project.write(REGISTRY_REL, dump_json(data))

    results, _ = run_checks(project, [correction])
    count = len(results[0]["findings"])
    run = run_command(project)
    print("%s: %s '%s' (%s) in %s" % (TOOL, "replaced" if existing else "encoded", args.id, entry["type"], REGISTRY_REL))
    if count:
        print("It fails right now at %d place%s. Run `%s check` and fix each one at the root cause."
              % (count, "" if count == 1 else "s", run))
    else:
        print("It matches nothing right now. If the mistake is still in the code, the pattern or globs "
              "are wrong: a check that never fails enforces nothing. Verify with `%s check`." % run)
    return 0


# --------------------------------------------------------------------------
# session-hook: what every runtime adapter calls
# --------------------------------------------------------------------------

def session_text(project):
    corrections = load_corrections(project)
    results, file_count = run_checks(project, corrections)
    run = run_command(project)
    text = render_report(results, file_count, 5, run)
    if results and all(result["ok"] for result in results):
        text += (" If a human corrects you in this session, encode the correction as a check "
                 "(see AGENTS.md): %s encode --help" % run)
    if len(text) > 6000:
        text = text[:6000] + "\n... truncated; run `%s check` for the full report." % run
    return text


def cmd_session_hook(args):
    run = RUN
    try:
        project = Project(args.project or default_project(discover=True))
        run = run_command(project)
        text = session_text(project)
    except Exception as exc:  # a session hook must never break the session it reports to
        text = "%s: could not run the checks (%s). Run `%s check` yourself." % (TOOL, exc, run)
    if args.runtime == "cursor":
        print(json.dumps({"additional_context": text}))
    elif args.runtime == "copilot":
        print(json.dumps({"additionalContext": text}))
    else:
        print(text)
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog=TOOL,
        description="Turn agent corrections into deterministic, project-local checks.",
        epilog="Checks are data (regexes and globs); nothing in the registry is ever executed.",
    )
    parser.add_argument("--version", action="version", version="%s %s" % (TOOL, VERSION))
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    here = "project directory (default: the project this script is vendored in, else the current directory)"
    found = here + "; an installed CLI also looks in parent directories for the nearest registry"

    def add(name, func, help_text, project_help=found):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("project", nargs="?", help=project_help)
        p.set_defaults(func=func)
        return p

    for name, func, text in (
        ("init", cmd_init, "Start in a new project, including a project skill agents load automatically. Refuses to touch existing instruction, skill, or hook files."),
        ("adopt", cmd_adopt, "Adopt into an existing project. Merges, preserves existing content, installs the project skill, idempotent."),
    ):
        p = add(name, func, text, here)
        p.add_argument("--hooks", action="append", metavar="RUNTIME[,RUNTIME]",
                       help="also install a session-start hook for: %s, or all (default: none)" % ", ".join(ADAPTERS))
        p.add_argument("--claude-md", choices=("auto", "always", "never"), default="auto",
                       help="CLAUDE.md pointer importing @AGENTS.md: auto adds it only to an existing "
                            "CLAUDE.md, always creates one, never skips it (default: auto)")
        p.add_argument("--force", action="store_true",
                       help="overwrite this tool's own files even if they were edited")
        p.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")

    p = add("uninstall", cmd_uninstall,
            "Remove this tool's tagged blocks, hook entries, skill files, and files. Keeps everything else.", here)
    p.add_argument("--purge", action="store_true",
                   help="also delete the registry of encoded corrections and tool files you edited")
    p.add_argument("--dry-run", action="store_true", help="show what would be removed; remove nothing")

    p = add("check", cmd_check, "Run every encoded correction. Exit 0 when all pass, 1 when any is "
                                "violated, 2 on a configuration error.")
    p.add_argument("--only", action="append", metavar="ID", help="run only this correction (repeatable)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--max-findings", type=int, default=20, metavar="N",
                   help="findings shown per correction in text output (default: 20)")

    p = add("encode", cmd_encode, "Encode a correction as a check in the project-local registry.")
    p.add_argument("--id", required=True, help="kebab-case name for the correction")
    kind = p.add_mutually_exclusive_group(required=True)
    kind.add_argument("--forbid", metavar="REGEX", help="fail on any line matching this regex")
    kind.add_argument("--require", metavar="REGEX", help="fail when a file selected by --glob has no matching line")
    kind.add_argument("--forbid-path", metavar="GLOB", help="fail when a file matching this glob exists")
    p.add_argument("--glob", action="append", metavar="GLOB",
                   help="files to check, e.g. 'src/**/*.ts' (repeatable; default for --forbid: all files)")
    p.add_argument("--exclude", action="append", metavar="GLOB",
                   help="files exempt from the check; the one reviewable place for exceptions (repeatable)")
    p.add_argument("--ignore-case", action="store_true")
    p.add_argument("--message", required=True, help="what is wrong")
    p.add_argument("--fix", required=True, help="how to fix the root cause (not how to silence the check)")
    p.add_argument("--why", help="the correction that prompted this check")
    p.add_argument("--replace", action="store_true", help="overwrite an existing correction with the same id")

    add("list", cmd_list, "List the encoded corrections.")

    p = add("session-hook", cmd_session_hook,
            "Adapter entry point: run the checks and print the result as session context. Always exits 0.")
    p.add_argument("--runtime", choices=tuple(ADAPTERS) + ("plain",), default="plain")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        # A path or source line the terminal cannot encode must not crash a check:
        # a traceback exits 1, which CI would read as "a correction was violated".
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ToolError, OSError) as exc:
        print("%s: error: %s" % (TOOL, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
