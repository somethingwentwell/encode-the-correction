"""init, adopt, uninstall: preserve what was there, stay idempotent, remove only our own."""
import json
import os
import unittest

from support import BEGIN, END, REGISTRY, SKILL_PATHS, TOOL_DIR, VENDORED, ProjectCase, skill_tree

USER_AGENTS = "# Agent notes\n\nUse tabs. Run `make test` before committing.\n"
USER_CLAUDE = "# Claude notes\n\nPrefer small diffs.\n"


class InitTests(ProjectCase):
    def test_init_creates_a_new_project(self):
        self.assertFalse(self.project.exists())
        out = self.cli("init", self.project).stdout
        self.assertIn("created", out)
        self.assertEqual(
            sorted(self.snapshot()),
            sorted([TOOL_DIR, VENDORED, REGISTRY, TOOL_DIR + "/manifest.json", "AGENTS.md"] + skill_tree()),
        )
        for rel in SKILL_PATHS:
            self.assertIn("name: encode-the-correction", self.read(rel))
        agents = self.read("AGENTS.md")
        self.assertTrue(agents.startswith(BEGIN))
        self.assertTrue(agents.endswith(END + "\n"))
        self.assertIn("python3 .encode-the-correction/cli.py check", agents)
        self.assertIn("python3 .encode-the-correction/cli.py encode", agents)
        self.assertEqual(self.read_json(REGISTRY), {"version": 1, "corrections": []})

    def test_init_installs_a_working_vendored_copy_identical_to_the_source(self):
        from support import CLI
        self.cli("init", self.project)
        self.assertEqual((self.project / VENDORED).read_bytes(), CLI.read_bytes())
        self.assertIn("no corrections are encoded yet", self.vendored("check").stdout)

    def test_init_writes_no_claude_md_and_no_hooks_by_default(self):
        self.cli("init", self.project)
        for rel in ("CLAUDE.md", ".claude/settings.json", ".cursor", ".codex", ".github"):
            self.assertFalse(self.exists(rel), rel)
        self.assertTrue(self.exists(".claude/skills/encode-the-correction/SKILL.md"))
        self.assertTrue(self.exists(".agents/skills/encode-the-correction/SKILL.md"))

    def test_init_refuses_an_existing_agents_file_and_writes_nothing(self):
        self.write("AGENTS.md", USER_AGENTS)
        before = self.snapshot()
        proc = self.cli("init", self.project, expect=2)
        self.assertIn("adopt", proc.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_init_refuses_to_run_twice(self):
        self.cli("init", self.project)
        before = self.snapshot()
        self.cli("init", self.project, expect=2)
        self.assertEqual(self.snapshot(), before)

    def test_init_refuses_an_existing_hook_file_for_a_requested_adapter(self):
        self.write(".cursor/hooks.json", '{"version": 1, "hooks": {}}\n')
        before = self.snapshot()
        self.cli("init", self.project, "--hooks", "cursor", expect=2)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_writes_nothing(self):
        out = self.cli("init", self.project, "--hooks", "all", "--dry-run").stdout
        self.assertIn("would create", out)
        self.assertFalse(self.project.exists())



    def test_init_refuses_an_existing_skill_file_and_writes_nothing(self):
        self.write(SKILL_PATHS[0], "---\nname: other\ndescription: not ours\n---\n\n# Keep me\n")
        before = self.snapshot()
        proc = self.cli("init", self.project, expect=2)
        self.assertIn("adopt", proc.stderr)
        self.assertEqual(self.snapshot(), before)


class SkillInstallTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir()

    def test_adopt_installs_the_same_skill_in_both_auto_load_paths(self):
        from encode_the_correction import SKILL_BODY
        self.cli("adopt", self.project)
        for rel in SKILL_PATHS:
            self.assertEqual(self.read(rel), SKILL_BODY)
        # Cursor/Codex/Copilot load .agents/skills; Claude Code loads .claude/skills.

    def test_adopt_refreshes_a_stale_skill_it_installed(self):
        from encode_the_correction import SKILL_BODY
        self.cli("adopt", self.project)
        stale = SKILL_PATHS[0]
        self.write(stale, "---\nname: encode-the-correction\ndescription: stale\n---\n\n# stale\n")
        # hash in the manifest still matches the original, so this counts as an edit
        proc = self.cli("adopt", self.project, expect=2)
        self.assertIn("--force", proc.stderr)
        self.cli("adopt", self.project, "--force")
        self.assertEqual(self.read(stale), SKILL_BODY)

    def test_adopt_does_not_overwrite_a_foreign_skill_without_force(self):
        foreign = "---\nname: encode-the-correction\ndescription: already here\n---\n\n# Mine\n"
        self.write(SKILL_PATHS[1], foreign)
        proc = self.cli("adopt", self.project, expect=2)
        self.assertIn(SKILL_PATHS[1], proc.stderr)
        self.assertEqual(self.read(SKILL_PATHS[1]), foreign)

    def test_uninstall_removes_the_skill_and_empty_skill_directories(self):
        self.write("src/app.py", "print('hi')\n")
        self.cli("adopt", self.project)
        self.cli("uninstall", self.project, "--purge")
        self.assertEqual(self.snapshot(), {"src": None, "src/app.py": b"print('hi')\n"})
        for rel in (".agents", ".claude"):
            self.assertFalse(self.exists(rel), rel)

    def test_uninstall_leaves_other_skills_alone(self):
        self.write(".agents/skills/other/SKILL.md", "---\nname: other\ndescription: keep\n---\n\n# Other\n")
        self.write(".claude/settings.json", "{\n  \"permissions\": {}\n}\n")
        before_other = self.read(".agents/skills/other/SKILL.md")
        self.cli("adopt", self.project)
        self.cli("uninstall", self.project, "--purge")
        self.assertEqual(self.read(".agents/skills/other/SKILL.md"), before_other)
        self.assertFalse(self.exists(SKILL_PATHS[0]))
        self.assertFalse(self.exists(SKILL_PATHS[1]))
        self.assertTrue(self.exists(".claude/settings.json"))


class AdoptTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir()

    def test_adopt_requires_an_existing_directory(self):
        proc = self.cli("adopt", self.tmp / "missing", expect=2)
        self.assertIn("init", proc.stderr)
        self.assertFalse((self.tmp / "missing").exists())

    def test_adopt_preserves_existing_agents_and_claude_content(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.write("CLAUDE.md", USER_CLAUDE)
        self.write("src/app.py", "print('hi')\n")
        self.cli("adopt", self.project)

        agents = self.read("AGENTS.md")
        self.assertTrue(agents.startswith(USER_AGENTS), "user content must stay first and untouched")
        self.assertEqual(agents.count(BEGIN), 1)
        self.assertEqual(agents.count(END), 1)

        claude = self.read("CLAUDE.md")
        self.assertTrue(claude.startswith(USER_CLAUDE))
        block = claude[claude.index(BEGIN):]
        self.assertEqual(block.splitlines()[1:], ["@AGENTS.md", END], "thin pointer: just the import")
        self.assertEqual(self.read("src/app.py"), "print('hi')\n")

    def test_repeated_adopt_is_idempotent(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.write("CLAUDE.md", USER_CLAUDE)
        self.write(".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(ls)"]}}, indent=2) + "\n")
        self.cli("adopt", self.project, "--hooks", "all")
        first = self.snapshot()
        out = self.cli("adopt", self.project, "--hooks", "all").stdout
        self.assertEqual(self.snapshot(), first)
        self.assertNotIn("created", out)
        self.assertNotIn("merged", out)
        # Without --hooks the already-installed adapters are kept, not dropped.
        self.cli("adopt", self.project)
        self.vendored("adopt")
        self.assertEqual(self.snapshot(), first)
        self.assertEqual(self.read("AGENTS.md").count(BEGIN), 1)
        self.assertEqual(self.read("CLAUDE.md").count("@AGENTS.md"), 1)

    def test_adopt_refreshes_a_stale_block_in_place(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.cli("adopt", self.project)
        fresh = self.read("AGENTS.md")
        start, end = fresh.index(BEGIN), fresh.index(END)
        begin_line = fresh[start:fresh.index("\n", start) + 1]
        stale = fresh[:start] + begin_line + "old instructions from a previous version\n" + fresh[end:] + "\n## After\n\nMore user notes.\n"
        self.write("AGENTS.md", stale)
        out = self.cli("adopt", self.project).stdout
        self.assertIn("refreshed", out)
        after = self.read("AGENTS.md")
        self.assertEqual(after, fresh + "\n## After\n\nMore user notes.\n")
        self.assertNotIn("old instructions", after)

    def test_claude_md_is_not_created_when_absent(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.cli("adopt", self.project)
        self.assertFalse(self.exists("CLAUDE.md"))

    def test_claude_md_always_creates_a_thin_import(self):
        self.cli("adopt", self.project, "--claude-md", "always")
        self.assertEqual(self.read("CLAUDE.md").splitlines()[1:], ["@AGENTS.md", END])

    def test_claude_md_never_leaves_it_untouched(self):
        self.write("CLAUDE.md", USER_CLAUDE)
        self.cli("adopt", self.project, "--claude-md", "never")
        self.assertEqual(self.read("CLAUDE.md"), USER_CLAUDE)

    def test_claude_md_that_already_imports_agents_is_untouched(self):
        text = "@AGENTS.md\n\n## Claude Code\n\nUse plan mode for big changes.\n"
        self.write("CLAUDE.md", text)
        self.cli("adopt", self.project)
        self.assertEqual(self.read("CLAUDE.md"), text)

    def test_an_import_mentioned_only_in_code_does_not_count(self):
        self.write("CLAUDE.md", "Do not write `@AGENTS.md` here.\n")
        self.cli("adopt", self.project)
        self.assertIn(BEGIN, self.read("CLAUDE.md"))

    def test_dot_claude_claude_md_gets_a_relative_import(self):
        self.write(".claude/CLAUDE.md", USER_CLAUDE)
        self.cli("adopt", self.project)
        self.assertFalse(self.exists("CLAUDE.md"))
        self.assertIn("\n@../AGENTS.md\n", self.read(".claude/CLAUDE.md"))

    def test_claude_md_symlinked_to_agents_is_left_alone(self):
        self.write("AGENTS.md", USER_AGENTS)
        os.symlink("AGENTS.md", str(self.project / "CLAUDE.md"))
        self.cli("adopt", self.project)
        self.assertTrue((self.project / "CLAUDE.md").is_symlink())
        self.assertEqual(self.read("AGENTS.md").count(BEGIN), 1)

    def test_crlf_files_keep_their_line_endings(self):
        crlf = USER_AGENTS.replace("\n", "\r\n")
        self.write("AGENTS.md", crlf)
        self.cli("adopt", self.project)
        merged = self.read("AGENTS.md")
        self.assertTrue(merged.startswith(crlf))
        self.assertNotIn("\n", merged.replace("\r\n", ""))
        self.cli("uninstall", self.project)
        self.assertEqual(self.read("AGENTS.md"), crlf)

    def test_unbalanced_markers_are_refused_and_nothing_changes(self):
        self.write("AGENTS.md", USER_AGENTS + BEGIN + " -->\nhalf a block, no end marker\n")
        before = self.snapshot()
        proc = self.cli("adopt", self.project, "--hooks", "all", expect=2)
        self.assertIn("unbalanced", proc.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_agents_symlink_leading_outside_the_project_is_refused(self):
        outside = self.tmp / "outside-AGENTS.md"
        outside.write_text("shared notes\n")
        os.symlink(str(outside), str(self.project / "AGENTS.md"))
        proc = self.cli("adopt", self.project, expect=2)
        self.assertIn("outside the project", proc.stderr)
        self.assertEqual(outside.read_text(), "shared notes\n")
        self.assertFalse(self.exists(TOOL_DIR))

    def test_dry_run_writes_nothing(self):
        self.write("AGENTS.md", USER_AGENTS)
        before = self.snapshot()
        out = self.cli("adopt", self.project, "--hooks", "all", "--dry-run").stdout
        self.assertIn("would merge", out)
        self.assertEqual(self.snapshot(), before)


class UninstallTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir()

    def test_uninstall_restores_a_preexisting_project_byte_for_byte(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.write("CLAUDE.md", USER_CLAUDE.rstrip("\n"))  # no trailing newline, on purpose
        self.write("README.md", "# App\n")
        self.write("src/app.py", "print('hi')\n")
        self.write(".claude/settings.json", json.dumps(
            {"permissions": {"allow": ["Bash(ls)"]},
             "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}]}},
            indent=4) + "\n")
        self.write(".github/workflows/ci.yml", "on: push\n")
        before = self.snapshot()

        self.cli("adopt", self.project, "--hooks", "all")
        self.assertNotEqual(self.snapshot(), before)
        self.cli("uninstall", self.project)
        self.assertEqual(self.snapshot(), before)

    def test_uninstall_after_init_removes_everything_the_tool_added(self):
        self.cli("init", self.project / "fresh", "--hooks", "all", "--claude-md", "always")
        self.assertIn("CLAUDE.md", self.snapshot(self.project / "fresh"))
        self.cli("uninstall", self.project / "fresh")
        self.assertEqual(self.snapshot(self.project / "fresh"), {})

    def test_uninstall_from_the_vendored_copy(self):
        self.write("AGENTS.md", USER_AGENTS)
        before = self.snapshot()
        self.cli("adopt", self.project, "--hooks", "claude-code,copilot")
        self.vendored("uninstall")
        self.assertEqual(self.snapshot(), before)

    def test_uninstall_keeps_user_content_added_to_files_the_tool_created(self):
        self.cli("adopt", self.project, "--claude-md", "always", "--hooks", "cursor")
        self.write("AGENTS.md", self.read("AGENTS.md") + "\n## Mine\n\nKeep me.\n")
        hooks = self.read_json(".cursor/hooks.json")
        hooks["hooks"]["afterFileEdit"] = [{"command": ".cursor/hooks/format.sh"}]
        self.write(".cursor/hooks.json", json.dumps(hooks, indent=2) + "\n")
        self.cli("uninstall", self.project)
        self.assertEqual(self.read("AGENTS.md"), "\n## Mine\n\nKeep me.\n")
        self.assertEqual(self.read_json(".cursor/hooks.json"),
                         {"version": 1, "hooks": {"afterFileEdit": [{"command": ".cursor/hooks/format.sh"}]}})
        self.assertFalse(self.exists("CLAUDE.md"))
        self.assertFalse(self.exists(TOOL_DIR))

    def test_uninstall_keeps_encoded_corrections_unless_purge(self):
        self.cli("adopt", self.project)
        self.encode_timer_check()
        registry = self.read(REGISTRY)

        out = self.cli("uninstall", self.project).stdout
        self.assertIn("--purge", out)
        self.assertEqual(self.read(REGISTRY), registry, "encoded corrections are never deleted by default")
        self.assertEqual(sorted(self.snapshot()), [TOOL_DIR, REGISTRY])

        # Adopting again picks the kept registry back up.
        self.cli("adopt", self.project)
        self.assertEqual(self.read(REGISTRY), registry)

        self.cli("uninstall", self.project, "--purge")
        self.assertEqual(self.snapshot(), {})

    def test_uninstall_keeps_an_edited_tool_file_unless_purge(self):
        self.cli("adopt", self.project)
        edited = self.read(VENDORED) + "\n# local patch\n"
        self.write(VENDORED, edited)
        out = self.cli("uninstall", self.project).stdout
        self.assertIn("kept", out)
        self.assertEqual(self.read(VENDORED), edited)
        self.cli("uninstall", self.project, "--purge")
        self.assertFalse(self.exists(TOOL_DIR))

    def test_uninstall_leaves_files_it_did_not_add(self):
        self.cli("adopt", self.project)
        self.write(TOOL_DIR + "/notes.txt", "mine\n")
        out = self.cli("uninstall", self.project, "--purge").stdout
        self.assertIn("notes.txt", out)
        self.assertEqual(sorted(self.snapshot()), [TOOL_DIR, TOOL_DIR + "/notes.txt"])

    def test_uninstall_dry_run_removes_nothing(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.cli("adopt", self.project, "--hooks", "all")
        before = self.snapshot()
        out = self.cli("uninstall", self.project, "--dry-run", "--purge").stdout
        self.assertIn("would remove", out)
        self.assertEqual(self.snapshot(), before)

    def test_uninstall_without_a_manifest_still_removes_tagged_content_only(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.write(".codex/hooks.json", json.dumps(
            {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}, indent=2) + "\n")
        before = self.snapshot()
        self.cli("adopt", self.project, "--hooks", "codex")
        os.remove(str(self.project / TOOL_DIR / "manifest.json"))
        self.cli("uninstall", self.project)
        self.assertEqual(self.snapshot(), before)

    def test_uninstall_on_a_project_without_the_tool_is_a_no_op(self):
        self.write("AGENTS.md", USER_AGENTS)
        before = self.snapshot()
        self.assertIn("nothing", self.cli("uninstall", self.project).stdout)
        self.assertEqual(self.snapshot(), before)


class OverwriteProtectionTests(ProjectCase):
    """Destructive paths need an explicit flag; by default they do not run."""

    def setUp(self):
        super().setUp()
        self.project.mkdir()

    def test_adopt_does_not_overwrite_an_edited_tool_file_without_force(self):
        self.cli("adopt", self.project, "--hooks", "cursor")
        hook = TOOL_DIR + "/hooks/session-start.sh"
        self.write(hook, "#!/bin/sh\necho custom\n")
        before = self.snapshot()
        proc = self.cli("adopt", self.project, expect=2)
        self.assertIn("--force", proc.stderr)
        self.assertEqual(self.snapshot(), before)
        self.cli("adopt", self.project, "--force")
        self.assertIn("session-hook", self.read(hook))

    def test_adopt_does_not_claim_a_foreign_file_at_its_path_without_force(self):
        self.write(VENDORED, "print('some other cli.py')\n")
        before = self.snapshot()
        self.cli("adopt", self.project, expect=2)
        self.assertEqual(self.snapshot(), before)

    def test_adopt_never_overwrites_the_registry_even_with_force(self):
        self.cli("adopt", self.project)
        self.encode_timer_check()
        registry = self.read(REGISTRY)
        self.cli("adopt", self.project, "--force")
        self.assertEqual(self.read(REGISTRY), registry)

    def test_encode_does_not_replace_an_existing_correction_without_replace(self):
        self.cli("adopt", self.project)
        self.encode_timer_check()
        registry = self.read(REGISTRY)
        proc = self.encode_timer_check("--why", "second attempt", expect=2)
        self.assertIn("--replace", proc.stderr)
        self.assertEqual(self.read(REGISTRY), registry)
        self.encode_timer_check("--why", "second attempt", "--replace")
        corrections = self.read_json(REGISTRY)["corrections"]
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["why"], "second attempt")


if __name__ == "__main__":
    unittest.main()
