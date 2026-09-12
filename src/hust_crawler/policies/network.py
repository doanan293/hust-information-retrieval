from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import socket
import threading
from urllib.parse import urlsplit

from .url import canonicalize_url


class NonPublicAddressError(OSError):
    """Raised when a hostname resolves to private, loopback, or non-global IP addresses."""


@dataclass(frozen=True, slots=True)
class NetworkDecision:
    accepted: bool
    canonical_url: str | None
    hostname: str | None
    reason: str | None


def is_public_address(value: str) -> bool:
    return ipaddress.ip_address(value).is_global


def validate_public_web_url(url: str) -> NetworkDecision:
    raw = urlsplit(url)
    if raw.scheme not in {"http", "https"}:
        return NetworkDecision(False, None, raw.hostname, "unsupported_scheme")
    if raw.username is not None or raw.password is not None:
        return NetworkDecision(False, None, raw.hostname, "credentials_not_allowed")
    canonical, hostname, reason = canonicalize_url(url)
    if canonical is None or hostname is None:
        return NetworkDecision(False, None, None, reason or "malformed_url")
    try:
        if not is_public_address(hostname):
            return NetworkDecision(False, canonical, hostname, "non_public_address")
    except ValueError:
        pass
    parsed = urlsplit(canonical)
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port not in {None, default_port}:
        return NetworkDecision(False, canonical, hostname, "non_default_port")
    return NetworkDecision(True, canonical, hostname, None)


def resolve_hostname(hostname: str, timeout: float = 5.0, port: int = 443) -> None:
    outcome: list[tuple[list[tuple], BaseException | None]] = []
    completed = threading.Event()

    def resolve() -> None:
        try:
            answers = socket.getaddrinfo(hostname, port, 0, socket.SOCK_STREAM)
            outcome.append((answers, None))
        except BaseException as exc:
            outcome.append(([], exc))
        finally:
            completed.set()

    threading.Thread(target=resolve, daemon=True).start()
    if not completed.wait(timeout):
        raise TimeoutError(f"DNS resolution timed out after {timeout:g}s")
    answers, error = outcome[0]
    if error is not None:
        raise error
    addresses = {answer[4][0].partition("%")[0] for answer in answers}
    non_public = sorted(
        address for address in addresses if not ipaddress.ip_address(address).is_global
    )
    if non_public:
        raise NonPublicAddressError(
            f"hostname resolves to non-public address: {', '.join(non_public)}"
        )


async def resolve_public_hostname(
    hostname: str, port: int = 443, timeout_seconds: float = 5.0
) -> None:
    await asyncio.to_thread(resolve_hostname, hostname, timeout_seconds, port)
