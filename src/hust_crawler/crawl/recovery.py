from __future__ import annotations

from collections.abc import Mapping


POLICY_RETRY_REASONS = frozenset(
    {"robots_disallowed", "login_required", "captcha_blocked", "access_denied"}
)


ACCESS_POLICY_SEMANTIC_KEYS = frozenset(
    {
        "access_policy_revision",
        "browser_user_agent",
        "robots_txt_obey",
        "user_agent_name",
        "user_agent_version",
    }
)


def validate_policy_migration(
    saved: Mapping[str, object], effective: Mapping[str, object]
) -> None:
    saved_revision = int(saved.get("access_policy_revision", 1))
    effective_revision = int(effective.get("access_policy_revision", 1))
    if saved_revision != 1 or effective_revision != 2:
        raise ValueError(
            "unsupported access policy revision migration: "
            f"{saved_revision} -> {effective_revision}"
        )
    if saved.get("robots_txt_obey", True) is not True:
        raise ValueError("policy migration requires saved robots_txt_obey=true")
    if effective.get("robots_txt_obey") is not False:
        raise ValueError("policy migration requires effective robots_txt_obey=false")

    saved_other = {
        key: value for key, value in saved.items() if key not in ACCESS_POLICY_SEMANTIC_KEYS
    }
    effective_other = {
        key: value for key, value in effective.items() if key not in ACCESS_POLICY_SEMANTIC_KEYS
    }
    if saved_other != effective_other:
        changed = sorted(
            key
            for key in set(saved_other) | set(effective_other)
            if saved_other.get(key) != effective_other.get(key)
        )
        raise ValueError(
            "resume semantic configuration differs outside access policy: "
            + ", ".join(changed)
        )


def scheduled_retry(record: Mapping[str, object]) -> dict[str, object]:
    retry = dict(record)
    retry.update({"status": "scheduled", "frontier_action": "scheduled"})
    for key in ("completed", "final_url", "http_status", "reason"):
        retry.pop(key, None)
    return retry
