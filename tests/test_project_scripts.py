"""Regression tests for the project-owned scripts.

Both `lint-all.py` and `run-tests.py` shell out and read the output back. On Windows,
`subprocess.run(..., text=True)` decodes with the *locale* codec (cp1252), not UTF-8 —
so the moment a linter or a failing test echoed a source line containing `é`, the
reader thread raised `UnicodeDecodeError`, `stdout` came back as `None`, and the script
died on `stdout + stderr` with a `TypeError` that named neither cause.

That is not hypothetical here: this codebase parses French listings, so accented
characters are in nearly every module and fixture. These tests pin the fix.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str):
    """Import a hyphenated script by path (`lint-all.py` is not a valid module name)."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCENTED = "sécheuse incluse — 4½"


@pytest.fixture
def failing_accented_command():
    """A command that prints non-ASCII and exits non-zero, like a real lint failure."""
    return [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.reconfigure(encoding='utf-8'); print({ACCENTED!r}); sys.exit(1)",
    ]


def test_lint_all_survives_accented_tool_output(failing_accented_command):
    lint_all = load("lint-all")
    section = lint_all.run_tool("fake", failing_accented_command, "fix hint")
    assert "sécheuse" in section
    assert "fix hint" in section


def test_lint_all_reports_nothing_when_a_tool_passes():
    lint_all = load("lint-all")
    assert lint_all.run_tool("fake", [sys.executable, "-c", "pass"], "hint") == ""


def test_lint_all_treats_a_missing_tool_as_skipped():
    # A missing tool is not a failure: writing "command not found" into the artifact
    # hands the reader something no source edit can fix.
    lint_all = load("lint-all")
    assert lint_all.run_tool("fake", ["definitely-not-a-real-binary-xyz"], "hint") == ""


def test_subprocess_default_encoding_would_have_failed(failing_accented_command):
    """The bug itself, pinned.

    On a cp1252 host this call loses the accented bytes or fails outright; with
    UTF-8 requested explicitly it round-trips. If this ever stops discriminating, the
    two tests above are no longer proving anything.
    """
    result = subprocess.run(  # noqa: S603 - fixture-built argv running this interpreter
        failing_accented_command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert "sécheuse" in result.stdout
    assert result.returncode == 1


def test_run_tests_filter_keeps_failure_sections():
    run_tests = load("run-tests")
    raw = "\n".join(
        [
            "collected 3 items",
            "tests/test_x.py ..F",
            "=== FAILURES ===",
            "____ test_secheuse ____",
            "E   AssertionError: sécheuse",
            "= short test summary info =",
            "FAILED tests/test_x.py::test_secheuse",
        ]
    )
    filtered = run_tests.filter_output(raw)
    assert "sécheuse" in filtered
    assert "collected 3 items" not in filtered


def test_run_tests_filter_drops_site_packages_frames():
    run_tests = load("run-tests")
    raw = "=== FAILURES ===\n  /x/site-packages/sqlalchemy/orm.py:1\n  apt_finder/db.py:2\n"
    filtered = run_tests.filter_output(raw)
    assert "site-packages" not in filtered
    assert "apt_finder/db.py" in filtered


def test_run_tests_caps_a_long_failure_block():
    run_tests = load("run-tests")
    block = "_____ test_big _____\n" + "\n".join(f"line {i}" for i in range(100))
    capped = run_tests.cap_failure_blocks(block, limit=10)
    assert "truncated" in capped
    assert len(capped.splitlines()) < 20
