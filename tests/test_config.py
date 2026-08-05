"""Settings loading, and the seam that keeps this project's config out of the lake's."""

from decimal import Decimal

import pytest

from apt_finder.config import Settings


def build(**env) -> Settings:
    # `_env_file=None` keeps a developer's real `.env` out of the run.
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_defaults_match_the_brief(settings):
    assert settings.max_rent_cad == Decimal("2000")
    assert settings.require_washer is True
    assert settings.require_dryer is True
    assert settings.exclude_rooms is True
    # Fail closed on province: Ottawa is across the river.
    assert settings.allow_unknown_province is False


def test_search_urls_accept_a_comma_separated_string():
    # `.env` holds plain text, not a quoted JSON array — see the UrlList note in
    # config.py and dotenv-linter's QuoteCharacter rule.
    settings = build(kijiji_search_urls="https://a.example,https://b.example")
    assert settings.kijiji_search_urls == ["https://a.example", "https://b.example"]


def test_search_urls_strip_whitespace_and_drop_blanks():
    settings = build(facebook_search_urls=" https://a.example , , https://b.example ")
    assert settings.facebook_search_urls == ["https://a.example", "https://b.example"]


def test_single_url_without_commas():
    settings = build(kijiji_search_urls="https://only.example")
    assert settings.kijiji_search_urls == ["https://only.example"]


def test_a_real_list_passes_through():
    settings = build(kijiji_search_urls=["https://a.example"])
    assert settings.kijiji_search_urls == ["https://a.example"]


def test_defaults_are_populated_when_unset(settings):
    assert settings.kijiji_search_urls
    assert settings.facebook_search_urls
    assert all(url.startswith("https://") for url in settings.kijiji_search_urls)


def test_url_with_a_query_string_survives():
    # The Facebook default carries `?minPrice=400&maxPrice=2000`; splitting on the
    # wrong character would quietly truncate it.
    url = "https://www.facebook.com/marketplace/gatineau/propertyrentals?a=1&b=2"
    assert build(facebook_search_urls=url).facebook_search_urls == [url]


def test_rent_is_decimal_not_float():
    # Money must never enter binary floating point.
    assert isinstance(build(max_rent_cad="1875.50").max_rent_cad, Decimal)
    assert build(max_rent_cad="1875.50").max_rent_cad == Decimal("1875.50")


def test_does_not_satisfy_the_lake_settings_protocol(settings):
    """The seam, asserted rather than merely documented.

    `data_lake.settings.LakeSettings` describes market-data provider credentials that
    nothing here reads. This project supplies the lake with a session factory only, so
    a reach for `Connector.settings` raises instead of silently finding a credential.
    If someone later makes `Settings` satisfy the Protocol, that decision should be
    deliberate — and this test is where they will notice.
    """
    from data_lake.settings import LakeSettings

    assert not isinstance(settings, LakeSettings)


def test_configure_lake_sets_only_the_session_factory():
    import data_lake

    from apt_finder.runtime import configure_lake

    data_lake.reset()
    configure_lake()
    # The session factory resolves...
    assert data_lake.runtime.resolve_session_factory(None) is not None
    # ...and settings deliberately do not.
    with pytest.raises(RuntimeError, match="settings"):
        data_lake.runtime.resolve_settings(None)
    data_lake.reset()
