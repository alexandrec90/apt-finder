"""Request pacing, because the account matters more than the data.

The failure this module exists to prevent is not a slow scrape — it is a banned
Facebook account, which cannot be undone and takes the search with it. Every knob here
is therefore biased toward "too slow", and the defaults finish a full Gatineau sweep in
roughly twenty minutes rather than twenty seconds.

Three behaviours, each aimed at a specific detection signal:

- **Jittered intervals.** A request every 10.0 seconds for 200 requests is a stronger
  bot signature than one every 2 seconds — perfectly regular timing is the thing that
  is trivially easy to detect and impossible for a human to produce. Intervals are
  sampled uniformly from a range instead.
- **Occasional long pauses.** Real people open a listing, read it, get distracted. A
  session with no gap longer than 18 seconds does not look like browsing, so every
  ``throttle_long_pause_every`` requests we take a much longer one.
- **A hard per-run cap and a circuit breaker.** The realistic way this project gets
  someone banned is a pagination bug looping forever, not the steady-state rate. The
  cap bounds the damage of a bug nobody has found yet; the breaker aborts the whole run
  after a couple of 429/403/captcha responses rather than hammering a site that has
  already said no.

``sleep`` and ``rng`` are injected so the tests exercise the real decision logic
without spending real seconds.
"""

import random
import time
from collections.abc import Callable

from apt_finder.config import Settings

__all__ = ["BlockedError", "Throttle", "ThrottleExhaustedError"]


class ThrottleExhaustedError(RuntimeError):
    """The per-run request budget is spent. Stop cleanly and keep what was collected."""


class BlockedError(RuntimeError):
    """The platform pushed back (429/403/captcha) enough times to abort the run."""


class Throttle:
    """Paces requests to one platform for the duration of one run."""

    def __init__(
        self,
        settings: Settings,
        *,
        name: str = "",
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.name = name
        self._sleep = sleep
        # Not `random` module-level: a seeded Random keeps tests deterministic without
        # reseeding the global generator out from under anything else in the process.
        self._rng = rng or random.SystemRandom()
        self.requests = 0
        self.blocks = 0
        self.slept_seconds = 0.0

    @property
    def remaining(self) -> int:
        return max(0, self.settings.throttle_max_requests_per_run - self.requests)

    def _pause(self, seconds: float) -> None:
        self.slept_seconds += seconds
        self._sleep(seconds)

    def wait(self) -> None:
        """Block until the next request is allowed. Call immediately before each fetch.

        Raises :class:`ThrottleExhaustedError` when the run's budget is gone, and
        :class:`BlockedError` if the breaker has already tripped.
        """
        if self.blocks >= self.settings.throttle_block_threshold:
            raise BlockedError(f"{self.name}: {self.blocks} block responses — aborting run")
        if self.requests >= self.settings.throttle_max_requests_per_run:
            raise ThrottleExhaustedError(
                f"{self.name}: per-run cap of "
                f"{self.settings.throttle_max_requests_per_run} requests reached"
            )

        # The first request of a run is not delayed: there is nothing to pace against
        # yet, and an unconditional opening sleep only makes the tool annoying to use.
        if self.requests:
            every = self.settings.throttle_long_pause_every
            if every > 0 and self.requests % every == 0:
                self._pause(
                    self._rng.uniform(
                        self.settings.throttle_long_pause_min_seconds,
                        self.settings.throttle_long_pause_max_seconds,
                    )
                )
            else:
                self._pause(
                    self._rng.uniform(
                        self.settings.throttle_min_seconds,
                        self.settings.throttle_max_seconds,
                    )
                )
        self.requests += 1

    def note_block(self) -> None:
        """Record a 429/403/captcha and back off hard before anything else is tried.

        The backoff is exponential in the number of blocks so far, and happens *here*
        rather than being left to the caller — a caller that forgets is exactly how a
        soft rate-limit turns into a ban.
        """
        self.blocks += 1
        self._pause(self.settings.throttle_max_seconds * (4**self.blocks))
        if self.blocks >= self.settings.throttle_block_threshold:
            raise BlockedError(f"{self.name}: {self.blocks} block responses — aborting run")

    def note_success(self) -> None:
        """A clean response. Forgives one earlier block so a single blip cannot, on its
        own, trip the breaker later in a long and otherwise healthy run."""
        self.blocks = max(0, self.blocks - 1)
