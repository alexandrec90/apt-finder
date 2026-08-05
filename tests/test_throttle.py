"""Request pacing.

`sleep` and `rng` are injected, so the real decision logic runs without the suite
actually waiting six seconds between assertions — and the jitter is reproducible.
"""

import random

import pytest

from apt_finder.scrapers.throttle import BlockedError, Throttle, ThrottleExhaustedError


@pytest.fixture
def build(settings):
    """(slept, make) — `make()` returns a Throttle whose sleeps land in `slept`."""
    slept: list[float] = []

    def make() -> Throttle:
        # S311: a seeded Mersenne Twister is exactly right here — the test needs
        # reproducible jitter, not cryptographic randomness. Production uses
        # SystemRandom (see Throttle.__init__).
        rng = random.Random(0)  # noqa: S311
        return Throttle(settings, sleep=slept.append, rng=rng)

    return slept, make


def test_first_request_is_not_delayed(build):
    slept, make = build
    throttle = make()
    throttle.wait()
    assert slept == []
    assert throttle.requests == 1


def test_subsequent_requests_are_paced_within_the_configured_range(settings, build):
    slept, make = build
    throttle = make()
    for _ in range(5):
        throttle.wait()
    assert len(slept) == 4
    for delay in slept:
        assert settings.throttle_min_seconds <= delay <= settings.throttle_max_seconds


def test_delays_are_jittered_not_constant(build):
    # Perfectly regular timing is the signature this whole module exists to avoid.
    slept, make = build
    throttle = make()
    for _ in range(10):
        throttle.wait()
    assert len(set(slept)) > 1


def test_long_pause_every_n_requests(settings, build):
    slept, make = build
    settings.throttle_long_pause_every = 3
    throttle = make()
    for _ in range(7):
        throttle.wait()
    assert [d for d in slept if d >= settings.throttle_long_pause_min_seconds], slept


def test_long_pause_disabled_when_zero(settings, build):
    slept, make = build
    settings.throttle_long_pause_every = 0
    throttle = make()
    for _ in range(6):
        throttle.wait()
    assert all(d <= settings.throttle_max_seconds for d in slept)


def test_per_run_budget_is_enforced(settings, build):
    _, make = build
    settings.throttle_max_requests_per_run = 3
    throttle = make()
    for _ in range(3):
        throttle.wait()
    assert throttle.remaining == 0
    with pytest.raises(ThrottleExhaustedError):
        throttle.wait()


def test_remaining_counts_down(settings, build):
    _, make = build
    settings.throttle_max_requests_per_run = 5
    throttle = make()
    throttle.wait()
    assert throttle.remaining == 4


def test_block_backs_off_before_anything_else_is_tried(settings, build):
    slept, make = build
    settings.throttle_block_threshold = 5
    throttle = make()
    throttle.note_block()
    assert slept, "a block must sleep, not merely count"
    assert slept[-1] > settings.throttle_max_seconds


def test_backoff_grows_with_each_block(settings, build):
    slept, make = build
    settings.throttle_block_threshold = 5
    throttle = make()
    throttle.note_block()
    first = slept[-1]
    throttle.note_block()
    assert slept[-1] > first


def test_breaker_trips_at_the_threshold(settings, build):
    _, make = build
    settings.throttle_block_threshold = 2
    throttle = make()
    throttle.note_block()
    with pytest.raises(BlockedError):
        throttle.note_block()


def test_tripped_breaker_refuses_further_requests(settings, build):
    _, make = build
    settings.throttle_block_threshold = 1
    throttle = make()
    with pytest.raises(BlockedError):
        throttle.note_block()
    with pytest.raises(BlockedError):
        throttle.wait()


def test_success_forgives_one_block(settings, build):
    # A single blip in a long healthy run must not trip the breaker an hour later.
    _, make = build
    settings.throttle_block_threshold = 2
    throttle = make()
    throttle.note_block()
    throttle.note_success()
    assert throttle.blocks == 0
    throttle.note_block()  # would raise if the earlier block still counted


def test_success_does_not_go_negative(build):
    _, make = build
    throttle = make()
    throttle.note_success()
    throttle.note_success()
    assert throttle.blocks == 0


def test_slept_seconds_is_tracked(build):
    _, make = build
    throttle = make()
    for _ in range(4):
        throttle.wait()
    assert throttle.slept_seconds > 0
