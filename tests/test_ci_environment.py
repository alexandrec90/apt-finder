"""The PR gate can build an environment this project actually declares.

`[tool.uv.sources]` points `data-lake` at a sibling checkout, so `uv sync` resolves
only when that directory exists. Nothing on a runner creates it: the PR gate checked
out this repo alone and every job died on

    error: Failed to generate package metadata for `data-lake==0.1.0 @ editable+../data-lake`
      Caused by: Distribution not found at: file:///home/runner/work/apt-finder/data-lake

before it ran a single check. These tests pin the two halves of the fix together —
the sibling clone, and the token every job must hand it — because the failure mode
when they drift is a gate that cannot start, not one that reports something useful.

The clone lives in its **own** composite action, `checkout-data-lake`. It used to be
the first step of `setup-python-env`, which devkit v0.7.0 began vendoring
byte-identical from the shared repo; a vendored file cannot carry one project's private
sibling, and the pull that adopted v0.7.0 silently deleted the step. These tests are
what caught that, and retargeting them is what keeps them able to catch it again.

Ordering is now a property of the workflow rather than of one action's step list, so
`test_the_clone_runs_before_the_python_setup` reads pr-gate.yml.

Parsed as text on purpose. A YAML parse would read better, but PyYAML reaches this
suite only as a transitive dependency of `pre-commit`; importing it here would make
these tests fail for a reason that has nothing to do with CI.
"""

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION = REPO_ROOT / ".github" / "actions" / "checkout-data-lake" / "action.yml"
PR_GATE = REPO_ROOT / ".github" / "workflows" / "pr-gate.yml"
NIGHTLY = REPO_ROOT / ".github" / "workflows" / "nightly.yml"
CLONE_ACTION_REF = "./.github/actions/checkout-data-lake"
SETUP_ACTION_REF = "./.github/actions/setup-python-env"

# Every workflow whose jobs install the project. Both have to clone the sibling, and
# the nightly is the one nobody watches — it is exactly where a missing token would go
# unnoticed until someone needed the run.
INSTALLING_WORKFLOWS = (PR_GATE, NIGHTLY)


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
    assert slug is not None, "checkout-data-lake no longer names a data-lake repository"
    assert slug.group(1).split("/")[-1] == Path(sibling_source_path()).name


def test_the_clone_runs_before_the_python_setup():
    # Ordering is the whole point: a clone after the sync step is a clone into a job
    # that has already failed. It stopped being a property of one action's step list
    # when the clone moved out of setup-python-env, so it is asserted across the two
    # `uses:` directives instead — the sync happens inside the second one.
    for workflow in INSTALLING_WORKFLOWS:
        lines = [line.strip() for line in workflow.read_text(encoding="utf-8").splitlines()]
        clones = [i for i, line in enumerate(lines) if line == f"- uses: {CLONE_ACTION_REF}"]
        setups = [i for i, line in enumerate(lines) if line == f"- uses: {SETUP_ACTION_REF}"]
        assert len(clones) == len(setups), (
            f"{workflow.name}: {len(setups)} job(s) set up Python but {len(clones)} clone "
            "the sibling -- uv sync cannot resolve in the difference"
        )
        for clone, setup in zip(clones, setups, strict=True):
            assert clone < setup, (
                f"{workflow.name}:{setup + 1} sets up Python before cloning the sibling"
            )


def test_a_missing_token_fails_the_job_naming_the_secret_to_add():
    # data-lake is private, so the default GITHUB_TOKEN cannot read it. Whoever hits
    # that has no way to guess the remedy from git's "Repository not found".
    body = ACTION.read_text(encoding="utf-8")
    error_lines = [line for line in body.splitlines() if "::error::" in line]
    assert error_lines, "the clone failure is silent"
    assert any("DATA_LAKE_TOKEN" in line for line in error_lines)
    assert "exit 1" in body, "the clone failure does not fail the step"


def test_the_clone_failure_says_which_of_the_two_remedies_applies():
    # GitHub answers "Repository not found" for a private repository the token cannot
    # see, and the same for one that does not exist — so a bare clone failure cannot
    # distinguish "no secret is set, and the job fell back to GITHUB_TOKEN" from "the
    # secret is set but its token has no read access to data-lake". Those are different
    # fixes, and a message that names both leaves the reader to guess: PR #4 burned a
    # full CI round on exactly that ambiguity.
    body = ACTION.read_text(encoding="utf-8")
    assert 'if [ "${DATA_LAKE_TOKEN_SUPPLIED}" = "true" ]' in body, (
        "the clone failure no longer branches on whether the secret reached the job"
    )
    error_lines = [line for line in body.splitlines() if "::error::" in line]
    assert len(error_lines) >= 2, (
        f"one message cannot cover both remedies; found {len(error_lines)}"
    )


def test_the_token_never_reaches_the_shell_through_an_expression():
    # `${{ }}` is substituted before bash parses the line, so a token interpolated
    # into `run:` becomes part of the program rather than a value. It must arrive
    # through `env:` instead.
    #
    # Two env vars are derived from the input. DATA_LAKE_TOKEN carries the value;
    # DATA_LAKE_TOKEN_SUPPLIED carries only whether it is empty, which is what lets the
    # script tell the two remedies above apart. The emptiness test has to stay a
    # comparison — an expression that yields the token itself would put a secret in a
    # variable nothing masks.
    for line in ACTION.read_text(encoding="utf-8").splitlines():
        if "${{" in line and "data-lake-token" in line:
            name, _, expression = line.strip().partition(":")
            assert name in {"DATA_LAKE_TOKEN", "DATA_LAKE_TOKEN_SUPPLIED"}, line
            if name == "DATA_LAKE_TOKEN_SUPPLIED":
                assert "!= ''" in expression, line


def test_every_job_that_clones_the_sibling_passes_the_token():
    # A job that omits it gets an empty input, falls back to GITHUB_TOKEN, and dies on
    # a 404 for a repository that exists — the confusing failure this whole file exists
    # to prevent.
    # `uses:` directives only. devkit-drift's comment explains why it deliberately does
    # NOT use the setup action, and naming the path there must not read as a call site.
    for workflow in INSTALLING_WORKFLOWS:
        lines = workflow.read_text(encoding="utf-8").splitlines()
        uses = [i for i, line in enumerate(lines) if line.strip() == f"- uses: {CLONE_ACTION_REF}"]
        assert uses, f"{workflow.name} no longer uses the sibling-clone action"
        for index in uses:
            following = "\n".join(lines[index + 1 : index + 4])
            assert "data-lake-token: ${{ secrets.DATA_LAKE_TOKEN }}" in following, (
                f"{CLONE_ACTION_REF} at {workflow.name}:{index + 1} is not given the token"
            )


def test_the_vendored_setup_action_is_never_given_project_specific_inputs():
    # The regression that produced this split. `setup-python-env/action.yml` is in
    # devkit's MANIFEST and byte-compared by the `devkit-drift` gate, so it can only
    # ever accept the inputs devkit declares. A call site passing `data-lake-token`
    # here would be re-creating the deleted step's interface against an action that no
    # longer has it: silently ignored, and the clone never happens.
    for workflow in INSTALLING_WORKFLOWS:
        lines = workflow.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.strip() != f"- uses: {SETUP_ACTION_REF}":
                continue
            following = "\n".join(lines[index + 1 : index + 4])
            assert "data-lake-token" not in following, (
                f"{workflow.name}:{index + 1} passes data-lake-token to the vendored "
                "setup action, which does not declare it -- use "
                f"{CLONE_ACTION_REF} for that"
            )
