"""The command line: argument wiring and the failure-artifact contract.

Nothing here touches a real database or a real site — `_scraper` and `configure_lake`
are patched. The point is that a failing source writes a diagnosable file and does not
take the other source down with it.
"""

import json

import pytest

from apt_finder import cli
from apt_finder.criteria import MATCH, MAYBE
from apt_finder.scrapers.base import ScrapeResult


@pytest.fixture(autouse=True)
def artifacts(tmp_path, monkeypatch):
    """Redirect the log artifacts into a temp dir so tests never write to logs/."""
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cli, "ERROR_ARTIFACT", tmp_path / "scrape-errors.log")
    monkeypatch.setattr(cli, "REPORT_ARTIFACT", tmp_path / "scrape-report.json")
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "configure_lake", lambda: None)
    return tmp_path


def test_parser_defaults_to_all_sources():
    args = cli.build_parser().parse_args(["scrape"])
    assert args.source == "all"


def test_parser_accepts_one_source():
    assert cli.build_parser().parse_args(["scrape", "--source", "kijiji"]).source == "kijiji"


def test_parser_rejects_an_unknown_source():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["scrape", "--source", "craigslist"])


def test_list_defaults_to_matches():
    args = cli.build_parser().parse_args(["list"])
    assert args.verdict == MATCH
    assert args.limit == 50


def test_list_accepts_maybe():
    assert cli.build_parser().parse_args(["list", "--verdict", MAYBE]).verdict == MAYBE


def test_inspect_requires_a_source():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["inspect"])


def test_scrape_writes_a_report(artifacts, monkeypatch, capsys):
    class Fake:
        def run(self):
            return ScrapeResult(platform="kijiji", seen=3, new=2, updated=1)

    monkeypatch.setattr(cli, "_scraper", lambda source, config: Fake())
    assert cli.main(["scrape", "--source", "kijiji"]) == 0

    report = json.loads((artifacts / "scrape-report.json").read_text(encoding="utf-8"))
    assert report["results"][0]["platform"] == "kijiji"
    assert report["results"][0]["seen"] == 3
    assert "3 seen" in capsys.readouterr().out


def test_failure_is_written_to_the_artifact(artifacts, monkeypatch, capsys):
    class Boom:
        def run(self):
            raise RuntimeError("kijiji moved the goalposts")

    monkeypatch.setattr(cli, "_scraper", lambda source, config: Boom())
    assert cli.main(["scrape", "--source", "kijiji"]) == 1

    artifact = (artifacts / "scrape-errors.log").read_text(encoding="utf-8")
    # The artifact must carry enough to diagnose without re-running.
    assert "kijiji moved the goalposts" in artifact
    assert "Traceback" in artifact
    assert "fix: apt-finder inspect kijiji" in artifact
    assert "FAILED" in capsys.readouterr().out


def test_one_failing_source_does_not_sink_the_other(artifacts, monkeypatch):
    def scraper(source, config):
        class Impl:
            def run(self):
                if source == "kijiji":
                    raise RuntimeError("down")
                return ScrapeResult(platform="facebook", seen=5, new=5)

        return Impl()

    monkeypatch.setattr(cli, "_scraper", scraper)
    assert cli.main(["scrape"]) == 1

    report = json.loads((artifacts / "scrape-report.json").read_text(encoding="utf-8"))
    platforms = [entry["platform"] for entry in report["results"]]
    assert platforms == ["facebook"]


def test_artifact_is_cleared_on_a_clean_run(artifacts, monkeypatch):
    (artifacts / "scrape-errors.log").write_text("stale failure", encoding="utf-8")

    class Fake:
        def run(self):
            return ScrapeResult(platform="kijiji")

    monkeypatch.setattr(cli, "_scraper", lambda source, config: Fake())
    cli.main(["scrape", "--source", "kijiji"])

    # A stale artifact would send the next reader chasing a fixed failure.
    assert (artifacts / "scrape-errors.log").read_text(encoding="utf-8") == ""


def test_inspect_writes_what_it_parsed(artifacts, monkeypatch, capsys):
    from apt_finder.listing import ScrapedListing

    class Fake:
        def collect(self):
            return [ScrapedListing(platform="kijiji", external_id="1", title="4 1/2")], None

    monkeypatch.setattr(cli, "_scraper", lambda source, config: Fake())
    assert cli.main(["inspect", "kijiji"]) == 0

    dumped = json.loads((artifacts / "inspect-kijiji.json").read_text(encoding="utf-8"))
    assert dumped["count"] == 1
    assert dumped["listings"][0]["title"] == "4 1/2"


def test_inspect_says_so_when_nothing_parsed(artifacts, monkeypatch, capsys):
    class Fake:
        def collect(self):
            return [], None

    monkeypatch.setattr(cli, "_scraper", lambda source, config: Fake())
    cli.main(["inspect", "kijiji"])
    assert "structure moved" in capsys.readouterr().out
