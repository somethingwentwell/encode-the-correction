"""Session adapters: documented schemas, merged without loss, all calling the same check.

The expectations here are written from the vendor docs (see docs/adapters.md), not
from the implementation, so a drift in either direction fails a test. No live
Claude Code / Cursor / Codex / Copilot session is needed: each hook command is
taken from the written config and executed directly, the way the runtime would.
"""
import json
import shlex
import shutil
import subprocess
import sys
import unittest

from support import REGISTRY, TOOL_DIR, ProjectCase

ANTI_PATTERN = "setTimeout(() => render(), 0);\n"
WRAPPER = TOOL_DIR + "/hooks/session-start.sh"

GROUP_KEYS = {"matcher", "hooks"}
DOCS = {
    "claude-code": {
        "file": ".claude/settings.json", "event": "SessionStart", "grouped": True, "version": False,
        "handler_keys": {"type", "command", "args", "timeout", "async", "asyncRewake", "shell",
                         "onFailure", "if", "statusMessage", "once"},
        "timeout_key": "timeout",
    },
    "cursor": {
        "file": ".cursor/hooks.json", "event": "sessionStart", "grouped": False, "version": True,
        "handler_keys": {"command", "type", "timeout", "loop_limit", "failClosed", "matcher"},
        "timeout_key": "timeout",
    },
    "codex": {
        "file": ".codex/hooks.json", "event": "SessionStart", "grouped": True, "version": False,
        "handler_keys": {"type", "command", "commandWindows", "timeout", "statusMessage",
                         "additionalContextLimit", "async"},
        "timeout_key": "timeout",
    },
    "copilot": {
        "file": ".github/hooks/encode-the-correction.json", "event": "sessionStart", "grouped": False, "version": True,
        "handler_keys": {"type", "bash", "powershell", "cwd", "env", "timeoutSec"},
        "timeout_key": "timeoutSec",
    },
}
# Unrelated, user-owned config that must survive adopt, re-adopt and uninstall untouched.
EXISTING = {
    "claude-code": {
        "permissions": {"allow": ["Bash(npm test:*)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}],
            "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo user-session"}]}],
        },
    },
    "cursor": {
        "version": 1,
        "hooks": {"afterFileEdit": [{"command": ".cursor/hooks/format.sh"}],
                  "sessionStart": [{"command": "echo user-session"}]},
    },
    "codex": {
        "hooks": {"SessionStart": [{"matcher": "startup|resume",
                                    "hooks": [{"type": "command", "command": "echo user-session", "timeout": 30}]}]},
    },
    "copilot": {
        "version": 1,
        "hooks": {"sessionStart": [{"type": "command", "bash": "echo user-session",
                                    "powershell": "Write-Output user-session", "cwd": ".", "timeoutSec": 10}]},
    },
}
# Hook stdin payloads, shaped as each vendor documents them.
STDIN = {
    "claude-code": {"session_id": "abc123", "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp",
                    "hook_event_name": "SessionStart", "source": "startup", "model": "claude-opus-5"},
    "cursor": {"session_id": "abc123", "is_background_agent": False, "composer_mode": "agent"},
    "codex": {"session_id": "abc123", "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp",
              "hook_event_name": "SessionStart", "model": "gpt-5", "source": "startup"},
    "copilot": {"sessionId": "abc123", "timestamp": 1795000000000, "cwd": "/tmp", "source": "new",
                "initialPrompt": "fix the bug"},
}


def handlers(doc, name):
    """This event's handler objects, flattened, as (handler, enclosing group or None)."""
    entries = doc["hooks"][DOCS[name]["event"]]
    if not DOCS[name]["grouped"]:
        return [(entry, None) for entry in entries]
    return [(handler, group) for group in entries for handler in group["hooks"]]


def is_ours(handler):
    return any(TOOL_DIR + "/" in str(handler.get(key, "")) for key in ("command", "bash", "powershell"))


class AdapterCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir()

    def our_handler(self, name):
        ours = [h for h, _ in handlers(self.read_json(DOCS[name]["file"]), name) if is_ours(h)]
        self.assertEqual(len(ours), 1, "exactly one %s hook for %s" % (TOOL_DIR, name))
        return ours[0]

    def run_hook(self, name, **kwargs):
        """Execute the installed hook command the way its runtime does."""
        handler = self.our_handler(name)
        stdin = json.dumps(STDIN[name])
        if name == "claude-code":
            # Shell form; Claude Code exports CLAUDE_PROJECT_DIR and runs from wherever the session is.
            return self.shell(handler["command"], cwd=self.tmp, stdin=stdin,
                              env={"CLAUDE_PROJECT_DIR": str(self.project)}, **kwargs)
        if name == "cursor":
            # Project hooks run from the project root.
            return self.shell(handler["command"], cwd=self.project, stdin=stdin, **kwargs)
        if name == "codex":
            # Runs with the session cwd, which may be a subdirectory of the repo.
            return self.shell(handler["command"], cwd=kwargs.pop("cwd", self.project), stdin=stdin, **kwargs)
        return self.shell(handler["bash"], cwd=self.project / handler["cwd"], stdin=stdin, shell="bash", **kwargs)

    def context(self, name, proc):
        """The text the runtime would hand to the agent."""
        if name == "cursor":
            return json.loads(proc.stdout)["additional_context"]
        if name == "copilot":
            return json.loads(proc.stdout)["additionalContext"]
        return proc.stdout


class SchemaTests(AdapterCase):
    def test_written_config_matches_the_documented_schema(self):
        self.cli("adopt", self.project, "--hooks", "all")
        for name, docs in DOCS.items():
            with self.subTest(runtime=name):
                doc = self.read_json(docs["file"])
                self.assertEqual(set(doc), {"version", "hooks"} if docs["version"] else {"hooks"})
                if docs["version"]:
                    self.assertEqual(doc["version"], 1)
                self.assertEqual(list(doc["hooks"]), [docs["event"]])
                entries = doc["hooks"][docs["event"]]
                self.assertEqual(len(entries), 1)
                if docs["grouped"]:
                    self.assertLessEqual(set(entries[0]), GROUP_KEYS)
                    self.assertNotIn("matcher", entries[0], "no matcher: run for every session source")
                handler = self.our_handler(name)
                self.assertLessEqual(set(handler), docs["handler_keys"], "only documented fields")
                self.assertIsInstance(handler[docs["timeout_key"]], int)
                if name != "cursor":
                    self.assertEqual(handler["type"], "command")
                if name == "copilot":
                    self.assertIn(WRAPPER, handler["bash"])
                    self.assertIsInstance(handler["powershell"], str)
                    self.assertEqual(handler["cwd"], ".")
                else:
                    self.assertIn(WRAPPER, handler["command"])
                    self.assertNotIn("args", handler, "shell form, not exec form")

    def test_commands_reference_the_project_the_documented_way(self):
        self.cli("adopt", self.project, "--hooks", "all")
        self.assertEqual(self.our_handler("claude-code")["command"],
                         'sh "${CLAUDE_PROJECT_DIR}"/%s claude-code' % WRAPPER)
        self.assertEqual(self.our_handler("cursor")["command"], "sh %s cursor" % WRAPPER)
        self.assertIn('"$(git rev-parse --show-toplevel', self.our_handler("codex")["command"])
        self.assertEqual(self.our_handler("copilot")["bash"], "sh %s copilot" % WRAPPER)

    def test_only_requested_adapters_are_written(self):
        self.cli("adopt", self.project, "--hooks", "cursor")
        self.assertTrue(self.exists(".cursor/hooks.json"))
        for rel in (".claude", ".codex", ".github"):
            self.assertFalse(self.exists(rel), rel)

    def test_claude_adapter_uses_shared_project_settings_only(self):
        self.cli("adopt", self.project, "--hooks", "claude")
        self.assertEqual(sorted(p.name for p in (self.project / ".claude").iterdir()), ["settings.json"])
        self.assertFalse(self.exists("CLAUDE.md"), "the hook does not require a CLAUDE.md")

    def test_undocumented_runtimes_are_rejected(self):
        before = self.snapshot()
        proc = self.cli("adopt", self.project, "--hooks", "windsurf", expect=2)
        self.assertIn("Unknown --hooks runtime", proc.stderr)
        self.assertEqual(self.snapshot(), before)


