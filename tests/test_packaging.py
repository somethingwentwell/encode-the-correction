"""Packaging: one stdlib-only module, one console script, and the same behaviour once installed.

Three layers, cheapest first:
- the declared metadata in pyproject.toml;
- the module run the way an installed console script runs it, with no install;
- a real `pip install` of this repo into a throwaway virtual environment, and the
  generic CI script that does the same. These two need pip to fetch the build
  backend (setuptools); set ETC_SKIP_INSTALL_TESTS=1 to skip them when offline.
"""
import importlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import miniyaml
from support import (CLI, CONSOLE, REGISTRY, REPO, TOOL_DIR, VENDORED, InstalledVenv, ProjectCase, copy_repo,
                     install_tests_skip_reason, pip_env, skill_tree)

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

ANTI_PATTERN = "export function refresh() {\n  setTimeout(() => render(), 0);\n}\n"
FIXED = "export function refresh() {\n  store.invalidate();\n}\n"
USER_AGENTS = "# Agent notes\n\nUse tabs. Run `make test` before committing.\n"
SCRIPT_TARGET = "encode_the_correction:main"


def source_version():
    import encode_the_correction
    return encode_the_correction.VERSION


@unittest.skipUnless(tomllib, "reading pyproject.toml needs tomllib (Python 3.11+)")
class MetadataTests(unittest.TestCase):
    def setUp(self):
        with open(str(REPO / "pyproject.toml"), "rb") as handle:
            self.pyproject = tomllib.load(handle)
        self.project = self.pyproject["project"]

    def test_the_only_console_script_is_the_documented_one(self):
        self.assertEqual(self.project["scripts"], {CONSOLE: SCRIPT_TARGET})
        self.assertNotIn("gui-scripts", self.project)

    def test_the_console_script_target_is_the_cli_entry_point(self):
        module_name, _, attribute = self.project["scripts"][CONSOLE].partition(":")
        module = importlib.import_module(module_name)
        self.assertEqual(Path(module.__file__).resolve(), CLI)
        self.assertTrue(callable(getattr(module, attribute)))

    def test_there_are_no_runtime_dependencies(self):
        self.assertEqual(self.project["dependencies"], [])
        self.assertNotIn("optional-dependencies", self.project)
        self.assertNotIn("dependencies", self.project.get("dynamic", []))

    def test_setuptools_is_the_build_backend_and_only_that(self):
        build = self.pyproject["build-system"]
        self.assertEqual(build["build-backend"], "setuptools.build_meta")
        self.assertEqual(len(build["requires"]), 1)
        self.assertTrue(build["requires"][0].startswith("setuptools"))

    def test_exactly_the_cli_module_is_packaged(self):
        self.assertEqual(self.pyproject["tool"]["setuptools"]["py-modules"], [CLI.stem])
        self.assertNotIn("packages", self.pyproject["tool"]["setuptools"])

    def test_the_version_has_one_source_and_python_3_8_is_supported(self):
        self.assertEqual(self.project["dynamic"], ["version"])
        self.assertEqual(self.pyproject["tool"]["setuptools"]["dynamic"]["version"], {"attr": CLI.stem + ".VERSION"})
        self.assertRegex(source_version(), r"^\d+\.\d+\.\d+$")
        self.assertEqual(self.project["requires-python"], ">=3.8")
        self.assertEqual(self.project["name"], CONSOLE)


