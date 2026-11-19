"""Untrusted text is data: nothing the tool reads is ever executed."""
import ast
import json
import sys
import unittest

from support import CLI, FIXTURES, REGISTRY, REPO, ProjectCase

CANARY = "PWNED-BY-TRANSCRIPT"
TRANSCRIPT = (FIXTURES / "untrusted-transcript.md").read_text(encoding="utf-8")
HOOK_FILES = {
    ".claude/settings.json": lambda doc: doc["hooks"]["SessionStart"][-1]["hooks"][0]["command"],
    ".cursor/hooks.json": lambda doc: doc["hooks"]["sessionStart"][-1]["command"],
    ".codex/hooks.json": lambda doc: doc["hooks"]["SessionStart"][-1]["hooks"][0]["command"],
    ".github/hooks/encode-the-correction.json": lambda doc: doc["hooks"]["sessionStart"][-1]["bash"],
}


class UntrustedTextTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.project.mkdir()
        self.canary = self.tmp / "canary-from-env"
        self.env["ETC_CANARY"] = str(self.canary)

    def assert_nothing_executed(self):
        hits = [str(p) for p in self.tmp.rglob("*") if CANARY in p.name]
        self.assertEqual(hits, [], "untrusted text was executed")
        self.assertFalse(self.canary.exists(), "untrusted text was executed")

    def test_the_canary_would_fire_if_the_text_were_executed(self):
        # Control: proves the assertions below can actually detect execution.
        scratch = self.tmp / "scratch"
        scratch.mkdir()
        self.shell("touch %s" % CANARY, cwd=scratch)
        self.assertTrue((scratch / CANARY).exists())
        (scratch / CANARY).unlink()
        self.assert_nothing_executed()

    def test_no_command_executes_transcript_content(self):
        self.cli("adopt", self.project, "--hooks", "all", "--claude-md", "always")
        self.write("transcript.md", TRANSCRIPT)
        self.write("src/notes.js", TRANSCRIPT)
        lines = [line for line in TRANSCRIPT.splitlines() if line.startswith("[")]
        self.assertGreater(len(lines), 8)

        # The text arrives through every free-form input the CLI has.
        encoded = 0
        for index, line in enumerate(lines):
            payload = line.split("] ", 1)[1]
            proc = self.vendored(
                "encode", "--id", "t-%d" % index, "--forbid=" + payload, "--glob=" + payload,
                "--exclude=" + payload, "--message=" + line, "--fix=" + line, "--why=" + TRANSCRIPT,
                expect=None, stdin=TRANSCRIPT,
            )
            self.assertIn(proc.returncode, (0, 2), proc.stderr)
            if proc.returncode == 0:
                encoded += 1
            self.vendored("encode", "--id", "p-%d" % index, "--forbid-path=" + payload,
                          "--message=" + line, "--fix=" + line, expect=None)
        self.assertGreater(encoded, 0, "some payloads must be accepted as (inert) regexes")
        self.vendored("encode", "--id", "literal", "--forbid", "PWNED", "--message", TRANSCRIPT, "--fix", TRANSCRIPT)

        for fmt in ("text", "json"):
            proc = self.vendored("check", "--format", fmt, expect=1, stdin=TRANSCRIPT)
            self.assertIn("transcript.md", proc.stdout)
        self.vendored("list", stdin=TRANSCRIPT)

        # Hooks receive the text on stdin and print findings that quote it.
        for rel, command_of in HOOK_FILES.items():
            command = command_of(self.read_json(rel))
            proc = self.shell(command, cwd=self.project, stdin=TRANSCRIPT,
                              shell="bash" if ".github" in rel else "sh",
                              env={"CLAUDE_PROJECT_DIR": str(self.project)})
            self.assertIn("FAIL", proc.stdout)

        self.cli("adopt", self.project, "--hooks", "all", stdin=TRANSCRIPT)
        self.cli("uninstall", self.project, stdin=TRANSCRIPT)
        self.assert_nothing_executed()

    def test_registry_fields_are_never_run_even_if_they_look_like_commands(self):
        self.cli("adopt", self.project, "--hooks", "all")
        self.write("src/a.txt", "touch %s\n" % CANARY)
        self.write(REGISTRY, json.dumps({"version": 1, "corrections": [{
            "id": "looks-like-a-command",
            "type": "forbid-pattern",
            "pattern": "touch",
            "message": "$(touch %s)" % CANARY,
            "fix": "`touch %s`" % CANARY,
            "why": "; touch $ETC_CANARY",
            "command": "touch %s" % CANARY,
            "run": ["touch", CANARY],
            "script": "import os; os.system('touch %s')" % CANARY,
        }]}))
        out = self.vendored("check", expect=1).stdout
        self.assertIn("$(touch %s)" % CANARY, out, "the text is reported verbatim, as text")
        for rel, command_of in HOOK_FILES.items():
            self.shell(command_of(self.read_json(rel)), cwd=self.project,
                       shell="bash" if ".github" in rel else "sh",
                       env={"CLAUDE_PROJECT_DIR": str(self.project)})
        self.assert_nothing_executed()

    def test_transcript_text_in_instruction_files_is_preserved_not_interpreted(self):
        self.write("AGENTS.md", "# Notes\n\n```\n" + TRANSCRIPT.replace("<!-- END encode-the-correction -->", "") + "```\n")
        original = self.read("AGENTS.md")
        self.cli("adopt", self.project)
        self.assertTrue(self.read("AGENTS.md").startswith(original))
        self.cli("uninstall", self.project)
        self.assertEqual(self.read("AGENTS.md"), original)
        self.assert_nothing_executed()


