# TODO — Apt Finder

## Setup

- [ ] Fill in `.env` from `.env.example` (it is gitignored; nothing works without it)
- [ ] Confirm `python scripts/sync-devkit.py --list` shows a stamped `DEVKIT_VERSION`
- [ ] Set `DEVKIT_DIR` in CI so the drift check actually gates — a
      `--check` that prints "nothing to do (skipping)" is checking nothing
- [ ] Replace the placeholder DB password in `.env` (the committed one is a
      local-dev placeholder and is fine to keep local, but never reuse it remotely)

## Before the first real scrape

These need a live look at the sites; they cannot be settled from code.

- [ ] **Verify `KIJIJI_SEARCH_URLS`.** The default carries category/location ids
      (`c37l1700242`) that Kijiji renumbers occasionally. A stale id returns an
      unrelated or empty result set rather than an error. Check with
      `apt-finder inspect kijiji` — it writes what was parsed to
      `logs/inspect-kijiji.json` without touching the database.
- [ ] **Verify `FACEBOOK_SEARCH_URLS`** the same way, after `apt-finder facebook-login`.
- [ ] **Confirm Kijiji's price units.** The parser assumes integer cents
      (`_CENTS_THRESHOLD` in `scrapers/kijiji.py`) and falls back to dollars for small
      integers. Both branches are tested; only a live payload proves which one fires.
- [ ] Run `docker compose up -d && alembic upgrade head` once the Docker daemon is up —
      the migration has not been applied against a real Postgres yet.

## Worth doing next

- [ ] Notify on new matches (`scripts/notify.py` already exists) rather than requiring
      `apt-finder list` to be run by hand.
- [ ] Schedule `apt-finder scrape` a few times a day. Keep the runs irregular — a cron
      job firing at exactly :00 is its own signature.
- [ ] Add a `replay` command that re-evaluates stored rows against current criteria.
      Everything needed is already persisted (`raw`, plus the parsed columns); this is
      the payoff for storing rejects.
- [ ] Track price changes over time instead of overwriting `price_cad` — a listing whose
      rent drops twice is a strong signal.

## Archive

- [x] Replace the placeholder in `apt_finder/` with something that does the job
- [x] Delete `tests/test_smoke.py` once real tests exist
