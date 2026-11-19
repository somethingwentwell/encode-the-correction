"""Shared helpers. Every test works in a throwaway project under the system temp dir."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "encode_the_correction.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOOL_DIR = ".encode-the-correction"
VENDORED = TOOL_DIR + "/cli.py"
REGISTRY = TOOL_DIR + "/corrections.json"
BEGIN = "<!-- BEGIN encode-the-correction"
END = "<!-- END encode-the-correction -->"

sys.path.insert(0, str(REPO))


class ProjectCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="etc-test-")
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name).resolve()
        for folder in [self.tmp] + list(self.tmp.parents):
            if folder == REPO or (folder / ".git").exists():
                raise AssertionError("tests must run in a temp dir outside any repository, got %s" % self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.project = self.tmp / "my project"  # the space is deliberate: hook commands must quote paths
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("CLAUDE_PROJECT_DIR", "CURSOR_PROJECT_DIR", "GIT_DIR", "GIT_WORK_TREE")}
        self.env["HOME"] = str(self.home)

    def tearDown(self):
        # Local project scope only: nothing may ever be written to the user's home.
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [], "the tool wrote to $HOME")

    # -- running things ----------------------------------------------------

    def run_argv(self, argv, cwd=None, expect=0, stdin=None, env=None):
        proc = subprocess.run(
            [str(a) for a in argv], cwd=str(cwd or self.tmp), env=dict(self.env, **(env or {})),
            input=stdin, capture_output=True, text=True, timeout=120,
        )
        if expect is not None:
            self.assertEqual(proc.returncode, expect, "argv=%r\nstdout=%s\nstderr=%s" % (argv, proc.stdout, proc.stderr))
        return proc

    def cli(self, *args, **kwargs):
        """Run the tool from its source repo."""
        return self.run_argv([sys.executable, CLI] + list(args), **kwargs)

    def vendored(self, *args, **kwargs):
        """Run the copy installed inside the temp project."""
        return self.run_argv([sys.executable, self.project / VENDORED] + list(args), **kwargs)

    def shell(self, command, cwd=None, shell="sh", **kwargs):
        """Run a hook command string the way a runtime would: through a shell."""
        return self.run_argv([shell, "-c", command], cwd=cwd or self.project, **kwargs)

    # -- files -------------------------------------------------------------

    def write(self, rel, text):
        path = self.project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def read(self, rel):
        return (self.project / rel).read_bytes().decode("utf-8")

    def read_json(self, rel):
        return json.loads(self.read(rel))

    def exists(self, rel):
        return (self.project / rel).exists()

    def snapshot(self, root=None):
        """Every file under root as {relative path: bytes}; directories as None."""
        root = Path(root or self.project)
        snap = {}
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                snap[rel] = "-> " + os.readlink(str(path))
            elif path.is_dir():
                snap[rel] = None
            else:
                snap[rel] = path.read_bytes()
        return snap

    def encode_timer_check(self, *extra, **kwargs):
        return self.vendored(
            "encode", "--id", "no-timer-quick-fixes", "--forbid", r"\bsetTimeout\(",
            "--glob", "src/**/*.js",
            "--message", "Imperative timers patch over a state-flow problem.",
            "--fix", "Drive the update from state and delete the timer.",
            *extra, **kwargs
        )
