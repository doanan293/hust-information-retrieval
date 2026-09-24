from hust_crawler.crawl.adaptive import HostDecision, HostHealth, Observation


def test_healthy_host_grows_one_step_after_full_window() -> None:
    health = HostHealth(start_concurrency=1, normal_ceiling=2, hard_ceiling=4, window_size=5, change_interval=30.0)
    for second in range(5):
        health.observe(Observation(at=second, latency=0.2, status=200, error=None))
    assert health.recommend(now=31.0) == HostDecision(concurrency=2, cooldown_until=None)


def test_host_never_exceeds_hard_ceiling() -> None:
    health = HostHealth(start_concurrency=4, normal_ceiling=4, hard_ceiling=4, window_size=5, change_interval=30.0)
    assert health.recommend(now=100.0).concurrency == 4


def test_429_returns_to_one_and_honors_retry_after() -> None:
    health = HostHealth(start_concurrency=4, normal_ceiling=4, hard_ceiling=4, window_size=5, change_interval=30.0)
    health.observe(Observation(at=10.0, latency=0.3, status=429, error=None, retry_after_seconds=120.0))
    assert health.recommend(now=10.0) == HostDecision(concurrency=1, cooldown_until=130.0)


def test_429_without_retry_after_uses_bounded_exponential_cooldown() -> None:
    health = HostHealth(start_concurrency=4, normal_ceiling=4, hard_ceiling=4)

    health.observe(Observation(at=10.0, latency=0.3, status=429, error=None))
    assert health.recommend(now=10.0).cooldown_until == 40.0

    health.observe(Observation(at=40.0, latency=0.3, status=429, error=None))
    assert health.recommend(now=40.0).cooldown_until == 100.0

    health.observe(Observation(at=100.0, latency=0.3, status=429, error=None))
    assert health.recommend(now=100.0).cooldown_until == 220.0

    health.observe(Observation(at=220.0, latency=0.3, status=429, error=None))
    assert health.recommend(now=220.0).cooldown_until == 340.0


def test_repeated_timeout_halves_concurrency() -> None:
    health = HostHealth(start_concurrency=4, normal_ceiling=4, hard_ceiling=4, window_size=5, change_interval=30.0)
    for second in range(3):
        health.observe(Observation(at=second, latency=None, status=None, error="timeout"))
    assert health.recommend(now=31.0).concurrency == 2


def test_three_consecutive_403_responses_open_host_circuit() -> None:
    health = HostHealth(
        start_concurrency=4,
        normal_ceiling=4,
        hard_ceiling=4,
        window_size=5,
        change_interval=30.0,
    )

    for second in (10.0, 20.0, 30.0):
        health.observe(Observation(at=second, latency=0.2, status=403, error=None))

    assert health.recommend(now=30.0) == HostDecision(
        concurrency=1,
        cooldown_until=330.0,
    )


def test_captcha_opens_host_circuit_immediately() -> None:
    health = HostHealth(
        start_concurrency=4,
        normal_ceiling=4,
        hard_ceiling=4,
    )

    health.observe(Observation(at=10.0, latency=0.2, status=200, error="captcha"))

    assert health.recommend(now=10.0) == HostDecision(
        concurrency=1,
        cooldown_until=310.0,
    )
