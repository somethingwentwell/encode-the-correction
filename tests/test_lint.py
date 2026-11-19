"""check is lint-like: exit 0 / 1 / 2, stable `path:line` output, and it finds the project from the cwd.

Everything here runs the tool the way an installed console script does (see
`ProjectCase.installed`), from inside a temp project, with no project argument.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

from support import CONSOLE, REGISTRY, TOOL_DIR, VENDORED, ProjectCase

ANTI_PATTERN = "export function refresh() {\n  setTimeout(() => render(), 0);\n}\n"
FIXED = "export function refresh() {\n  store.invalidate();\n}\n"
TIMER = {
    "id": "no-timer-quick-fixes", "type": "forbid-pattern", "pattern": r"\bsetTimeout\(",
    "include": ["src/**/*.js"],
    "message": "Imperative timers patch over a state-flow problem.",
    "fix": "Drive the update from state and delete the timer.",
}
UTILS = {
    "id": "no-utils-dumping-ground", "type": "forbid-path", "include": ["src/utils/**"],
    "message": "src/utils is a dumping ground with no owner.",
    "fix": "Put the function in the module that owns the concept.",
}
FAIL_FAST = {
    "id": "shell-scripts-fail-fast", "type": "require-pattern", "pattern": "^set -eu", "include": ["scripts/*.sh"],
    "message": "Shell script keeps going after a failed command.",
    "fix": "Add `set -eu` right after the shebang.",
}
# The complete text report for the project built by `seed_three_violations`.
GOLDEN = """\
FAIL no-timer-quick-fixes (forbid-pattern, 3 findings)
  src/a.js:2: setTimeout(() => render(), 0);
  src/a.js:5: setTimeout(() => render(), 0);
  src/ui/b.js:2: setTimeout(() => render(), 0);
  Problem: Imperative timers patch over a state-flow problem.
  Root-cause fix: Drive the update from state and delete the timer.

FAIL no-utils-dumping-ground (forbid-path, 1 finding)
  src/utils/helpers.js: this path must not exist
  Problem: src/utils is a dumping ground with no owner.
  Root-cause fix: Put the function in the module that owns the concept.

FAIL shell-scripts-fail-fast (require-pattern, 1 finding)
  scripts/deploy.sh: missing required pattern /^set -eu/
  Problem: Shell script keeps going after a failed command.
  Root-cause fix: Add `set -eu` right after the shebang.

