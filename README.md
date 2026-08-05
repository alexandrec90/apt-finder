# Apt Finder

Finds full apartments to rent in and around **Gatineau, Québec** on Kijiji and Facebook
Marketplace: under **$2,000/month**, **appliances included** (washer and dryer
specifically), **not a room**. Most listings are in French, and the parsers are built
for that.

Generated from [devkit](https://github.com/alexandrec90/devkit)'s project
template. The agent harness in `scripts/hooks/` is vendored from there — see
`CLAUDE.md`, "Vendored agent harness".

## Quick start

```bash
cp .env.example .env          # then fill in the placeholders
uv sync --all-extras          # creates .venv from the committed uv.lock
# no uv? pip install -e ".[dev]" works, but resolves fresh instead of from the lock
docker compose up -d
alembic upgrade head
uv run pytest
```

Then, for Facebook only, install the browser and sign in once:

```bash
uv run playwright install chromium
uv run apt-finder facebook-login   # a window opens; sign in yourself, then close it
```

Your password is never asked for or stored. Playwright keeps the resulting session
cookies in `.local/facebook-profile/` (gitignored), and later runs reuse them headlessly.

## Using it

```bash
uv run apt-finder scrape                  # both sources, throttled, into Postgres
uv run apt-finder scrape --source kijiji  # Kijiji needs no browser
uv run apt-finder list                    # what matched
uv run apt-finder list --verdict maybe    # what needs your own eyes
uv run apt-finder inspect kijiji          # dump what the parser saw, for drift triage
```

**A full sweep takes about twenty minutes.** That is deliberate. Requests are jittered
rather than issued at a fixed rate, a longer pause happens every dozen requests, each run
has a hard request cap, and two rate-limit responses abort the run. A banned account
cannot be undone and takes the whole search with it, so every default here is biased
toward "too slow". Tune them in `.env` if you must — but read
`apt_finder/scrapers/throttle.py` first.

### Three verdicts, not two

`match` means every criterion was met. `reject` means the listing *contradicted* one —
it said "buanderie commune", or it is in Ontario. `maybe` means it simply never said:
most short ads never mention a dryer, and roughly half of Marketplace posts carry no
usable location. Filtering those out strictly gives you a very clean, very empty list, so
they land in `maybe` for a 20-second human glance. Every verdict stores its reason codes.

### Before the first real run

Verify the search URLs in `.env`. Kijiji's category/location ids (`c37l1700242`) and
Facebook's Marketplace path drift, and a stale one returns an unrelated result set rather
than an error. `apt-finder inspect <source>` shows you what is actually being parsed.

In VS Code, `Ctrl+Shift+B` runs the default build task and the task quick-pick
(`Ctrl+Shift+P` → "Run Task") lists everything else, each with a one-line `detail`
explaining what it costs and what it touches.

## Host ports

This checkout is **slot 6** in devkit's `ports.toml`. Every published port
is its conventional base plus the slot:

| Service | Host port |
| --- | --- |
| app | 8006 |
| postgres | 5438 |

Regenerate the `.env` block for any checkout with
`python <devkit>/scripts/devkit_ports.py <checkout-name>`.

## Parallel worktrees

A second checkout runs its own stack side by side:

```bash
git worktree add ../apt-finder-b apt-finder-b
cd ../apt-finder-b
cp ../apt-finder/.env .env    # then replace the ports block — slot 7
python <devkit>/scripts/devkit_ports.py apt-finder-b
docker compose up -d
```

`COMPOSE_PROJECT_NAME` must equal the directory name in each. See `CLAUDE.md`,
"Parallel worktrees", for the rules that keep the two stacks independent.

## Layout

```text
apt_finder/                  application code
tests/                tests
scripts/                 project scripts (Python, each with tests)
scripts/hooks/           vendored agent harness — edit upstream in devkit
.devkit.toml      the per-project harness seam (NOT vendored)
docker-compose.yml       the local stack
```

## CI

`.github/workflows/pr-gate.yml` runs lint, tests, and the harness drift check on
every PR. The drift check is only meaningful when it can see devkit — if it prints
"nothing to do (skipping)", the gate is inert and the wiring needs fixing.

`.github/dependabot.yml` opens weekly dependency PRs, and
`.github/workflows/dependabot-automerge.yml` merges them once the gate passes —
patch/minor bumps of anything, plus majors confined to dev tooling. A major that
touches a runtime dependency is labelled `needs-manual-merge` and waits for you.