class InstalledLayoutTests(ProjectCase):
    """The module imported from outside the repo and outside any project, entered through `main`."""

    def setUp(self):
        super().setUp()
        self.project.mkdir()

    def test_module_invocation_works_from_any_directory(self):
        self.installed("--version")  # creates the site-packages-like directory
        env = {"PYTHONPATH": str(self.tmp / "site-packages")}
        proc = self.run_argv([sys.executable, "-m", CLI.stem, "--version"], cwd=self.project, env=env)
        self.assertEqual(proc.stdout.strip(), "%s %s" % (CONSOLE, source_version()))
        self.run_argv([sys.executable, "-m", CLI.stem, "check"], cwd=self.project, env=env, expect=2)

    def test_adopt_vendors_a_byte_identical_copy_of_the_module(self):
        self.installed("adopt", cwd=self.project)
        self.assertEqual((self.project / VENDORED).read_bytes(), CLI.read_bytes())
        self.assertEqual(self.read_json(REGISTRY), {"version": 1, "corrections": []})
        self.assertIn("python3 .encode-the-correction/cli.py check", self.read("AGENTS.md"))

    def test_adopt_is_idempotent_across_installed_source_and_vendored_entry_points(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.write("CLAUDE.md", "# Claude notes\n")
        self.write(".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(ls)"]}}, indent=2) + "\n")
        self.installed("adopt", "--hooks", "all", cwd=self.project)
        first = self.snapshot()
        self.assertTrue(self.read("AGENTS.md").startswith(USER_AGENTS))
        out = self.installed("adopt", "--hooks", "all", cwd=self.project).stdout
        self.assertNotIn("created", out)
        self.assertNotIn("merged", out)
        self.assertNotIn("refreshed", out)
        self.cli("adopt", self.project)
        self.vendored("adopt")
        self.assertEqual(self.snapshot(), first)

    def test_uninstall_restores_a_preexisting_project_byte_for_byte(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.write("src/app.py", "print('hi')\n")
        self.write(".cursor/hooks.json", json.dumps(
            {"version": 1, "hooks": {"stop": [{"command": "echo bye"}]}}, indent=2) + "\n")
        before = self.snapshot()
        self.installed("adopt", "--hooks", "all", "--claude-md", "always", cwd=self.project)
        self.assertNotEqual(self.snapshot(), before)
        self.installed("uninstall", cwd=self.project)
        self.assertEqual(self.snapshot(), before)

    def test_init_creates_a_new_project_and_refuses_an_existing_one(self):
        self.installed("init", "fresh", cwd=self.project)
        self.assertEqual(sorted(self.snapshot(self.project / "fresh")),
                         sorted([TOOL_DIR, VENDORED, REGISTRY, TOOL_DIR + "/manifest.json", "AGENTS.md"] + skill_tree()))
        before = self.snapshot()
        self.installed("init", "fresh", cwd=self.project, expect=2)
        self.assertEqual(self.snapshot(), before)

    def test_session_hooks_installed_by_the_installed_cli_still_never_fail(self):
        self.installed("adopt", "--hooks", "claude-code", cwd=self.project)
        self.write(REGISTRY, "{not json")
        self.installed("check", cwd=self.project, expect=2)
        command = self.read_json(".claude/settings.json")["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        proc = self.shell(command, cwd=self.tmp, env={"CLAUDE_PROJECT_DIR": str(self.project)}, expect=0)
        self.assertIn("could not run the checks", proc.stdout)


class PipInstallTests(ProjectCase):
    """`pip install <this repo>` into a fresh virtual environment, then the real console script."""

    @classmethod
    def setUpClass(cls):
        reason = install_tests_skip_reason()
        if reason:
            raise unittest.SkipTest(reason)
        holder = tempfile.TemporaryDirectory(prefix="etc-install-")
        cls.addClassCleanup(holder.cleanup)
        cls.venv = InstalledVenv(Path(holder.name).resolve())

    def setUp(self):
        super().setUp()
        self.project.mkdir()

    def console(self, *args, **kwargs):
        kwargs.setdefault("cwd", self.project)
        return self.run_argv([self.venv.console] + list(args), **kwargs)

    def test_the_console_script_is_on_the_path_and_reports_the_source_version(self):
        self.assertTrue(self.venv.console.is_file())
        self.assertEqual(self.console("--version").stdout.strip(), "%s %s" % (CONSOLE, source_version()))

    def test_the_distribution_ships_one_module_one_script_and_no_dependencies(self):
        probe = (
            "import importlib.metadata as m, json\n"
            "d = m.distribution(%r)\n"
            "print(json.dumps({'version': d.version, 'requires': d.requires or [],\n"
            "    'python': d.metadata['Requires-Python'],\n"
            "    'scripts': {e.name: e.value for e in d.entry_points if e.group == 'console_scripts'},\n"
            "    'modules': sorted(str(f) for f in d.files if str(f).endswith('.py'))}))\n" % CONSOLE
        )
        facts = json.loads(self.run_argv([self.venv.python, "-c", probe]).stdout)
        self.assertEqual(facts, {
            "version": source_version(), "requires": [], "python": ">=3.8",
            "scripts": {CONSOLE: SCRIPT_TARGET}, "modules": [CLI.name],
        })

    def test_check_from_a_project_root_exits_0_1_and_2(self):
        self.assertIn("adopt", self.console("check", expect=2).stderr)  # not adopted yet
        self.console("adopt")
        self.assertEqual((self.project / VENDORED).read_bytes(), CLI.read_bytes())
        self.console("check", expect=0)
        self.write("src/a.js", ANTI_PATTERN)
        self.console("encode", "--id", "no-timer-quick-fixes", "--forbid", r"\bsetTimeout\(", "--glob", "src/**/*.js",
                     "--message", "Imperative timers patch over a state-flow problem.",
                     "--fix", "Drive the update from state and delete the timer.")
        failed = self.console("check", expect=1)
        self.assertIn("  src/a.js:2: setTimeout(() => render(), 0);\n", failed.stdout)
        self.assertEqual(failed.stderr, "")
        self.assertEqual(self.console("check", cwd=self.project / "src", expect=1).stdout, failed.stdout)
        self.assertEqual(self.vendored("check", expect=1).stdout, failed.stdout)
        self.assertEqual(self.run_argv([self.venv.python, "-m", CLI.stem, "check"], cwd=self.project, expect=1).stdout,
                         failed.stdout)
        self.write("src/a.js", FIXED)
        self.console("check", expect=0)
        self.write(REGISTRY, "{not json")
        self.assertEqual(self.console("check", expect=2).stdout, "")

    def test_adopt_and_uninstall_from_the_console_script_preserve_the_project(self):
        self.write("AGENTS.md", USER_AGENTS)
        self.write(".claude/settings.json", json.dumps({"permissions": {"allow": ["Bash(ls)"]}}, indent=2) + "\n")
        before = self.snapshot()
        self.console("adopt", "--hooks", "all")
        adopted = self.snapshot()
        self.console("adopt", "--hooks", "all")
        self.cli("adopt", self.project)
        self.assertEqual(self.snapshot(), adopted)
        self.console("uninstall")
        self.assertEqual(self.snapshot(), before)

    def test_the_pre_commit_hook_entry_runs_in_the_environment_pre_commit_would_build(self):
        # pre-commit pip-installs the hook repo into a virtualenv and runs `entry` from the repository root.
        (hook,) = miniyaml.load((REPO / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))
        self.assertEqual(hook["language"], "python")
        argv = shlex.split(hook["entry"])
        resolved = shutil.which(argv[0], path=str(self.venv.bin))
        self.assertIsNotNone(resolved, "the hook entry must be a script this package installs")
        self.console("adopt")
        self.run_argv([resolved] + argv[1:], cwd=self.project, expect=0)
        self.write(REGISTRY, json.dumps({"version": 1, "corrections": [{
            "id": "no-timers", "type": "forbid-pattern", "pattern": "setTimeout", "message": "m", "fix": "f"}]}))
        self.write("src/a.js", ANTI_PATTERN)
        self.assertIn("src/a.js:2", self.run_argv([resolved] + argv[1:], cwd=self.project, expect=1).stdout)


@unittest.skipUnless(os.name == "posix" and shutil.which("sh"), "needs a POSIX shell")
class GenericCiScriptTests(ProjectCase):
    """examples/ci.sh, run for real: install the CLI, run the check, fail the job on a violation."""

    def setUp(self):
        super().setUp()
        reason = install_tests_skip_reason()
        if reason:
            self.skipTest(reason)
        self.project.mkdir()
        self.cli("adopt", self.project)
        self.write(REGISTRY, json.dumps({"version": 1, "corrections": [{
            "id": "no-timers", "type": "forbid-pattern", "pattern": "setTimeout", "include": ["src/**"],
            "message": "m", "fix": "f"}]}))
        self.write("src/a.js", FIXED)
        # `python3` on the job's PATH is the interpreter running these tests.
        shims = self.tmp / "bin"
        shims.mkdir()
        os.symlink(sys.executable, str(shims / "python3"))
        (self.tmp / "ci-tmp").mkdir()
        self.ci_env = pip_env(
            self.tmp, ETC_SOURCE=str(copy_repo(self.tmp / "source")), TMPDIR=str(self.tmp / "ci-tmp"),
            PATH=str(shims) + os.pathsep + os.environ.get("PATH", ""))

    def ci(self, expect):
        proc = subprocess.run(["sh", str(REPO / "examples" / "ci.sh")], cwd=str(self.project), env=self.ci_env,
                              capture_output=True, text=True, timeout=900)
        self.assertEqual(proc.returncode, expect, "stdout=%s\nstderr=%s" % (proc.stdout, proc.stderr))
        return proc

    def test_the_job_passes_on_a_clean_project_and_fails_on_a_violation(self):
        before = self.snapshot()
        self.assertIn("OK - all 1 encoded corrections pass (2 files scanned)", self.ci(expect=0).stdout)
        self.assertEqual(self.snapshot(), before, "the job must not leave an environment inside the checkout")
        self.write("src/a.js", ANTI_PATTERN)
        self.assertIn("  src/a.js:2: setTimeout(() => render(), 0);\n", self.ci(expect=1).stdout)

    def test_the_job_fails_with_2_when_the_project_has_no_registry(self):
        shutil.rmtree(str(self.project / TOOL_DIR))
        self.assertIn("No registry", self.ci(expect=2).stderr)


if __name__ == "__main__":
    unittest.main()