encode-the-correction: 3 of 3 encoded corrections violated (5 findings, 6 files scanned).
Fix the root cause in the code. Do not weaken a check, add an exclude, or justify an exception in a comment unless a human approves it.
"""


class LintCase(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir()
        self.installed("adopt", cwd=self.project)

    def set_registry(self, *corrections):
        self.write(REGISTRY, json.dumps({"version": 1, "corrections": list(corrections)}, indent=2) + "\n")

    def seed_three_violations(self):
        self.set_registry(TIMER, UTILS, FAIL_FAST)
        self.write("src/ui/b.js", ANTI_PATTERN)
        self.write("src/a.js", ANTI_PATTERN + ANTI_PATTERN)
        self.write("src/utils/helpers.js", "export {};\n")
        self.write("scripts/deploy.sh", "#!/bin/sh\necho deploy\n")
        self.write("scripts/build.sh", "#!/bin/sh\nset -eu\necho build\n")

    def check(self, *args, **kwargs):
        kwargs.setdefault("cwd", self.project)
        return self.installed("check", *args, **kwargs)


class ExitCodeTests(LintCase):
    def test_exit_0_when_every_correction_passes(self):
        self.assertIn("no corrections are encoded yet", self.check(expect=0).stdout)
        self.set_registry(TIMER)
        self.write("src/a.js", FIXED)
        proc = self.check(expect=0)
        self.assertEqual(proc.stdout, "encode-the-correction: OK - all 1 encoded corrections pass (2 files scanned).\n")
        self.assertEqual(proc.stderr, "")

    def test_exit_1_on_a_violation_and_0_again_after_the_fix(self):
        self.set_registry(TIMER)
        self.write("src/a.js", ANTI_PATTERN)
        proc = self.check(expect=1)
        self.assertIn("  src/a.js:2: setTimeout(() => render(), 0);\n", proc.stdout)
        self.assertEqual(proc.stderr, "", "findings belong on stdout")
        self.write("src/a.js", FIXED)
        self.check(expect=0)

    def test_exit_2_on_configuration_errors_with_nothing_on_stdout(self):
        cases = {
            "not json": "{not json",
            "wrong shape": json.dumps({"version": 1, "corrections": {}}),
            "bad regex": json.dumps({"version": 1, "corrections": [dict(TIMER, pattern="(")]}),
            "unknown type": json.dumps({"version": 1, "corrections": [dict(TIMER, type="forbid-patern")]}),
            "no fix": json.dumps({"version": 1, "corrections": [dict(TIMER, fix="")]}),
            "duplicate id": json.dumps({"version": 1, "corrections": [TIMER, TIMER]}),
        }
        self.write("src/a.js", ANTI_PATTERN)  # a violation is present, yet a broken registry must not report 1
        for name, text in cases.items():
            with self.subTest(case=name):
                self.write(REGISTRY, text)
                proc = self.check(expect=2)
                self.assertEqual(proc.stdout, "")
                self.assertTrue(proc.stderr.startswith("encode-the-correction: error: "), proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_exit_2_when_the_project_has_no_registry(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        proc = self.installed("check", cwd=bare, expect=2)
        self.assertEqual(proc.stdout, "")
        self.assertIn("adopt", proc.stderr)

    def test_exit_2_on_usage_errors(self):
        self.assertIn("usage:", self.check("--no-such-option", expect=2).stderr)
        self.assertIn("usage:", self.check("--format", "sarif", expect=2).stderr)
        self.check("--only", "no-such-id", expect=2)

    def test_exit_2_not_a_traceback_when_the_registry_cannot_be_read(self):
        os.remove(str(self.project / REGISTRY))
        (self.project / REGISTRY).mkdir()
        proc = self.installed("check", self.project, cwd=self.tmp, expect=2)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("encode-the-correction: error: "), proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_json_format_uses_the_same_exit_codes(self):
        self.set_registry(TIMER)
        self.write("src/a.js", ANTI_PATTERN)
        report = json.loads(self.check("--format", "json", expect=1).stdout)
        self.assertEqual((report["ok"], report["files_scanned"]), (False, 2))
        self.assertEqual(report["corrections"][0]["findings"],
                         [{"path": "src/a.js", "line": 2, "text": "setTimeout(() => render(), 0);"}])
        self.write("src/a.js", FIXED)
        self.assertTrue(json.loads(self.check("--format", "json", expect=0).stdout)["ok"])
        self.write(REGISTRY, "{not json")
        self.assertEqual(self.check("--format", "json", expect=2).stdout, "")


class OutputTests(LintCase):
    def test_the_report_is_exactly_this_and_identical_on_every_run(self):
        self.seed_three_violations()
        first = self.check(expect=1)
        self.assertEqual(first.stdout, GOLDEN)
        self.assertEqual(first.stderr, "")
        self.assertEqual(self.check(expect=1).stdout, GOLDEN)

    def test_every_finding_is_a_path_line_record_relative_to_the_project(self):
        self.seed_three_violations()
        out = self.check(expect=1).stdout
        findings = [line for line in out.splitlines()
                    if line.startswith("  ") and not line.startswith(("  Problem:", "  Root-cause fix:"))]
        self.assertEqual(len(findings), 5)
        for line in findings:
            self.assertRegex(line, r"^  [^\s:/][^\s:]*(:[1-9][0-9]*)?: \S")
        self.assertEqual(len([line for line in findings if re.match(r"^  [^:]+:\d+: ", line)]), 3)
        # Nothing machine-specific: no absolute paths, no colours, no carriage returns.
        self.assertNotIn(str(self.tmp), out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\r", out)

    def test_findings_follow_registry_order_then_path_then_line(self):
        self.seed_three_violations()
        self.set_registry(FAIL_FAST, TIMER)
        report = json.loads(self.check("--format", "json", expect=1).stdout)
        self.assertEqual([c["id"] for c in report["corrections"]], ["shell-scripts-fail-fast", "no-timer-quick-fixes"])
        self.assertEqual([(f["path"], f["line"]) for f in report["corrections"][1]["findings"]],
                         [("src/a.js", 2), ("src/a.js", 5), ("src/ui/b.js", 2)])

    def test_max_findings_truncates_text_output_but_never_the_exit_code(self):
        self.seed_three_violations()
        proc = self.check("--max-findings", "1", expect=1)
        self.assertIn("  src/a.js:2: ", proc.stdout)
        self.assertIn("  ... and 2 more\n", proc.stdout)
        self.assertNotIn("src/ui/b.js", proc.stdout)

    def test_output_the_terminal_cannot_encode_is_escaped_not_a_crash(self):
        self.set_registry(TIMER)
        self.write("src/a.js", "setTimeout(() => say('caf\u00e9 \u2603'), 0);\n")
        proc = self.check(expect=1, env={"PYTHONIOENCODING": "ascii"})
        self.assertIn(r"  src/a.js:1: setTimeout(() => say('caf\xe9 \u2603'), 0);", proc.stdout)
        self.assertEqual(proc.stderr, "")

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_a_file_name_that_is_not_valid_utf8_is_reported_not_a_crash(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, env=self.env)
        self.set_registry(dict(TIMER, include=["**/*.js"]))
        (self.project / "src").mkdir()
        try:
            with open(os.path.join(os.fsencode(str(self.project / "src")), b"bad\xff.js"), "w") as handle:
                handle.write(ANTI_PATTERN)
        except OSError:
            self.skipTest("this filesystem does not allow file names that are not UTF-8")
        proc = self.check(expect=1, env={"PYTHONIOENCODING": "utf-8"})
        self.assertIn("  src/bad\\udcff.js:2: setTimeout(", proc.stdout)
        self.assertEqual(proc.stderr, "")


class DiscoveryTests(LintCase):
    def setUp(self):
        super().setUp()
        self.set_registry(TIMER)
        self.write("src/ui/deep/a.js", ANTI_PATTERN)

    def test_check_finds_the_registry_from_the_project_root(self):
        self.assertIn("  src/ui/deep/a.js:2: ", self.check(expect=1).stdout)

    def test_check_finds_the_registry_from_any_subdirectory_and_reports_the_same(self):
        from_root = self.check(expect=1).stdout
        from_below = self.check(cwd=self.project / "src" / "ui" / "deep", expect=1).stdout
        self.assertEqual(from_below, from_root, "paths stay relative to the project root")

    def test_installed_and_vendored_and_source_cli_report_the_same(self):
        installed = self.check(expect=1).stdout
        self.assertEqual(self.vendored("check", expect=1, cwd=self.tmp).stdout, installed)
        self.assertEqual(self.cli("check", self.project, expect=1).stdout, installed)

    def test_encode_and_list_find_the_project_the_same_way(self):
        below = self.project / "src" / "ui"
        self.installed("encode", "--id", "no-todo", "--forbid", "TODO", "--message", "Unowned TODO.",
                       "--fix", "Do it now or file an issue and link it.", cwd=below)
        self.assertEqual([c["id"] for c in self.read_json(REGISTRY)["corrections"]], ["no-timer-quick-fixes", "no-todo"])
        self.assertFalse((below / TOOL_DIR).exists())
        self.assertIn("no-todo [forbid-pattern]", self.installed("list", cwd=below).stdout)

    def test_an_explicit_project_argument_is_never_searched_upwards(self):
        proc = self.installed("check", self.project / "src", cwd=self.project, expect=2)
        self.assertIn("No registry", proc.stderr)

    def test_the_search_stops_at_a_repository_root(self):
        nested = self.project / "vendor" / "other-repo"
        (nested / ".git").mkdir(parents=True)
        (nested / "pkg").mkdir()
        proc = self.installed("check", cwd=nested / "pkg", expect=2)
        self.assertIn(str(nested / "pkg"), proc.stderr, "it must not fall back to the outer project's registry")

    def test_the_nearest_registry_wins(self):
        inner = self.project / "packages" / "inner"
        inner.mkdir(parents=True)
        self.installed("adopt", cwd=inner)
        self.assertIn("no corrections are encoded yet", self.installed("check", cwd=inner, expect=0).stdout)
        self.check(expect=1)

    def test_install_commands_do_not_search_they_act_on_the_cwd(self):
        before = self.snapshot()
        below = self.project / "src"
        self.assertIn("nothing from this tool found", self.installed("uninstall", cwd=below).stdout)
        self.assertEqual(self.snapshot(), before)
        dry = self.installed("adopt", "--dry-run", cwd=below).stdout
        self.assertIn("adopt: %s (dry run" % below, dry)
        self.assertIn("would create", dry)
        self.assertEqual(self.snapshot(), before)


class HintTests(LintCase):
    """Messages name a command that exists in the project being checked."""

    def test_a_project_with_the_vendored_copy_is_pointed_at_it(self):
        self.assertIn("python3 .encode-the-correction/cli.py encode --help", self.check().stdout)
        self.assertIn("python3 .encode-the-correction/cli.py encode --help", self.installed("list", cwd=self.project).stdout)

    def test_a_registry_only_project_is_pointed_at_the_installed_command(self):
        os.remove(str(self.project / VENDORED))
        for command in ("check", "list"):
            out = self.installed(command, cwd=self.project).stdout
            self.assertIn(CONSOLE + " encode --help", out)
            self.assertNotIn("cli.py", out)
        out = self.installed("encode", "--id", "no-todo", "--forbid", "TODO", "--message", "m", "--fix", "f",
                             cwd=self.project).stdout
        self.assertIn("Verify with `%s check`." % CONSOLE, out)


if __name__ == "__main__":
    unittest.main()
