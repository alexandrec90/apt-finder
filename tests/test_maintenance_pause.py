"""Contract for the repository's deliberate maintenance pause."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = REPO_ROOT / ".github" / "dependabot.yml"


def test_every_dependabot_ecosystem_is_paused():
    """No configured package manager may keep opening version-update PRs."""
    text = DEPENDABOT.read_text(encoding="utf-8")
    ecosystems = re.findall(r"^\s*-\s*package-ecosystem:", text, re.M)
    paused = re.findall(r"^\s*open-pull-requests-limit:\s*0\s*$", text, re.M)

    assert ecosystems, "dependabot.yml declares no package ecosystems"
    assert len(paused) == len(ecosystems), (
        "apt-finder is paused, so every Dependabot package ecosystem must set "
        "open-pull-requests-limit: 0"
    )
