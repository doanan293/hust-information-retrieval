from hust_crawler.crawl.resources import ResourceSampler


def test_first_cpu_sample_is_unavailable_and_second_is_percentage() -> None:
    values = iter([
        "cpu  100 0 100 800 0 0 0 0 0 0\n",
        "cpu  120 0 110 830 0 0 0 0 0 0\n",
    ])
    sampler = ResourceSampler(read_text=lambda path: next(values), meminfo_text=lambda: "MemTotal: 100 kB\nMemAvailable: 25 kB\n")
    assert sampler.sample().cpu_percent is None
    assert sampler.sample().cpu_percent == 50.0


def test_ram_percentage_uses_available_memory() -> None:
    sampler = ResourceSampler(
        read_text=lambda path: "cpu  100 0 100 800 0 0 0 0 0 0\n",
        meminfo_text=lambda: "MemTotal: 1000 kB\nMemAvailable: 200 kB\n",
    )
    assert sampler.sample().ram_percent == 80.0
