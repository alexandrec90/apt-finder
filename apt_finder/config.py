"""Application settings — search criteria, throttling, and credentials.

**On the data-lake seam.** This class deliberately does *not* satisfy
``data_lake.settings.LakeSettings``. That Protocol describes market-data provider
credentials (Finnhub, Alpha Vantage, IBKR…) this project has no use for, and
implementing it would mean carrying eight dead fields to satisfy a type.

We consume the package's *database* seam only — its declarative ``Base``, its
``Connector`` contract, and ``data_lake.configure(session_factory=...)``. Scrapers read
their knobs from this object through ``self.config``; ``Connector.settings`` is left
unconfigured on purpose, so anything that reaches for a provider credential raises a
``RuntimeError`` naming the fix instead of silently finding one. See
:mod:`apt_finder.runtime`.
"""

from decimal import Decimal
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: URL-list fields are `Annotated[..., NoDecode]` with a comma-splitting validator.
#: Without `NoDecode`, pydantic-settings JSON-decodes any complex field before
#: validation, which forces `.env` to hold a quoted JSON array — awkward to write, and
#: flagged by dotenv-linter's QuoteCharacter rule. Comma-separated is what a person
#: would type anyway, and no search URL we use contains a comma.
UrlList = Annotated[list[str], NoDecode]

#: Hull sector, Gatineau — the reference point for the search radius.
GATINEAU_LAT = 45.4765
GATINEAU_LON = -75.7013


class Settings(BaseSettings):
    """Everything tunable, sourced from `.env` (see `.env.example`)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The local-dev compose default, byte-identical to `POSTGRES_PASSWORD`'s fallback in
    # `docker-compose.yml` and to `DATABASE_URL` in `.env.example`. It reaches loopback
    # only, and any real deployment overrides it from `.env` — so it is a placeholder in
    # the same sense `.env.example` is, and that file is excluded from the scan outright.
    database_url: str = "postgresql+psycopg://apt_finder:apt_finder_local_dev@127.0.0.1:5438/apt_finder"  # pragma: allowlist secret

    # ------------------------------------------------------------------
    # Search criteria
    # ------------------------------------------------------------------
    max_rent_cad: Decimal = Decimal("2000")
    #: Reject anything cheaper than this. A $1/mo or $0 listing is a placeholder or a
    #: scam, not a bargain, and it pollutes the match list.
    min_rent_cad: Decimal = Decimal("400")
    require_washer: bool = True
    require_dryer: bool = True
    #: Fridge + stove. Kept separate from the laundry pair because Québec listings
    #: advertise them very differently ("électros inclus" vs "laveuse-sécheuse").
    require_kitchen_appliances: bool = True
    #: A room / colocation is never a match, whatever else it offers.
    exclude_rooms: bool = True

    # ------------------------------------------------------------------
    # Geography — Gatineau, Québec. Ontario is across the river and must not leak in.
    # ------------------------------------------------------------------
    centre_lat: float = GATINEAU_LAT
    centre_lon: float = GATINEAU_LON
    radius_km: float = 40.0
    #: Fail closed: a listing whose province cannot be established is rejected rather
    #: than assumed local. Ottawa is 5 km away, so "unknown" is far more likely to be
    #: Ontario than a missed Gatineau unit.
    allow_unknown_province: bool = False

    # ------------------------------------------------------------------
    # Kijiji
    # ------------------------------------------------------------------
    #: Search result pages to walk. Defaults to Kijiji's Gatineau apartment/condo
    #: category. VERIFY these against the live site before a first real run — Kijiji
    #: renumbers category/location ids occasionally, and a stale id returns an
    #: unrelated (or empty) result set rather than an error.
    kijiji_search_urls: UrlList = Field(
        default_factory=lambda: [
            "https://www.kijiji.ca/b-appartement-condo/gatineau/c37l1700242",
        ]
    )
    kijiji_max_pages: int = 3
    #: Optional. Paste the `Cookie:` header from a logged-in browser session. Kijiji
    #: search works signed-out, so this is only needed if you hit a login wall.
    kijiji_cookie: str = ""

    # ------------------------------------------------------------------
    # Facebook Marketplace
    # ------------------------------------------------------------------
    facebook_search_urls: UrlList = Field(
        default_factory=lambda: [
            "https://www.facebook.com/marketplace/gatineau/propertyrentals"
            "?minPrice=400&maxPrice=2000&exact=false",
        ]
    )
    #: Persistent Playwright profile. You log in once, interactively, via
    #: `apt-finder facebook-login`; every later run reuses the cookies from here.
    #: Credentials are never stored by this project.
    facebook_profile_dir: str = ".local/facebook-profile"
    facebook_headless: bool = True
    facebook_max_listings: int = 60

    # ------------------------------------------------------------------
    # Throttling — the whole point is not getting the account banned.
    # ------------------------------------------------------------------
    #: Seconds between requests to the same platform, sampled uniformly in this range.
    throttle_min_seconds: float = 6.0
    throttle_max_seconds: float = 18.0
    #: Every N requests, take a longer "reading" pause in this range. Steady 10s
    #: intervals for 200 requests is a far more obvious bot signature than a slow,
    #: irregular one.
    throttle_long_pause_every: int = 12
    throttle_long_pause_min_seconds: float = 45.0
    throttle_long_pause_max_seconds: float = 120.0
    #: Hard ceiling on requests per platform per run. A runaway pagination loop is the
    #: failure mode that gets an account flagged in one afternoon.
    throttle_max_requests_per_run: int = 120
    #: Consecutive blocks (429/403/captcha) that abort the run entirely.
    throttle_block_threshold: int = 2

    @field_validator("kijiji_search_urls", "facebook_search_urls", mode="before")
    @classmethod
    def _split_urls(cls, value: object) -> object:
        """Accept `a,b` from the environment; leave real lists (and defaults) alone."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    http_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings. Cached, so `.env` is read once."""
    return Settings()
