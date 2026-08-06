# Apt Finder

Finds full apartments to rent in and around Gatineau, Québec, on Kijiji and Facebook
Marketplace: under $2,000/month, appliances included (washer and dryer specifically),
not a room. Most listings are in French.

## What the domain actually requires

Four things about this problem shape the whole codebase. They are here because each one
looks like a detail and is in fact the thing that makes a naive implementation wrong.

1. **Ottawa is 5 km away, across a provincial border.** Both marketplaces treat
   Gatineau and Ottawa as one metro area, so a distance filter alone returns mostly
   Ontario. Province is therefore decided *independently* of proximity, by postal code
   first (Canadian FSAs encode the province: `K`/`L`/`M`/`N`/`P` = Ontario, `G`/`H`/`J`
   = Québec), and an unplaceable listing is **rejected, not deferred**. See
   `apt_finder/normalize/geo.py`.
2. **"laveuse" in the text does not mean a washer is included.** `espace pour
   laveuse-sécheuse` and `branchement laveuse-sécheuse` mean there is a *hookup* — you
   supply the machines — and both are extremely common locally. `buanderie commune`
   means shared building laundry. Appliance detection is tri-state with negation,
   hookup and shared-laundry handling: `apt_finder/normalize/amenities.py`.
3. **`chambre` appears in both answers.** `chambre à louer` is a room; `2 chambres à
   coucher` is a two-bedroom apartment. Room detection is phrase-based, never
   word-based. Québec `4½` room notation is the primary size signal, more common than a
   bedroom count: `apt_finder/normalize/dwelling.py`.
4. **The accounts are worth more than the data.** A banned Facebook account cannot be
   undone and takes the search with it. Hence jittered intervals, periodic long pauses,
   a per-run request cap, a circuit breaker, and Facebook's two-phase fetch (pre-filter
   on card data, open only the survivors). See `apt_finder/scrapers/throttle.py` and
   the module docstring in `apt_finder/scrapers/facebook.py`.

Three verdicts, not two: **contradiction rejects, silence defers.** An ad that says
"buanderie commune" is rejected; an ad that simply never mentions laundry becomes
`maybe`. A strict yes/no filter here produces a very clean, very empty result list.
`apt_finder/criteria.py` is the single auditable place all of this comes together, and
every verdict stores machine-readable reason codes so a rule change can be replayed
over stored rows and diffed.

## The data-lake seam

This project is a **consumer** of the sibling `data-lake` package (editable path
dependency; see `[tool.uv.sources]`). It uses:

- `data_lake.db.models.Listing` — the `listings` table, which **lives in the lake**.
- `data_lake.ingestion.base.Connector` — the fetch/upsert contract and session plumbing.
- `data_lake.configure(session_factory=...)` — called in `apt_finder/runtime.py`.

**The lake is domain-agnostic.** It is not a finance package; its market/news/social
tables merely came first because ibkr_trader was the first consumer. `test_lake_seam.py`
enforces exactly one boundary — *nothing account-shaped* (no `orders`/`executions`/
`predictions` tables, no `account_`/`order_`/`commission` columns). Scraped public
listings are ordinary shared content, so `Listing` belongs there, and a future consumer
looking for housing data finds it instead of re-scraping.

**The lake owns no engine and no migrations**, so this repo supplies both. That is why
`listings` is declared upstream but *created* by `migrations/` here.

**Adoption filter.** Importing `data_lake.db.models` registers the lake's entire schema
on the shared `Base.metadata`. Seeing is not owning: `apt_finder/db/adoption.py` lists
which of those tables this project materialises (`listings`), and Alembic's
`include_object` hook uses it. Without that, autogenerate proposes creating ten empty
market-data tables here. Add a name to `ADOPTED_LAKE_TABLES` when you start using a
dataset.

**Settings are deliberately not supplied to the lake.** Not because the lake is
finance-only — it isn't — but because `LakeSettings` is today a single monolithic
Protocol requiring eleven provider credentials (`finnhub_key`, `ibkr_host`, …). Since
Protocols are structural, satisfying it would mean carrying eleven dead fields. So
`data_lake.configure()` gets the session factory only, and a stray reach for
`Connector.settings` raises a `RuntimeError` naming the fix instead of silently finding a
credential. Scraper knobs come from `self.config` (`apt_finder/config.py`), and
`tests/test_config.py` pins both halves. *Narrowing that Protocol per-source is the real
fix, upstream.*

**Privacy (Québec Law 25).** Listings are scraped content from real people. Seller
identity is stored as a `stable_hash` digest only, never plaintext — the same rule the
lake applies to social authors. `tests/test_persistence.py` enforces it.

## Commands

```bash
apt-finder facebook-login          # once: sign in yourself; cookies persist locally
apt-finder scrape                  # both sources, throttled, into Postgres
apt-finder scrape --source kijiji
apt-finder list                    # what matched
apt-finder list --verdict maybe    # what needs your own eyes
apt-finder inspect kijiji          # dump what the parser saw -> logs/, for drift triage
```

`inspect` exists because both sites' structures drift. It writes what was actually
parsed to `logs/inspect-<source>.json` without touching the database, which turns "we
get zero results now" into a two-minute fix.

## Tech Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Database | PostgreSQL |
| ORM / Migrations | SQLAlchemy 2 (async) + Alembic |
| Container | Docker + Docker Compose |
| Tests | pytest |
| Lint | ruff |

## Environment Variables

See `.env.example` for every variable. `.env` is gitignored and holds this
checkout's ports and credentials.

## Tooling

> Everything in this section needs the local Docker Desktop daemon. If it isn't
> running, make the code change and defer container/stack verification until it is
> (or to CI). Run `docker ps` first — an `npipe`/daemon error means Desktop is stopped.

