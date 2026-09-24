from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlsplit

from scrapy import signals
from scrapy.http import Request, Response

from ..policies.access import classify_access
from .resources import ResourceSampler


@dataclass(frozen=True, slots=True)
class Observation:
    at: float
    latency: float | None
    status: int | None
    error: str | None
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class HostDecision:
    concurrency: int
    cooldown_until: float | None


class HostHealth:
    def __init__(
        self,
        *,
        start_concurrency: int,
        normal_ceiling: int,
        hard_ceiling: int,
        window_size: int = 20,
        change_interval: float = 30.0,
    ) -> None:
        if not 1 <= start_concurrency <= hard_ceiling:
            raise ValueError("start concurrency must be within hard ceiling")
        self.current = start_concurrency
        self.normal_ceiling = min(normal_ceiling, hard_ceiling)
        self.hard_ceiling = hard_ceiling
        self.window_size = window_size
        self.change_interval = change_interval
        self._observations: deque[Observation] = deque(maxlen=window_size)
        self._cooldown_until: float | None = None
        self._last_change = float("-inf")
        self._rate_limit_streak = 0
        self._forbidden_streak = 0

    def observe(self, observation: Observation) -> None:
        self._observations.append(observation)
        if observation.status == 429:
            self.current = 1
            self._rate_limit_streak += 1
            retry_after = observation.retry_after_seconds
            if retry_after is None:
                retry_after = min(120.0, 30.0 * (2 ** (self._rate_limit_streak - 1)))
            retry_after = max(0.0, retry_after)
            self._cooldown_until = observation.at + retry_after
            self._last_change = observation.at
            self._forbidden_streak = 0
        elif observation.error == "captcha":
            self.current = 1
            self._rate_limit_streak = 0
            self._forbidden_streak = 0
            self._cooldown_until = observation.at + 300.0
            self._last_change = observation.at
        elif observation.status == 403:
            self._rate_limit_streak = 0
            self._forbidden_streak += 1
            if self._forbidden_streak >= 3:
                self.current = 1
                self._cooldown_until = observation.at + 300.0
                self._last_change = observation.at
        else:
            self._rate_limit_streak = 0
            self._forbidden_streak = 0
            if (
                observation.error in {"timeout", "transport"} or observation.status in {503, 504}
            ) and observation.at - self._last_change >= self.change_interval:
                new_value = max(1, self.current // 2)
                if new_value != self.current:
                    self.current = new_value
                    self._last_change = observation.at

    def recommend(self, now: float) -> HostDecision:
        if self._cooldown_until is not None and now < self._cooldown_until:
            return HostDecision(1, self._cooldown_until)
        healthy = len(self._observations) >= self.window_size and all(
            observation.error is None
            and observation.status is not None
            and 200 <= observation.status < 400
            for observation in self._observations
        )
        if healthy and now - self._last_change >= self.change_interval:
            self.current = min(self.hard_ceiling, self.current + 1)
            self._last_change = now
        cooldown = self._cooldown_until if self._cooldown_until is not None and now < self._cooldown_until else None
        return HostDecision(min(self.current, self.hard_ceiling), cooldown)


class AdaptiveConcurrencyMiddleware:
    """Feed response health back into Scrapy's existing downloader slots."""

    def __init__(
        self,
        crawler,
        *,
        max_per_host: int,
        max_concurrency: int,
        resource_limit_percent: int = 80,
        resource_sampler: ResourceSampler | None = None,
    ) -> None:
        self._crawler = crawler
        self._max_per_host = max_per_host
        self._max_concurrency = max_concurrency
        self._resource_limit_percent = resource_limit_percent
        self._resource_sampler = resource_sampler or ResourceSampler()
        self._resource_pressure = False
        self._last_resource_sample = None
        self._hosts: dict[str, HostHealth] = {}
        self._started: dict[int, float] = {}
        self._slot_delays: dict[str, float] = {}
        self._signal_observed_responses: set[int] = set()

    @classmethod
    def from_crawler(cls, crawler):
        tuning = crawler.settings.get("HUST_TUNING")
        if tuning is None:
            raise ValueError("HUST_TUNING is required for adaptive middleware")
        middleware = cls(
            crawler,
            max_per_host=int(tuning.max_per_host),
            max_concurrency=int(tuning.max_concurrency),
            resource_limit_percent=int(getattr(tuning, "resource_limit_percent", 80)),
        )
        crawler.signals.connect(
            middleware._response_downloaded,
            signal=signals.response_downloaded,
        )
        return middleware

    @staticmethod
    def _host(request: Request) -> str:
        return (urlsplit(request.url).hostname or "").lower().rstrip(".")

    def _health(self, host: str, request: Request | None = None) -> HostHealth:
        health = self._hosts.get(host)
        if health is None:
            slot = None
            if request is not None:
                slot_key = request.meta.get("download_slot", host)
                slot = self._crawler.engine.downloader.slots.get(slot_key)
            start = int(getattr(slot, "concurrency", 1) or 1)
            health = self._hosts[host] = HostHealth(
                start_concurrency=min(start, self._max_per_host),
                normal_ceiling=min(2, self._max_per_host),
                hard_ceiling=self._max_per_host,
            )
        return health

    def process_request(self, request: Request, spider=None):
        self._started[id(request)] = monotonic()
        return None

    def _observe_response(self, request: Request, response: Response) -> None:
        started = self._started.pop(id(request), monotonic())
        retry_after = None
        raw_retry_after = response.headers.get(b"Retry-After")
        if raw_retry_after:
            try:
                retry_after = max(0.0, float(raw_retry_after.decode("latin1").strip()))
            except ValueError:
                retry_after = None
        error = None
        content_type = response.headers.get(b"Content-Type", b"").decode("latin1").lower()
        if "html" in content_type and hasattr(response, "text"):
            access = classify_access(
                status=response.status,
                url=response.url,
                html=response.text,
                rendered=bool(request.meta.get("rendered")),
            )
            if access.outcome == "captcha_blocked":
                error = "captcha"
        self.observe(
            request,
            response.status,
            max(0.0, monotonic() - started),
            error,
            retry_after,
        )
        self._update_resource_pressure()
        self.reconcile_host(self._host(request), request)

    def _response_downloaded(self, request: Request, response: Response, spider=None) -> None:
        self._observe_response(request, response)
        self._signal_observed_responses.add(id(response))

    def process_response(self, request: Request, response: Response):
        response_id = id(response)
        if response_id in self._signal_observed_responses:
            self._signal_observed_responses.remove(response_id)
        else:
            self._observe_response(request, response)
        return response

    def process_exception(self, request: Request, exception):
        started = self._started.pop(id(request), monotonic())
        error = "timeout" if "timeout" in exception.__class__.__name__.lower() else "transport"
        self.observe(request, None, max(0.0, monotonic() - started), error)
        self._update_resource_pressure()
        self.reconcile_host(self._host(request), request)
        return None

    def observe(
        self,
        request: Request,
        status: int | None,
        latency: float | None,
        error: str | None,
        retry_after_seconds: float | None = None,
    ) -> None:
        host = self._host(request)
        health = self._health(host, request)
        health.observe(Observation(
            at=monotonic(), latency=latency, status=status, error=error,
            retry_after_seconds=retry_after_seconds,
        ))
        stats = getattr(self._crawler, "stats", None)
        if stats is not None and hasattr(stats, "inc_value"):
            if status == 429:
                stats.inc_value(f"hust/host/{host}/429")
            elif status in {503, 504}:
                stats.inc_value(f"hust/host/{host}/5xx")
            elif error:
                stats.inc_value(f"hust/host/{host}/{error}")

    def reconcile_host(self, host: str, request: Request | None = None, *, now: float | None = None) -> None:
        health = self._health(host, request)
        current_time = monotonic() if now is None else now
        decision = health.recommend(current_time)
        if self._resource_pressure:
            decision = HostDecision(1, decision.cooldown_until)

        slots = self._crawler.engine.downloader.slots
        slot_keys = {host, f"playwright:{host}"}
        if request is not None:
            slot_keys.add(str(request.meta.get("download_slot", host)))
        for slot_key in slot_keys:
            slot = slots.get(slot_key)
            if slot is None:
                continue
            ceiling = min(2, self._max_per_host) if slot_key.startswith("playwright:") else self._max_per_host
            slot.concurrency = min(ceiling, max(1, decision.concurrency))
            if decision.cooldown_until is not None:
                baseline = self._slot_delays.setdefault(slot_key, float(slot.delay))
                remaining = max(0.0, decision.cooldown_until - current_time)
                slot.delay = max(baseline, remaining)
            elif slot_key in self._slot_delays:
                slot.delay = self._slot_delays.pop(slot_key)

    def reconcile(self, *, now: float | None = None) -> None:
        self._update_resource_pressure()
        for host in list(self._hosts):
            self.reconcile_host(host, now=now)

    def _update_resource_pressure(self) -> None:
        self._last_resource_sample = self._resource_sampler.sample()
        sample = self._last_resource_sample
        self._resource_pressure = any(
            value is not None and value >= self._resource_limit_percent
            for value in (sample.cpu_percent, sample.ram_percent)
        )
