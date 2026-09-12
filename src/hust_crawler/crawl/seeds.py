from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from hust_crawler.config import normalize_hostname
from hust_crawler.policies.url import canonicalize_url

SeedMode = Literal["recursive", "exact"]


@dataclass(frozen=True, slots=True)
class InvalidSeed:
    line: int
    value: str
    reason: str


@dataclass(frozen=True, slots=True)
class SeedSet:
    recursive_hostnames: frozenset[str]
    exact_urls: tuple[str, ...]
    invalid: tuple[InvalidSeed, ...]

    @property
    def allowed_hostnames(self) -> frozenset[str]:
        exact_hosts = {urlsplit(url).hostname for url in self.exact_urls}
        return self.recursive_hostnames | frozenset(host for host in exact_hosts if host)

    def mode_for(self, url: str) -> SeedMode | None:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if host in self.recursive_hostnames:
            return "recursive"
        return "exact" if url in self.exact_urls else None


def parse_seed_file(path: Path) -> SeedSet:
    recursive_hosts: set[str] = set()
    exact_urls: list[str] = []
    invalid_seeds: list[InvalidSeed] = []

    content = path.read_text(encoding="utf-8")
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue

        parts = urlsplit(value)
        if parts.scheme:
            if parts.scheme.lower() not in {"http", "https"}:
                invalid_seeds.append(InvalidSeed(line_number, value, "unsupported_scheme"))
                continue
            canonical, _, reason = canonicalize_url(value)
            if canonical:
                exact_urls.append(canonical)
            else:
                invalid_seeds.append(InvalidSeed(line_number, value, reason or "invalid_url"))
        else:
            try:
                host = normalize_hostname(value)
                recursive_hosts.add(host)
            except ValueError:
                invalid_seeds.append(InvalidSeed(line_number, value, "invalid_hostname"))

    sorted_exact = tuple(sorted(dict.fromkeys(exact_urls)))
    rec_frozen = frozenset(recursive_hosts)

    if not rec_frozen and not sorted_exact:
        raise ValueError("input contains no valid seeds")

    return SeedSet(
        recursive_hostnames=rec_frozen,
        exact_urls=sorted_exact,
        invalid=tuple(invalid_seeds),
    )
