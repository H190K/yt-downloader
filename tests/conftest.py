"""Shared pytest configuration for the H190K Downloader test suite.

* Puts the repository root on ``sys.path`` so ``core`` / ``ui`` import from any cwd.
* Registers the ``network`` marker. Tests marked ``@pytest.mark.network`` are skipped unless
  ``--run-network`` is given (they hit YouTube and use the real tools in ``data/bin``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="run tests marked 'network' (real downloads with the tools in data/bin)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "network: needs internet + data/bin tools; skipped unless --run-network is passed"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="network test: pass --run-network to run")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
