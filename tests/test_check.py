"""check and encode: a correction becomes an enforceable, root-cause-oriented check."""
import json
import shutil
import subprocess
import unittest

from support import REGISTRY, TOOL_DIR, ProjectCase

ANTI_PATTERN = "export function refresh() {\n  setTimeout(() => render(), 0);\n}\n"
FIXED = "export function refresh() {\n  store.invalidate();\n}\n"


class CheckTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir()
        self.cli("adopt", self.project)

    def test_check_requires_an_adopted_project(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        proc = self.cli("check", bare, expect=2)
        self.assertIn("adopt", proc.stderr)

    def test_an_empty_registry_passes_and_says_what_to_do_next(self):
        self.assertIn("encode --help", self.vendored("check").stdout)

    def test_encoding_a_correction_makes_it_enforceable_on_the_next_check(self):
        self.write("src/ui/refresh.js", ANTI_PATTERN)
        self.vendored("check", expect=0)  # the mistake is in the code, but nothing enforces it yet

        encoded = self.encode_timer_check("--why", "Agent patched a render race with setTimeout(0).").stdout
        self.assertIn("fails right now at 1 place", encoded)

        failed = self.vendored("check", expect=1).stdout
        self.assertIn("FAIL no-timer-quick-fixes", failed)
        self.assertIn("src/ui/refresh.js:2: setTimeout(() => render(), 0);", failed)
        self.assertIn("Problem: Imperative timers patch over a state-flow problem.", failed)
        self.assertIn("Root-cause fix: Drive the update from state and delete the timer.", failed)
        self.assertIn("Why this check exists: Agent patched a render race", failed)
        self.assertIn("Fix the root cause", failed)

        self.write("src/ui/refresh.js", FIXED)
        self.assertIn("OK - all 1 encoded corrections pass", self.vendored("check", expect=0).stdout)

    def test_check_fails_on_a_seeded_registry_and_passes_after_the_fix(self):
        self.write(REGISTRY, json.dumps({"version": 1, "corrections": [{
            "id": "renderer-must-not-import-main",
            "type": "forbid-pattern",
            "pattern": r"from ['\"].*electron-main/",
            "include": ["electron-renderer/**"],
            "message": "Renderer code imports main-process code, which blocks the UI thread.",
            "fix": "Move the call behind an IPC channel; never import electron-main from the renderer.",
        }]}))
        self.write("electron-main/db.ts", "export const db = 1;\n")
        self.write("electron-renderer/view.ts", "import { db } from '../electron-main/db';\n")
        failed = self.cli("check", self.project, expect=1).stdout
        self.assertIn("electron-renderer/view.ts:1", failed)
        self.assertIn("IPC channel", failed)
        self.write("electron-renderer/view.ts", "import { query } from './ipc';\n")
        self.cli("check", self.project, expect=0)

    def test_source_cli_and_vendored_cli_run_the_same_check(self):
        self.write("src/a.js", ANTI_PATTERN)
        self.encode_timer_check()
        from_source = self.cli("check", self.project, "--format", "json", expect=1).stdout
        from_vendored = self.vendored("check", "--format", "json", expect=1, cwd=self.tmp).stdout
        self.assertEqual(json.loads(from_source), json.loads(from_vendored))
        report = json.loads(from_source)
        self.assertFalse(report["ok"])
        self.assertEqual(report["corrections"][0]["findings"],
                         [{"path": "src/a.js", "line": 2, "text": "setTimeout(() => render(), 0);"}])

    def test_globs_scope_the_check_and_exclude_is_the_only_exception_mechanism(self):
        self.write("src/a.js", ANTI_PATTERN)
        self.write("src/framework/scheduler.js", ANTI_PATTERN)
        self.write("scripts/tool.js", ANTI_PATTERN)
        self.write("src/notes.md", "setTimeout( in prose\n")
        self.encode_timer_check("--exclude", "src/framework/**")
        out = self.vendored("check", expect=1).stdout
        self.assertIn("src/a.js:2", out)
        self.assertNotIn("scheduler.js", out)
        self.assertNotIn("scripts/tool.js", out)
        self.assertNotIn("notes.md", out)
        # An inline comment is not an escape hatch.
        self.write("src/a.js", "// etc-ignore: justified, trust me\n" + ANTI_PATTERN)
        self.vendored("check", expect=1)

    def test_require_pattern(self):
        self.write("scripts/deploy.sh", "#!/bin/sh\necho deploy\n")
        self.write("scripts/build.sh", "#!/bin/sh\nset -eu\necho build\n")
        self.vendored("encode", "--id", "shell-scripts-fail-fast", "--require", r"^set -eu", "--glob", "scripts/*.sh",
                      "--message", "Shell script keeps going after a failed command.",
                      "--fix", "Add `set -eu` right after the shebang.")
        out = self.vendored("check", expect=1).stdout
        self.assertIn("scripts/deploy.sh: missing required pattern", out)
        self.assertNotIn("build.sh", out)
        self.write("scripts/deploy.sh", "#!/bin/sh\nset -eu\necho deploy\n")
        self.vendored("check", expect=0)

    def test_require_pattern_needs_a_glob(self):
        proc = self.vendored("encode", "--id", "x", "--require", "foo", "--message", "m", "--fix", "f", expect=2)
        self.assertIn("--glob", proc.stderr)

    def test_forbid_path(self):
        self.write("src/utils/helpers.js", "export {};\n")
        self.vendored("encode", "--id", "no-utils-dumping-ground", "--forbid-path", "src/utils/**",
                      "--message", "src/utils is a dumping ground with no owner.",
                      "--fix", "Put the function in the module that owns the concept.")
        out = self.vendored("check", expect=1).stdout
        self.assertIn("src/utils/helpers.js: this path must not exist", out)
        shutil.rmtree(str(self.project / "src" / "utils"))
        self.vendored("check", expect=0)

    def test_ignore_case_and_only(self):
        self.write("a.txt", "TODO later\n")
        self.write("src/a.js", ANTI_PATTERN)
        self.encode_timer_check()
        self.vendored("encode", "--id", "no-todo", "--forbid", "todo", "--ignore-case", "--glob", "*.txt",
                      "--message", "Unowned TODO.", "--fix", "Do it now or file an issue and link it.")
        out = self.vendored("check", "--only", "no-todo", expect=1).stdout
        self.assertIn("a.txt:1", out)
        self.assertNotIn("no-timer-quick-fixes", out)
        self.vendored("check", "--only", "nope", expect=2)

    def test_encode_rejects_bad_input_without_touching_the_registry(self):
        before = self.read(REGISTRY)
        common = ["--message", "m", "--fix", "f"]
        self.assertIn("regular expression",
                      self.vendored("encode", "--id", "bad-regex", "--forbid", "(unclosed", *common, expect=2).stderr)
        self.assertIn("kebab-case",
                      self.vendored("encode", "--id", "Bad Id", "--forbid", "x", *common, expect=2).stderr)
        self.assertIn("root cause",
                      self.vendored("encode", "--id", "no-fix", "--forbid", "x", "--message", "m", "--fix", " ", expect=2).stderr)
        self.vendored("encode", "--id", "no-message", "--forbid", "x", "--fix", "f", expect=2)
        self.assertEqual(self.read(REGISTRY), before)

    def test_encode_warns_when_the_new_check_matches_nothing(self):
        self.assertIn("matches nothing right now", self.encode_timer_check().stdout)

    def test_a_broken_registry_is_a_configuration_error_not_a_pass(self):
        self.write(REGISTRY, "{not json")
        self.assertIn("not valid JSON", self.vendored("check", expect=2).stderr)
        self.write(REGISTRY, json.dumps({"version": 1, "corrections": [
            {"id": "broken", "type": "forbid-pattern", "pattern": "(", "message": "m", "fix": "f"}]}))
        self.assertIn("correction 'broken'", self.vendored("check", expect=2).stderr)
        self.write(REGISTRY, json.dumps({"version": 1, "corrections": [
            {"id": "typo", "type": "forbid-patern", "pattern": "x", "message": "m", "fix": "f"}]}))
        self.assertIn("\"type\" must be one of", self.vendored("check", expect=2).stderr)

    def test_tool_directory_binary_files_and_symlinks_are_not_scanned(self):
        self.encode_timer_check("--glob", "**")
        self.write(TOOL_DIR + "/scratch.js", ANTI_PATTERN)
        self.write("node_modules/dep/index.js", ANTI_PATTERN)
        self.write("assets/blob.bin", "\0\0setTimeout(\n")
        outside = self.tmp / "outside.js"
        outside.write_text(ANTI_PATTERN)
        (self.project / "src").mkdir()
        (self.project / "src" / "link.js").symlink_to(outside)
        self.vendored("check", expect=0)
        self.write("src/real.js", ANTI_PATTERN)  # control: the same content in a normal file does fail
        self.vendored("check", expect=1)

    def test_list_shows_the_encoded_corrections(self):
        self.assertIn("no corrections encoded yet", self.vendored("list").stdout)
        self.encode_timer_check()
        out = self.vendored("list").stdout
        self.assertIn("no-timer-quick-fixes [forbid-pattern]", out)
        self.assertIn("src/**/*.js", out)

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_git_projects_respect_gitignore(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True, env=self.env)
        self.write(".gitignore", "dist/\n")
        self.write("dist/bundle.js", ANTI_PATTERN)
        self.write("src/tracked.js", ANTI_PATTERN)
        self.write(TOOL_DIR + "/scratch.js", ANTI_PATTERN)
        self.write("node_modules/dep/index.js", ANTI_PATTERN)
        self.encode_timer_check("--glob", "**/*.js")
        report = json.loads(self.vendored("check", "--format", "json", expect=1).stdout)
        self.assertEqual([f["path"] for f in report["corrections"][0]["findings"]], ["src/tracked.js"])


class GlobTests(unittest.TestCase):
    def test_glob_semantics(self):
        from encode_the_correction import glob_to_regex
        cases = [
            ("*.py", "a.py", True), ("*.py", "pkg/deep/a.py", True), ("*.py", "a.pyc", False),
            ("src/*.js", "src/a.js", True), ("src/*.js", "src/x/a.js", False),
            ("src/**/*.js", "src/a.js", True), ("src/**/*.js", "src/x/y/a.js", True),
            ("src/**/*.js", "lib/a.js", False), ("src/**", "src/x/y.txt", True), ("src/**", "srcx/y.txt", False),
            ("**", "anything/at/all", True), ("docs/", "docs/a/b.md", True), ("docs/", "x/docs/b.md", True),
            ("**/test_*.py", "test_a.py", True), ("**/test_*.py", "t/test_a.py", True),
            ("file?.txt", "file1.txt", True), ("file?.txt", "file/.txt", False),
            ("[ab].txt", "a.txt", True), ("[!ab].txt", "a.txt", False), ("[!ab].txt", "c.txt", True),
            ("./src/a.b", "src/a.b", True), ("src/a.b", "src/aXb", False),
        ]
        for glob, path, expected in cases:
            with self.subTest(glob=glob, path=path):
                self.assertEqual(bool(glob_to_regex(glob).match(path)), expected)


if __name__ == "__main__":
    unittest.main()
