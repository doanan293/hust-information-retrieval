from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True, slots=True)
class ResourceSample:
    cpu_percent: float | None
    ram_percent: float | None


class ResourceSampler:
    def __init__(
        self,
        *,
        read_text: Callable[[Path], str] | None = None,
        meminfo_text: Callable[[], str] | None = None,
    ) -> None:
        self._read_text = read_text or (lambda path: path.read_text(encoding="utf-8"))
        self._meminfo_text = meminfo_text or (lambda: Path("/proc/meminfo").read_text(encoding="utf-8"))
        self._previous_cpu: tuple[int, int] | None = None

    def sample(self) -> ResourceSample:
        return ResourceSample(self._cpu_percent(), self._ram_percent())

    def _cpu_percent(self) -> float | None:
        try:
            fields = self._read_text(Path("/proc/stat")).splitlines()[0].split()
            values = [int(value) for value in fields[1:]]
            total = sum(values)
            idle = values[3] + (values[4] if len(values) > 4 else 0)
        except (OSError, IndexError, ValueError):
            return None
        previous = self._previous_cpu
        self._previous_cpu = (total, idle)
        if previous is None or total <= previous[0]:
            return None
        total_delta = total - previous[0]
        idle_delta = max(0, idle - previous[1])
        return round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)

    def _ram_percent(self) -> float | None:
        try:
            values = {}
            for line in self._meminfo_text().splitlines():
                key, separator, raw = line.partition(":")
                if separator and key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(raw.strip().split()[0])
            total, available = values["MemTotal"], values["MemAvailable"]
            if total <= 0:
                return None
            return round(max(0.0, min(100.0, 100.0 * (total - available) / total)), 1)
        except (OSError, KeyError, ValueError, IndexError):
            return None
