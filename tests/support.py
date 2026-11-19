"""Shared helpers. Every test works in a throwaway project under the system temp dir."""
import json
import os
import shutil
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

CONSOLE = "encode-the-correction"
# What the launcher that pip generates for the console script does, verbatim.
CONSOLE_SHIM = "import sys; from encode_the_correction import main; sys.exit(main())"
SKIP_INSTALL_ENV = "ETC_SKIP_INSTALL_TESTS"

sys.path.insert(0, str(REPO))


def copy_repo(dest):
    """A copy of this repo's working tree to build from, so pip never writes build files into the repo."""
    shutil.copytree(str(REPO), str(dest), ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "build", "dist", "*.egg-info", ".venv", "venv", ".tox"))
    return dest


def pip_env(scratch, **extra):
    """The caller's environment (pip needs its proxy and index settings) with pip's cache kept in `scratch`."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PIP_REQUIRE_VIRTUALENV")}
    env.update(PIP_CACHE_DIR=str(Path(scratch) / "pip-cache"), PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
    env.update(extra)
    return env


def install_tests_skip_reason():
    if os.environ.get(SKIP_INSTALL_ENV):
        return "%s is set" % SKIP_INSTALL_ENV
    return None


class InstalledVenv:
    """This repo pip-installed into a throwaway virtual environment, built once per test class."""

    def __init__(self, scratch):
        scratch = Path(scratch)
        self.root = scratch / "venv"
        try:
            import ensurepip  # noqa: F401  (some distributions ship venv and ensurepip separately)
            import venv  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("this Python cannot create a virtual environment with pip (%s)" % exc)
        done = subprocess.run([sys.executable, "-m", "venv", str(self.root)], capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise AssertionError("`python -m venv` failed:\n%s%s" % (done.stdout, done.stderr))
        self.bin = self.root / ("Scripts" if os.name == "nt" else "bin")
        self.python = self.bin / ("python.exe" if os.name == "nt" else "python")
        self.console = self.bin / (CONSOLE + (".exe" if os.name == "nt" else ""))
        done = subprocess.run(
            [str(self.python), "-m", "pip", "install", str(copy_repo(scratch / "source"))],
            env=pip_env(scratch), capture_output=True, text=True, timeout=900,
        )
        if done.returncode != 0:
            raise AssertionError(
                "`pip install` of this repo failed. pip downloads the build backend (setuptools); on a "
                "machine without network access set %s=1 to skip these tests.\n%s%s"
                % (SKIP_INSTALL_ENV, done.stdout, done.stderr))


class ProjectCase(unittest.TestCase):
    maxDiff = None
    # Shells invoked by adapter tests may create these in $HOME (pwsh on GitHub
    # Actions writes .cache and .local even with -NoProfile). They are not this tool.
    _RUNTIME_HOME_ENTRIES = frozenset({".cache", ".config", ".local"})

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
        # Local project scope only: the tool itself must never write to $HOME.
        unexpected = sorted(
            p.name for p in self.home.iterdir()
            if p.name not in self._RUNTIME_HOME_ENTRIES
        )
        self.assertEqual(unexpected, [], "the tool wrote to $HOME")

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

    def installed(self, *args, **kwargs):
        """Run the tool the way an installed console script does, without installing anything.

        The module is imported from a site-packages-like directory that is neither this
        repo nor inside a project, and entered through `main`, exactly as the generated
        launcher does. `cwd` is what tells it which project to work on.
        """
        site = self.tmp / "site-packages"
        if not site.exists():
            site.mkdir()
            shutil.copy(str(CLI), str(site / CLI.name))
        env = dict(kwargs.pop("env", None) or {}, PYTHONPATH=str(site))
        return self.run_argv([sys.executable, "-c", CONSOLE_SHIM] + list(args), env=env, **kwargs)

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
