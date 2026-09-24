from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import sys
import time
from typing import Callable, TextIO


@dataclass(slots=True)
class EtaSample:
    at: float
    completed: int
    queued: int
    discovered: int


class EtaEstimator:
    def __init__(self, stable_samples: int = 3) -> None:
        self.stable_samples = stable_samples
        self.samples: deque[EtaSample] = deque(maxlen=stable_samples * 2)

    def observe(self, at: float, completed: int, queued: int, discovered: int) -> str:
        self.samples.append(EtaSample(at, completed, queued, discovered))
        return self.render()

    def render(self) -> str:
        if len(self.samples) < self.stable_samples:
            return "unknown (discovering)"

        window = list(self.samples)[-self.stable_samples:]
        if window[-1].discovered > window[0].discovered:
            return "unknown (discovering)"

        latest = window[-1]
        earliest = window[0]
        delta_completed = latest.completed - earliest.completed
        delta_time = latest.at - earliest.at

        if latest.queued == 0:
            return "0s"

        if delta_time <= 0 or delta_completed <= 0:
            return "unknown (idle)"

        recent_rate = delta_completed / delta_time
        remaining_seconds = latest.queued / recent_rate
        if remaining_seconds <= 0:
            return "0s"
        if remaining_seconds < 60:
            return f"{int(remaining_seconds)}s"
        if remaining_seconds < 3600:
            minutes = int(remaining_seconds // 60)
            seconds = int(remaining_seconds % 60)
            return f"{minutes}m {seconds}s"
        hours = int(remaining_seconds // 3600)
        minutes = int((remaining_seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


class CrawlProgressReporter:
    def __init__(
        self,
        crawler,
        *,
        output: TextIO = sys.stderr,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.crawler = crawler
        self.output = output
        self.clock = clock
        self.started = clock()
        self.eta_estimator = EtaEstimator(stable_samples=3)
        self._peak_queue: int = 0
        self._recent_rate: float = 0.0

    @classmethod
    def from_crawler(cls, crawler):
        reporter = cls(crawler)
        crawler.progress_reporter = reporter
        tuning = crawler.settings.get("HUST_TUNING")
        interval = float(getattr(tuning, "progress_interval_seconds", 0.0) or 0.0)
        if interval > 0:
            from twisted.internet.task import LoopingCall
            from scrapy import signals

            reporter._loop = LoopingCall(reporter.report)
            reporter._loop.start(interval, now=False)
            crawler.signals.connect(reporter.close, signal=signals.spider_closed)
        return reporter

    def close(self, *args) -> None:
        loop = getattr(self, "_loop", None)
        if loop is not None and loop.running:
            loop.stop()

    def _get(self, key: str, default=0):
        stats = self.crawler.stats
        getter = getattr(stats, "get_value", None)
        return getter(key, default) if getter else stats.get(key, default)

    def metrics(self) -> dict[str, object]:
        if self._peak_queue == 0 and self._recent_rate == 0.0:
            enqueued = int(self._get("scheduler/enqueued", 0))
            dequeued = int(self._get("scheduler/dequeued", 0))
            self._peak_queue = max(0, enqueued - dequeued)
            elapsed = max(0.001, self.clock() - self.started)
            done = int(self._get("response_received_count", 0))
            self._recent_rate = done / elapsed
        return {
            "peak_queue": self._peak_queue,
            "recent_rate": self._recent_rate,
        }

    def report(self) -> str:
        elapsed = max(0.001, self.clock() - self.started)
        done = int(self._get("response_received_count", 0))
        enqueued = int(self._get("scheduler/enqueued", 0))
        dequeued = int(self._get("scheduler/dequeued", 0))
        queued = max(0, enqueued - dequeued)
        rate = done / elapsed
        if queued > self._peak_queue:
            self._peak_queue = queued
        self._recent_rate = rate

        eta = self.eta_estimator.observe(
            at=self.clock(),
            completed=done,
            queued=queued,
            discovered=enqueued,
        )

        cpu = self._get("hust/resources/cpu_percent", None)
        ram = self._get("hust/resources/ram_percent", None)

        line = (
            f"crawl | done={done} | queued={queued}"
            f" | rate={rate:.1f} url/s"
        )
        if cpu is not None:
            line += f" | cpu={float(cpu):.0f}%"
        if ram is not None:
            line += f" | ram={float(ram):.0f}%"
        line += f" | eta={eta}"

        print(line, file=self.output, flush=True)
        downloader = getattr(getattr(self.crawler, "engine", None), "downloader", None)
        if downloader is not None:
            slots = downloader.slots
            transferring = sum(len(slot.transferring) for slot in slots.values())
            waiting = sum(len(slot.queue) for slot in slots.values())
            active = len(downloader.active)
            print(
                f"download | active={active} | transferring={transferring}"
                f" | slot_waiting={waiting}"
                f" | outside_slots={max(0, active - sum(len(s.active) for s in slots.values()))}"
                f" | retries={self._get('hust/retry/count', 0)}"
                f" | retry_waiting={self._get('hust/retry/waiting', 0)}"
                f" | exceptions={self._get('downloader/exception_count', 0)}",
                file=self.output, flush=True,
            )
            for key, slot in slots.items():
                if not slot.active:
                    continue
                print(
                    f"slot | host={key} | active={len(slot.active)}"
                    f" | transferring={len(slot.transferring)} | queued={len(slot.queue)}"
                    f" | concurrency={slot.concurrency} | delay={slot.delay:.2f}s",
                    file=self.output, flush=True,
                )
        return line
