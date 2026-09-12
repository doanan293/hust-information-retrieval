import pytest

from hust_crawler.crawl.recovery import validate_policy_migration


def test_policy_migration_allows_only_access_policy_delta() -> None:
    saved = {
        "crawl_strategy": "hybrid-unified",
        "assets": "content-only",
        "robots_txt_obey": True,
        "user_agent_name": "HUSTPublicCrawler",
        "user_agent_version": "1.0",
    }
    effective = {
        "crawl_strategy": "hybrid-unified",
        "assets": "content-only",
        "robots_txt_obey": False,
        "user_agent_name": "HUSTPublicCrawler",
        "user_agent_version": "1.0",
        "access_policy_revision": 2,
        "browser_user_agent": "Mozilla/5.0 Chrome/140.0.0.0",
    }

    validate_policy_migration(saved, effective)

    with pytest.raises(ValueError, match="assets"):
        validate_policy_migration(saved, {**effective, "assets": "all"})


def test_policy_migration_rejects_wrong_revision_or_direction() -> None:
    saved = {"robots_txt_obey": True, "user_agent_name": "HUSTPublicCrawler"}
    effective = {
        "robots_txt_obey": False,
        "access_policy_revision": 2,
        "browser_user_agent": "Mozilla/5.0 Chrome/140.0.0.0",
    }

    with pytest.raises(ValueError, match="revision"):
        validate_policy_migration(saved, {**effective, "access_policy_revision": 3})
    with pytest.raises(ValueError, match="robots_txt_obey"):
        validate_policy_migration(saved, {**effective, "robots_txt_obey": True})
