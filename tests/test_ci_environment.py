"""The PR gate can build an environment this project actually declares.

`[tool.uv.sources]` points `data-lake` at a sibling checkout, so `uv sync` resolves
only when that directory exists. Nothing on a runner creates it: the PR gate checked
out this repo alone and every job died on

    error: Failed to generate package metadata for `data-lake==0.1.0 @ editable+../data-lake`
      Caused by: Distribution not found at: file:///home/runner/work/apt-finder/data-lake

before it ran a single check. These tests pin the two halves of the fix together —
the sibling clone in `setup-python-env`, and the token every job must hand it —
because the failure mode when they drift is a gate that cannot start, not one that
reports something useful.

Parsed as text on purpose. A YAML parse would read better, but PyYAML reaches this
suite only as a transitive dependency of `pre-commit`; importing it here would make
these tests fail for a reason that has nothing to do with CI.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION = REPO_ROOT / ".github" / "actions" / "setup-python-env" / "action.yml"
PR_GATE = REPO_ROOT / ".github" / "workflows" / "pr-gate.yml"
LOCAL_ACTION_REF = "./.github/actions/setup-python-env"


def sibling_source_path() -> str:
    """The editable path `uv sync` will look for, straight from pyproject."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    return pyproject["tool"]["uv"]["sources"]["data-lake"]["path"]


def test_the_gate_clones_exactly_the_path_uv_will_look_for():
    # The two are edited in different files by different concerns, so an equality
    # assertion is the only thing keeping a renamed sibling from reproducing the
    # original failure verbatim.
    expected = "${{ github.workspace }}/" + sibling_source_path()
    assert f"TARGET_DIR: {expected}" in ACTION.read_text(encoding="utf-8")


def test_the_cloned_repository_matches_the_sibling_directory_name():
    slug = re.search(r"DATA_LAKE_SLUG:\s*(\S+)", ACTION.read_text(encoding="utf-8"))
    assert slug is not None, "setup-python-env no longer names a data-lake repository"
    assert slug.group(1).split("/")[-1] == Path(sibling_source_path()).name


def test_the_clone_runs_before_uv_sync():
    # Ordering is the whole point: a clone after the sync step is a clone into a job
    # that has already failed.
    # Matched against the `run:` directive, not a bare "uv sync": the phrase appears in
    # this action's own comments, and a substring search finds those first.
    body = ACTION.read_text(encoding="utf-8")
    assert body.index("TARGET_DIR:") < body.index("run: uv sync")


def test_a_missing_token_fails_the_job_naming_the_secret_to_add():
    # data-lake is private, so the default GITHUB_TOKEN cannot read it. Whoever hits
    # that has no way to guess the remedy from git's "Repository not found".
    body = ACTION.read_text(encoding="utf-8")
    error_lines = [line for line in body.splitlines() if "::error::" in line]
    assert error_lines, "the clone failure is silent"
    assert any("DATA_LAKE_TOKEN" in line for line in error_lines)
    assert "exit 1" in body, "the clone failure does not fail the step"


def test_the_token_never_reaches_the_shell_through_an_expression():
    # `${{ }}` is substituted before bash parses the line, so a token interpolated
    # into `run:` becomes part of the program rather than a value. It must arrive
    # through `env:` instead.
    for line in ACTION.read_text(encoding="utf-8").splitlines():
        if "${{" in line and "data-lake-token" in line:
            assert line.strip().startswith("DATA_LAKE_TOKEN:"), line


def test_every_job_that_sets_up_the_environment_passes_the_token():
    # A job that omits it gets an empty input, falls back to GITHUB_TOKEN, and dies on
    # a 404 for a repository that exists — the confusing failure this whole file exists
    # to prevent.
    # `uses:` directives only. devkit-drift's comment explains why it deliberately does
    # NOT use this action, and naming the path there must not read as a call site.
    lines = PR_GATE.read_text(encoding="utf-8").splitlines()
    uses = [i for i, line in enumerate(lines) if line.strip() == f"- uses: {LOCAL_ACTION_REF}"]
    assert uses, "pr-gate.yml no longer uses the local setup action"
    for index in uses:
        following = "\n".join(lines[index + 1 : index + 4])
        assert "data-lake-token: ${{ secrets.DATA_LAKE_TOKEN }}" in following, (
            f"{LOCAL_ACTION_REF} at pr-gate.yml:{index + 1} is not given the token"
        )