### Scripts and the vendored harness

Both are covered by **`.claude/rules/engineering.md`** — script conventions (pure
importable functions, stdlib-only hooks, tests in the same change), the failure-artifact
rule, and how the `.devkit.toml` seam works. That file is vendored from
[devkit](https://github.com/alexandrec90/devkit) and drift-gated, so it is the
authority; this file does not repeat it.

### VS Code tasks

- Use `"type": "process"` so VS Code monitors the process directly — that is what
  makes the spinner stop and the exit-code icon appear reliably.
- Set `"close": false` in `presentation` so the terminal stays open for review.
- **Wrap with `notify-wrap.py`** for the completion toast; never call `notify.py`
  from inside a script. Notifications are a task-layer concern only.
- Label convention: `"Domain: Title Case Action"`, and **every task carries a
  `detail`** — that is the second line in the quick-pick, and the only place a
  one-click action can state its cost or blast radius.

### Failure artifacts (fix from a file, not from the terminal)

Any task or script whose failures an agent is expected to act on must persist the
failure to a **parseable artifact file** under `logs/`. Never rely on streamed
terminal output — it scrolls away and buries the signal. Keep the terminal to a
status line plus the artifact path; put everything needed to diagnose in the file.
Write the artifact on failure too, not just success, and overwrite per run.

### Docker subprocess calls

- **`docker compose exec` must use `-T`** — without it a pseudo-TTY is allocated and
  the subprocess handle can outlive the command, leaving the caller hung.

## Parallel worktrees

`../apt-finder-b` is a second checkout (`git worktree`) on its own branch, with its
own Docker stack. The `.git` object store, Docker image layers, and the package cache
are shared, so the second stack is cheap.

- **`COMPOSE_PROJECT_NAME` must equal the directory name** — it namespaces
  containers, network, and volumes.
- **Every `*_HOST_PORT` is offset by the checkout's slot** (this one is slot
  6, `apt-finder-b` is slot 7). Slots are assigned in
  devkit's `ports.toml`, not picked by hand; `docker compose up` failing with "port
  is already allocated" means two checkouts share a slot.
- `docker compose down -v` is project-scoped and safe to reset one stack, but
  daemon-wide commands like `docker system prune` hit both — don't run them while
  the other stack is up.

## Testing

**`.claude/rules/engineering.md`** is the authority: tests ship in the same commit,
every testable unit of logic is covered, regression test first, targeted runs locally
and full runs in CI.

Add this project's specifics *below* — fixtures, isolation rules, markers, what to mock
and where — but do not restate the policy above. It is vendored and drift-gated; a copy
here is a fork that will disagree with it the first time either is edited.

### This project's specifics

- **No network, ever.** Scrapers take an injected client (`KijijiScraper(client=...)`)
  or browser (`FacebookScraper(browser=...)`). A test that needs a page supplies the
  HTML or the card dicts. Playwright is never imported by the suite — the guarded import
  in `scrapers/facebook.py` is what lets `tests/test_facebook_scraper.py` run with no
  Chromium installed.
- **No real database.** `session_factory` (in `tests/conftest.py`) is in-memory SQLite on
  a `StaticPool`. The models use `SqliteFriendlyBigInt`/`JsonVariant` precisely so this
  works with no container running.
- **`Settings` is always built with `_env_file=None`** (the `settings` fixture). Without
  it a developer's real `.env` leaks in and the suite passes or fails depending on whose
  machine it is on.
- **The parsers are pure, so test them exhaustively.** `apt_finder/normalize/*` and
  `criteria.py` take text and return values — no clock, no IO. They are also the part
  most likely to be subtly wrong, so new French phrasing gets a test case rather than a
  code tweak.
- **Invisible characters are written as `chr(0x…)`** in tests (NBSP, narrow NBSP,
  fraction slash). A literal NBSP is indistinguishable from a space in review, and ruff's
  RUF001 rejects it.
- `scripts/hooks/tests/` is the vendored harness tier, excluded from `testpaths`. Run it
  with `python -m pytest scripts/hooks/tests/ -q`. Never edit those here.

> `pytest` lives in the `dev` extra, so a bare `uv sync` does not install it. Use
> `uv sync --extra dev` (or `--all-extras`) before `uv run pytest`.

## Guardrails

Baseline guardrails — including the instruction-file feedback loop (**never silently
work around a bad instruction**) — are in `.claude/rules/engineering.md`. Rules for
writing skills and rules themselves are in `.claude/rules/authoring.md`.

This project's own, one line each:

- **Never widen the throttle defaults to make a run finish faster.** They exist to
  protect accounts that cannot be recovered. Slow is the feature.
- **Never store a seller's name, profile URL, or phone number** — hash only
  (`stable_hash`), per Québec Law 25. See `apt_finder/db/models.py`.
- **`.local/` is gitignored and must stay that way**: it holds a live logged-in Facebook
  session, which is functionally a password.
- **Never relax the province rule to "QC or unknown" by default.** Ontario is 5 km away;
  `ALLOW_UNKNOWN_PROVINCE` exists so that choice is explicit and visible in the reasons.
- **Never start an unattended watch loop that wasn't asked for.** Subscribing to a PR
  or arming a recurring check spends a turn every time it fires, on a user who isn't
  looking. `enforce-watch-budget.py` caps it; the reasoning is in
  `.claude/rules/engineering.md` ("waiting is not polling").
- **Scraping is subject to both sites' terms of service.** This is built for one
  person's own apartment search, on their own accounts, at human rates. Do not add
  concurrency, proxy rotation, CAPTCHA solving, or user-agent cycling — those change
  what this is.
