"""The pre-commit hook manifest, the CI workflows and the documented commands are valid and stay in step.

YAML files are parsed with `miniyaml`, a strict stdlib-only parser for the plain
subset these files are written in, then checked against the documented schema of
the tool that reads them. Command lines are parsed with the CLI's own parser, so
documentation cannot drift from the code.
"""
import contextlib
import io
import re
import shlex
import sys
import unittest

import miniyaml
from support import CONSOLE, REPO

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

URL = "https://github.com/somethingwentwell/encode-the-correction"
INSTALL = "pip install git+" + URL + ".git"
MANIFEST = ".pre-commit-hooks.yaml"
REPO_WORKFLOW = ".github/workflows/tests.yml"
EXAMPLE_WORKFLOW = "examples/github-actions.yml"
GITLAB_EXAMPLE = "examples/gitlab-ci.yml"
CI_SCRIPT = "examples/ci.sh"
DOCS = ("README.md", "docs/ci.md", "docs/adapters.md")
# Keys pre-commit documents for a hook manifest entry (https://pre-commit.com/#creating-new-hooks).
MANIFEST_KEYS = {
    "id", "name", "entry", "language", "files", "exclude", "types", "types_or", "exclude_types", "always_run",
    "fail_fast", "verbose", "pass_filenames", "require_serial", "description", "language_version",
    "minimum_pre_commit_version", "args", "stages", "additional_dependencies",
}
WORKFLOW_KEYS = {"name", "on", "permissions", "jobs", "env", "concurrency", "defaults", "run-name"}
STEP_KEYS = {"name", "id", "if", "uses", "with", "run", "env", "shell", "working-directory",
             "continue-on-error", "timeout-minutes"}


