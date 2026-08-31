import os
import subprocess
import sys


def test_runtime_installs_asyncio_reactor_before_cli_import() -> None:
    code = """
import hust_crawler.runtime as runtime
runtime._install_reactor()
import hust_crawler.cli
from twisted.internet import reactor
actual = f\"{reactor.__class__.__module__}.{reactor.__class__.__name__}\"
assert actual == \"twisted.internet.asyncioreactor.AsyncioSelectorReactor\", actual
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
