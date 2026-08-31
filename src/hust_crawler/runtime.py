from __future__ import annotations


_ASYNCIO_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"


def _install_reactor() -> None:
    from scrapy.utils.reactor import install_reactor

    install_reactor(_ASYNCIO_REACTOR)


def main(argv: list[str] | None = None) -> int:
    _install_reactor()
    from .cli import main as cli_main

    return cli_main(argv)