def text_of(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def load(rel):
    return miniyaml.load(text_of(rel))


def fenced(rel, language):
    """Bodies of the ```<language> blocks in a Markdown file (indented blocks are dedented)."""
    blocks = []
    for match in re.finditer(r"^([ ]*)```%s\n(.*?)^\1```$" % language, text_of(rel), flags=re.M | re.S):
        indent = len(match.group(1))
        blocks.append("".join(line[indent:] if line.strip() else "\n" for line in match.group(2).splitlines(True)))
    return blocks


def cli_arguments(command):
    """The arguments a shell command line passes to this tool, or None when it does not run the tool."""
    words = shlex.split(command, comments=True)
    for index, word in enumerate(words):
        if word == CONSOLE or word.endswith(("/cli.py", "encode_the_correction.py")):
            return words[index + 1:]
    return None


def parse(arguments):
    """Parse with the CLI's real argument parser; returns the namespace or raises AssertionError."""
    from encode_the_correction import build_parser
    errors = io.StringIO()
    with contextlib.redirect_stderr(errors):
        try:
            return build_parser().parse_args(arguments)
        except SystemExit:
            raise AssertionError("the CLI rejects %r: %s" % (arguments, errors.getvalue()))


def shell_lines(script):
    """Logical lines of a shell snippet, with backslash continuations joined."""
    return [line.strip() for line in script.replace("\\\n", " ").splitlines() if line.strip()]


class MiniYamlTests(unittest.TestCase):
    """The validator itself: it accepts the plain subset and refuses the rest."""

    def test_it_parses_the_subset(self):
        text = (
            "# comment\n"
            "name: demo  # trailing\n"
            "on:\n  push:\n    branches: [main, 'a,b']\n  pull_request:\n"
            "flags: {0}\n"
            "jobs:\n  check:\n    steps:\n      - uses: actions/checkout@v7\n        with:\n          depth: 1\n"
            "      - name: \"two: words\"\n        run: |\n          echo a # kept\n\n          echo b\n"
            "list:\n- x\n- 'it''s'\n"
        ).format("true")
        self.assertEqual(miniyaml.load(text), {
            "name": "demo",
            "on": {"push": {"branches": ["main", "a,b"]}, "pull_request": None},
            "flags": True,
            "jobs": {"check": {"steps": [
                {"uses": "actions/checkout@v7", "with": {"depth": 1}},
                {"name": "two: words", "run": "echo a # kept\n\necho b\n"},
            ]}},
            "list": ["x", "it's"],
        })

    def test_it_refuses_what_is_outside_the_subset_or_would_load_as_the_wrong_type(self):
        for bad in ("a: 3.10\n", "a: yes\n", "a: {b: 1}\n", "a: &anchor 1\n", "a: *alias\n", "a: !!str 1\n",
                    "a: 1\na: 2\n", "a:\n\t- b\n", "a: >\n  folded\n", "- a\nb: 1\n", "a: b: c\n",
                    "a:\n   - b\n  - c\n", "a: [1, [2]]\n", 'a: "open\n', "---\na: 1\n", "a:\n  b: 1\n c: 2\n"):
            with self.subTest(text=bad):
                self.assertRaises(miniyaml.YamlError, miniyaml.load, bad)


class PreCommitManifestTests(unittest.TestCase):
    def setUp(self):
        self.hooks = load(MANIFEST)

    def test_manifest_is_a_list_of_hooks_with_the_documented_keys(self):
        self.assertIsInstance(self.hooks, list)
        self.assertEqual([hook["id"] for hook in self.hooks], [CONSOLE])
        for hook in self.hooks:
            self.assertEqual(set(hook) - MANIFEST_KEYS, set(), "undocumented manifest key")
            for key in ("id", "name", "entry", "language"):  # the keys pre-commit requires
                self.assertIsInstance(hook[key], str)
                self.assertTrue(hook[key].strip())

    def test_hook_installs_this_package_and_runs_check_on_the_whole_project(self):
        (hook,) = self.hooks
        self.assertEqual(hook["language"], "python", "pre-commit must pip-install this repo to get the script")
        self.assertNotIn("additional_dependencies", hook)
        # `check` scans the project itself: it takes no file names and must not be skipped by file filters.
        self.assertIs(hook["pass_filenames"], False)
        self.assertIs(hook["always_run"], True)
        arguments = cli_arguments(hook["entry"])
        self.assertIsNotNone(arguments, "the entry must run the console script")
        args = parse(arguments + hook.get("args", []))
        self.assertEqual(args.command, "check")
        self.assertIsNone(args.project, "pre-commit runs hooks from the repository root")
        self.assertEqual(args.format, "text")

    @unittest.skipUnless(tomllib, "reading pyproject.toml needs tomllib (Python 3.11+)")
    def test_hook_entry_is_a_console_script_the_package_declares(self):
        with open(str(REPO / "pyproject.toml"), "rb") as handle:
            scripts = tomllib.load(handle)["project"]["scripts"]
        self.assertIn(shlex.split(self.hooks[0]["entry"])[0], scripts)

    def test_documented_pre_commit_configs_use_this_manifest_and_parse(self):
        configs = [miniyaml.load(block) for rel in DOCS for block in fenced(rel, "yaml")]
        remote = [repo for config in configs for repo in config["repos"] if repo["repo"] != "local"]
        local = [repo for config in configs for repo in config["repos"] if repo["repo"] == "local"]
        self.assertGreaterEqual(len(remote), 2, "README and docs/ci.md both show the snippet")
        for repo in remote:
            self.assertEqual(repo["repo"], URL)
            self.assertTrue(repo["rev"], "pre-commit requires a pinned rev")
            self.assertEqual([hook["id"] for hook in repo["hooks"]], [self.hooks[0]["id"]])
        self.assertEqual(len(local), 1)
        (hook,) = local[0]["hooks"]
        self.assertEqual(set(hook) - MANIFEST_KEYS, set())
        self.assertEqual(hook["language"], "system")
        self.assertEqual(hook["entry"], "python3 .encode-the-correction/cli.py check")
        self.assertEqual((hook["pass_filenames"], hook["always_run"]), (False, True))
        self.assertEqual(parse(cli_arguments(hook["entry"])).command, "check")


class WorkflowCase(unittest.TestCase):
    def assert_valid_workflow(self, rel):
        workflow = load(rel)
        self.assertEqual(set(workflow) - WORKFLOW_KEYS, set(), "unknown top-level workflow key")
        self.assertIsInstance(workflow["name"], str)
        self.assertEqual(workflow["permissions"], {"contents": "read"}, "a lint job needs read access only")
        self.assertIsInstance(workflow["on"], dict)
        self.assertIn("pull_request", workflow["on"])
        self.assertIn("push", workflow["on"])
        self.assertTrue(workflow["jobs"])
        for name, job in workflow["jobs"].items():
            self.assertRegex(name, r"^[A-Za-z_][A-Za-z0-9_-]*$")
            self.assertIsInstance(job["runs-on"], str)
            self.assertIsInstance(job["steps"], list)
            for step in job["steps"]:
                self.assertEqual(set(step) - STEP_KEYS, set(), "unknown step key")
                self.assertEqual(len({"uses", "run"} & set(step)), 1, "a step has exactly one of uses/run: %r" % step)
                if "uses" in step:
                    self.assertRegex(step["uses"], r"^[\w.-]+/[\w.-]+@v\d+$", "actions are pinned to a major version")
                else:
                    self.assertIsInstance(step["run"], str)
        return workflow

    def run_lines(self, job):
        return [line for step in job["steps"] if "run" in step for line in shell_lines(step["run"])]

    def uses(self, job):
        return [step["uses"].split("@")[0] for step in job["steps"] if "uses" in step]


class ExampleWorkflowTests(WorkflowCase):
    def test_example_github_workflow_installs_the_cli_then_runs_check(self):
        workflow = self.assert_valid_workflow(EXAMPLE_WORKFLOW)
        (job,) = workflow["jobs"].values()
        self.assertEqual(self.uses(job), ["actions/checkout", "actions/setup-python"])
        lines = self.run_lines(job)
        self.assertEqual(lines, ['python -m %s' % INSTALL.replace("git+", '"git+') + '"', CONSOLE + " check"])
        self.assertEqual(parse(cli_arguments(lines[-1])).command, "check")
        for step in job["steps"]:
            self.assertNotIn("continue-on-error", step, "a failing check must fail the job")
            self.assertNotIn("if", step)

    def test_example_gitlab_job_installs_the_cli_then_runs_check(self):
        document = load(GITLAB_EXAMPLE)
        (job,) = document.values()
        self.assertEqual(set(job), {"image", "script"})
        self.assertRegex(job["image"], r"^python:3\.\d+$", "a full Python image: it has git, which pip needs")
        self.assertNotIn("allow_failure", job)
        self.assertEqual(job["script"], ['python -m %s' % INSTALL.replace("git+", '"git+') + '"', CONSOLE + " check"])

    def test_generic_ci_script_installs_then_runs_check_and_returns_its_status(self):
        script = text_of(CI_SCRIPT)
        lines = [line for line in shell_lines(script) if not line.startswith("#")]
        self.assertTrue(script.startswith("#!/bin/sh\n"))
        self.assertEqual(lines[0], "set -eu", "a failed install must fail the job, not run a stale CLI")
        self.assertIn("git+%s.git" % URL, script)
        self.assertEqual(lines[-1], '"$venv/bin/%s" check "$@"' % CONSOLE, "the check is last: its status is the job's")
        self.assertNotIn("|| true", script)
        self.assertRegex(script, r"(?m)^venv=\$\(mktemp -d\)/venv$", "the environment lives outside the checkout")


class RepoWorkflowTests(WorkflowCase):
    def test_this_repo_runs_its_unit_tests_on_pushes_and_pull_requests(self):
        workflow = self.assert_valid_workflow(REPO_WORKFLOW)
        (job,) = workflow["jobs"].values()
        self.assertEqual(self.uses(job), ["actions/checkout", "actions/setup-python"])
        lines = self.run_lines(job)
        self.assertEqual(lines[0], "python -m unittest discover -s tests")
        self.assertEqual(lines[1:], ["python -m pip install .", CONSOLE + " check"],
                         "CI also installs the package and lints this repo with the installed CLI")

    def test_the_matrix_covers_the_supported_python_range_with_quoted_versions(self):
        (job,) = load(REPO_WORKFLOW)["jobs"].values()
        versions = job["strategy"]["matrix"]["python-version"]
        self.assertTrue(all(isinstance(version, str) for version in versions), "3.10 unquoted would load as 3.1")
        self.assertEqual(versions[0], "3.8", "requires-python is >=3.8")
        self.assertIn("%d.%d" % sys.version_info[:2], versions, "the Python running these tests is in the matrix")
        self.assertIs(job["strategy"]["fail-fast"], False)
        setup = [step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@")][0]
        self.assertEqual(setup["with"], {"python-version": "${{ matrix.python-version }}"})


class DocumentedCommandTests(unittest.TestCase):
    """Every command line the docs tell a user to run is one the CLI accepts."""

    def documented(self):
        found = []
        for rel in DOCS:
            for block in fenced(rel, "sh"):
                found += [(rel, line) for line in shell_lines(block)]
        return found

    def test_documented_commands_parse_with_the_real_cli(self):
        commands = {}
        for rel, line in self.documented():
            arguments = cli_arguments(line)
            if arguments is not None:
                with self.subTest(file=rel, line=line):
                    commands.setdefault(parse(arguments).command, []).append(line)
        self.assertEqual(sorted(commands), ["adopt", "check", "encode", "init"])

    def test_readme_documents_install_lint_use_and_what_fails(self):
        readme = text_of("README.md")
        for needle in ("pipx install git+%s.git" % URL, INSTALL, CONSOLE + " check",
                       "python3 .encode-the-correction/cli.py check", "python3 -m unittest discover -s tests",
                       "examples/github-actions.yml", "examples/gitlab-ci.yml", "examples/ci.sh", "docs/ci.md",
                       "never fail or block a\nsession", "fail on a violation"):
            self.assertIn(needle, readme)

    def test_readme_keeps_its_four_original_sections_in_order(self):
        headings = re.findall(r"(?m)^## (.+)$", text_of("README.md"))
        original = ["1. Where the idea comes from", "2. Start in a new project",
                    "3. Adopt in an existing project", "4. Hand it to any coding agent"]
        self.assertEqual([heading for heading in headings if heading in original], original)

    def test_the_pasteable_instruction_prefers_the_installed_cli_and_falls_back_to_the_vendored_one(self):
        instruction = fenced("README.md", "text")[0]
        self.assertTrue(instruction.startswith('Install the "encode-the-correction" tool from <REPO>'))
        installed = instruction.index("`%s check`" % CONSOLE)
        vendored = instruction.index("`python3 .encode-the-correction/cli.py check`")
        self.assertLess(installed, vendored)
        for needle in ("pipx install git+<REPO>", "encode_the_correction.py adopt .", CONSOLE + " adopt .",
                       "--hooks claude-code", "Do not commit or push unless I ask."):
            self.assertIn(needle, instruction)


if __name__ == "__main__":
    unittest.main()
