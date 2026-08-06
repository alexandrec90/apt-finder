"""`apt-finder` — the command line over the whole thing.

    apt-finder facebook-login          # once: sign in yourself, cookies are kept
    apt-finder scrape                  # both sources, throttled, into Postgres
    apt-finder scrape --source kijiji
    apt-finder list                    # what matched
    apt-finder list --verdict maybe    # what needs your eyes
    apt-finder inspect kijiji          # dump what the parser saw, for drift triage

Failures land in ``logs/scrape-errors.log`` as well as the terminal, per the project's
failure-artifact rule: the terminal keeps a status line, the file keeps everything
needed to diagnose.
"""

import argparse
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from data_lake.db.models import Listing
from sqlalchemy import select

from apt_finder.config import get_settings
from apt_finder.criteria import MATCH, MAYBE, REJECT
from apt_finder.db.session import get_session
from apt_finder.runtime import configure_lake
from apt_finder.scrapers.base import ScrapeResult

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = REPO_ROOT / "logs"
ERROR_ARTIFACT = LOG_DIR / "scrape-errors.log"
REPORT_ARTIFACT = LOG_DIR / "scrape-report.json"

SOURCES = ("kijiji", "facebook")


def _write_error(source: str, exc: BaseException) -> None:
    """Persist a failure where the next run (or the next agent) will find it."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ERROR_ARTIFACT.write_text(
        f"# source: apt_finder/cli.py ({source})\n"
        f"# when: {datetime.now(UTC).isoformat()}\n"
        f"# fix: apt-finder inspect {source}\n\n" + "".join(traceback.format_exception(exc)),
        encoding="utf-8",
    )


def _scraper(source: str, config):
    """Import scrapers lazily so `list` never needs httpx or Playwright installed."""
    if source == "kijiji":
        from apt_finder.scrapers.kijiji import KijijiScraper

        return KijijiScraper(config=config)
    from apt_finder.scrapers.facebook import FacebookScraper

    return FacebookScraper(config=config)


def cmd_scrape(args) -> int:
    config = get_settings()
    configure_lake()
    sources = SOURCES if args.source == "all" else (args.source,)

    results: list[ScrapeResult] = []
    failures = 0
    for source in sources:
        try:
            result = _scraper(source, config).run()
        except Exception as exc:
            failures += 1
            _write_error(source, exc)
            print(f"{source}: FAILED — details in {ERROR_ARTIFACT.relative_to(REPO_ROOT)}")
            continue
        results.append(result)
        print(result.summary())

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_ARTIFACT.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(UTC).isoformat(),
                "results": [
                    {
                        "platform": r.platform,
                        "seen": r.seen,
                        "new": r.new,
                        "updated": r.updated,
                        "by_verdict": r.by_verdict,
                        "stopped_early": r.stopped_early,
                    }
                    for r in results
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if not failures:
        ERROR_ARTIFACT.write_text("", encoding="utf-8")
    return 1 if failures else 0


def cmd_list(args) -> int:
    configure_lake()
    with get_session() as session:
        rows = (
            session.execute(
                select(Listing)
                .where(Listing.verdict == args.verdict)
                .order_by(Listing.last_seen_at.desc())
                .limit(args.limit)
            )
            .scalars()
            .all()
        )
    if not rows:
        print(f"No listings with verdict '{args.verdict}'. Run `apt-finder scrape` first.")
        return 0
    for row in rows:
        price = f"${row.price_cad:,.0f}" if row.price_cad is not None else "$?"
        rooms = f"{row.rooms:g}½".replace(".5½", "½") if row.rooms else "?"
        laundry = f"w:{row.washer or '?'} d:{row.dryer or '?'}"
        print(f"{price:>8}  {rooms:>4}  {row.city or '?':<16} {laundry:<22} {row.title or ''}")
        print(f"          {row.url or ''}")
        if args.verdict != MATCH and row.verdict_reasons:
            print(f"          why: {', '.join(row.verdict_reasons)}")
    print(f"\n{len(rows)} listing(s).")
    return 0


def cmd_facebook_login(_args) -> int:
    from apt_finder.scrapers.facebook import facebook_login

    config = get_settings()
    print(f"Opening a browser. Sign in, then close it. Profile: {config.facebook_profile_dir}")
    facebook_login(config)
    print("Saved. Later runs reuse this profile headlessly.")
    return 0


def cmd_inspect(args) -> int:
    """Dump what the parser actually extracted, so selector drift is a two-minute fix."""
    config = get_settings()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    artifact = LOG_DIR / f"inspect-{args.source}.json"
    scraper = _scraper(args.source, config)
    listings, stopped = scraper.collect()
    artifact.write_text(
        json.dumps(
            {
                "source": args.source,
                "stopped_early": stopped,
                "count": len(listings),
                "listings": [
                    {
                        "external_id": item.external_id,
                        "title": item.title,
                        "price_text": item.price_text,
                        "location_text": item.location_text,
                        "url": item.url,
                        "attributes": item.attributes,
                        "description": (item.description or "")[:500],
                    }
                    for item in listings[:25]
                ],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"{args.source}: {len(listings)} listing(s) parsed -> {artifact.relative_to(REPO_ROOT)}")
    if not listings:
        print("Nothing parsed. That usually means the site's structure moved — see the artifact.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apt-finder", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape", help="fetch, evaluate and store listings")
    scrape.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    scrape.set_defaults(func=cmd_scrape)

    listing = sub.add_parser("list", help="show stored listings by verdict")
    listing.add_argument("--verdict", choices=[MATCH, MAYBE, REJECT], default=MATCH)
    listing.add_argument("--limit", type=int, default=50)
    listing.set_defaults(func=cmd_list)

    login = sub.add_parser("facebook-login", help="interactive one-time Facebook sign-in")
    login.set_defaults(func=cmd_facebook_login)

    inspect = sub.add_parser("inspect", help="dump parsed output without touching the database")
    inspect.add_argument("source", choices=SOURCES)
    inspect.set_defaults(func=cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