class MergeTests(AdapterCase):
    def seed(self, name, indent=4):
        text = json.dumps(EXISTING[name], indent=indent) + "\n"
        self.write(DOCS[name]["file"], text)
        return text

    def test_merge_keeps_unrelated_hooks_is_idempotent_and_uninstalls_cleanly(self):
        for name, docs in DOCS.items():
            with self.subTest(runtime=name):
                original = self.seed(name)
                self.cli("adopt", self.project, "--hooks", name)
                merged = self.read_json(docs["file"])

                # Everything the user had is still there, in place and unmodified.
                users = [h for h, _ in handlers(merged, name) if not is_ours(h)]
                self.assertEqual(users, [h for h, _ in handlers(EXISTING[name], name)])
                self.assertEqual({k: v for k, v in merged.items() if k != "hooks"},
                                 {k: v for k, v in EXISTING[name].items() if k != "hooks"})
                for event, value in EXISTING[name]["hooks"].items():
                    if event != docs["event"]:
                        self.assertEqual(merged["hooks"][event], value)
                self.assertEqual(merged["hooks"][docs["event"]][0], EXISTING[name]["hooks"][docs["event"]][0])
                self.our_handler(name)
                self.assertIn('\n    "hooks"', self.read(docs["file"]), "existing indentation is kept")

                once = self.read(docs["file"])
                self.cli("adopt", self.project, "--hooks", name)
                self.cli("adopt", self.project)
                self.assertEqual(self.read(docs["file"]), once)

                self.cli("uninstall", self.project)
                self.assertEqual(self.read(docs["file"]), original)

    def test_refresh_replaces_an_outdated_hook_in_place_without_duplicating(self):
        self.seed("claude-code")
        self.cli("adopt", self.project, "--hooks", "claude-code")
        doc = self.read_json(".claude/settings.json")
        current = json.dumps(doc)
        doc["hooks"]["SessionStart"][1]["hooks"][0] = {
            "type": "command", "command": "sh .encode-the-correction/hooks/old-name.sh"}
        doc["hooks"]["SessionStart"].append({"hooks": [{"type": "command", "command": "echo later-user-hook"}]})
        self.write(".claude/settings.json", json.dumps(doc, indent=4) + "\n")
        self.cli("adopt", self.project)
        after = self.read_json(".claude/settings.json")
        self.assertEqual(json.dumps(after["hooks"]["SessionStart"][:2]),
                         json.dumps(json.loads(current)["hooks"]["SessionStart"]))
        self.assertEqual(after["hooks"]["SessionStart"][2]["hooks"][0]["command"], "echo later-user-hook")

    def test_copilot_adapter_leaves_other_hook_files_alone(self):
        other = json.dumps(EXISTING["copilot"], indent=2) + "\n"
        self.write(".github/hooks/project-hooks.json", other)
        self.cli("adopt", self.project, "--hooks", "copilot")
        self.assertEqual(self.read(".github/hooks/project-hooks.json"), other)
        self.cli("uninstall", self.project)
        self.assertEqual(sorted(p.name for p in (self.project / ".github" / "hooks").iterdir()), ["project-hooks.json"])
        self.assertEqual(self.read(".github/hooks/project-hooks.json"), other)

    def test_a_version_key_added_to_an_existing_file_is_removed_again(self):
        original = json.dumps({"hooks": {"afterFileEdit": [{"command": "fmt"}]}}, indent=2) + "\n"
        self.write(".cursor/hooks.json", original)
        self.cli("adopt", self.project, "--hooks", "cursor")
        self.assertEqual(self.read_json(".cursor/hooks.json")["version"], 1)
        self.cli("uninstall", self.project)
        self.assertEqual(self.read(".cursor/hooks.json"), original)

    def test_invalid_or_unexpected_hook_files_abort_before_anything_is_written(self):
        cases = {
            "not json": "{ this is not json",
            "jsonc comment": '{\n  // my settings\n  "hooks": {}\n}\n',
            "top-level array": "[]\n",
            "hooks not an object": '{"hooks": []}\n',
            "event not an array": '{"hooks": {"SessionStart": {}}}\n',
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                self.write(".claude/settings.json", text)
                self.write("AGENTS.md", "# mine\n")
                before = self.snapshot()
                proc = self.cli("adopt", self.project, "--hooks", "claude-code", expect=2)
                self.assertIn(".claude/settings.json", proc.stderr)
                self.assertIn("nothing was changed", proc.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_an_unknown_schema_version_is_not_guessed_at(self):
        self.write(".cursor/hooks.json", '{"version": 2, "hooks": {}}\n')
        before = self.snapshot()
        proc = self.cli("adopt", self.project, "--hooks", "cursor", expect=2)
        self.assertIn("version", proc.stderr)
        self.assertEqual(self.snapshot(), before)


class InvocationTests(AdapterCase):
    def setUp(self):
        super().setUp()
        self.cli("adopt", self.project, "--hooks", "all")

    def seed_violation(self):
        self.write("src/a.js", ANTI_PATTERN)
        self.encode_timer_check()

    def test_every_hook_reports_exactly_what_check_reports(self):
        self.seed_violation()
        check = self.vendored("check", "--max-findings", "5", expect=1).stdout.strip()
        for name in DOCS:
            with self.subTest(runtime=name):
                proc = self.run_hook(name)  # exit 0: a session hook informs, it does not block
                self.assertEqual(self.context(name, proc).strip(), check)
                self.assertEqual(proc.stderr, "")

    def test_every_hook_reports_a_pass_once_the_root_cause_is_fixed(self):
        self.seed_violation()
        self.write("src/a.js", "store.invalidate();\n")
        for name in DOCS:
            with self.subTest(runtime=name):
                text = self.context(name, self.run_hook(name))
                self.assertIn("OK - all 1 encoded corrections pass", text)
                self.assertIn("encode the correction as a check", text)

    def test_json_runtimes_get_only_their_documented_output_field(self):
        self.seed_violation()
        self.assertEqual(list(json.loads(self.run_hook("cursor").stdout)), ["additional_context"])
        self.assertEqual(list(json.loads(self.run_hook("copilot").stdout)), ["additionalContext"])

    def test_hooks_never_fail_the_session_even_when_the_registry_is_broken(self):
        self.write(REGISTRY, "{broken")
        for name in DOCS:
            with self.subTest(runtime=name):
                text = self.context(name, self.run_hook(name))
                self.assertIn("could not run the checks", text)

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_codex_hook_finds_the_project_from_a_subdirectory_of_a_git_repo(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, env=self.env)
        self.seed_violation()
        proc = self.run_hook("codex", cwd=self.project / "src")
        self.assertIn("FAIL no-timer-quick-fixes", proc.stdout)

    def test_copilot_powershell_command_invokes_the_same_cli_entry_point(self):
        self.seed_violation()
        argv = shlex.split(self.our_handler("copilot")["powershell"])
        self.assertEqual(argv, ["python", TOOL_DIR + "/cli.py", "session-hook", "--runtime", "copilot"])
        direct = self.run_argv([sys.executable] + argv[1:], cwd=self.project)
        self.assertEqual(direct.stdout, self.run_hook("copilot").stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "PowerShell not available")
    def test_copilot_powershell_command_runs_under_powershell(self):
        self.seed_violation()
        proc = self.run_argv(["pwsh", "-NoProfile", "-Command", self.our_handler("copilot")["powershell"]],
                             cwd=self.project)
        self.assertIn("FAIL no-timer-quick-fixes", json.loads(proc.stdout)["additionalContext"])

    def test_wrapper_runs_from_any_directory_and_defaults_to_plain_text(self):
        self.seed_violation()
        proc = self.run_argv(["sh", self.project / WRAPPER], cwd=self.tmp)
        self.assertIn("FAIL no-timer-quick-fixes", proc.stdout)

    def test_uninstall_removes_the_wrapper_and_every_hook_entry(self):
        self.cli("uninstall", self.project)
        self.assertEqual(self.snapshot(), {})


if __name__ == "__main__":
    unittest.main()