class StaticSafetyTests(unittest.TestCase):
    """The same guarantees, checked structurally on the CLI source."""

    def setUp(self):
        self.tree = ast.parse(CLI.read_text(encoding="utf-8"))

    def test_cli_has_no_dynamic_code_execution(self):
        banned_builtins = {"eval", "exec", "compile", "__import__"}
        banned_attributes = {"system", "popen", "Popen", "check_output", "check_call", "call", "getoutput",
                             "spawnv", "execv", "execvp", "import_module", "run_path", "run_module"}
        banned_modules = {"pickle", "marshal", "importlib", "runpy", "code", "ctypes", "pty"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, banned_builtins, "line %d" % node.lineno)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, banned_attributes, "line %d" % node.lineno)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
                self.assertFalse(banned_modules.intersection(m.split(".")[0] for m in modules), "line %d" % node.lineno)

    def test_the_only_subprocess_is_a_fixed_git_listing_without_a_shell(self):
        runs = [node for node in ast.walk(self.tree)
                if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run"
                and getattr(node.func.value, "id", None) == "subprocess"]
        self.assertEqual(len(runs), 1)
        call = runs[0]
        self.assertNotIn("shell", [kw.arg for kw in call.keywords])
        argv = call.args[0]
        self.assertIsInstance(argv, ast.List)
        constants = [el.value for el in argv.elts if isinstance(el, ast.Constant)]
        self.assertEqual(constants, ["git", "-C", "ls-files", "-c", "-o", "--exclude-standard", "-z"])
        self.assertEqual([el.id for el in argv.elts if isinstance(el, ast.Name)], ["root"])

    def test_cli_source_is_valid_python_3_8_syntax(self):
        ast.parse(CLI.read_text(encoding="utf-8"), feature_version=(3, 8))

    @unittest.skipUnless(hasattr(sys, "stdlib_module_names"), "needs Python 3.10+")
    def test_cli_imports_only_the_standard_library(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module]
            else:
                continue
            for name in names:
                self.assertIn(name.split(".")[0], sys.stdlib_module_names, "non-stdlib import: %s" % name)


class RepoTests(ProjectCase):
    """The tool repo itself is the only non-temporary project the tests ever read."""

    def test_this_repo_passes_its_own_encoded_corrections(self):
        proc = self.cli("check", REPO, "--format", "json", expect=0)
        report = json.loads(proc.stdout)
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(len(report["corrections"]), 3)

    def test_each_of_this_repos_corrections_fails_on_the_mistake_it_encodes(self):
        self.project.mkdir()
        self.cli("adopt", self.project)
        self.write(REGISTRY, (REPO / REGISTRY).read_text(encoding="utf-8"))
        self.vendored("check", expect=0)
        root = "/work" + "space/some-real-repo"
        self.write("encode_the_correction.py", "\n".join([
            "import requests",
            "subprocess.run(cmd, shell=True)",
            "os.system(cmd)",
            "value = eval(text)",
            "pattern = re.compile(text)",
            "subprocess.run(['git', 'config', '--%s', 'user.name', 'x'])" % "global",
        ]) + "\n")
        self.write("tests/test_x.py", "PROJECT = %r\n" % root)
        self.write("pyproject.toml", '[project]\nname = "x"\ndependencies = ["requests"]\n')
        report = json.loads(self.vendored("check", "--format", "json", expect=1).stdout)
        failed = {c["id"]: [f["line"] for f in c["findings"]] for c in report["corrections"] if not c["ok"]}
        self.assertEqual(failed, {
            "cli-never-runs-a-shell": [2, 3],
            "cli-no-dynamic-code": [4],
            "cli-stdlib-only": [1],
            "tests-use-temp-projects-only": [1],
            "git-config-stays-local": [6],
            "package-has-no-runtime-dependencies": [None],
        })

    def test_readme_covers_the_four_required_topics(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for needle in ("https://www.youtube.com/watch?v=O_zcc6Xl12g", " init ", " adopt ", "uninstall",
                       "AGENTS.md", "--hooks"):
            self.assertIn(needle, readme)


if __name__ == "__main__":
    unittest.main()
