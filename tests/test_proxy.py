"""Tests for ProxyManager anti-ban rotating proxy subsystem."""

import time
import pytest
from helpers.proxy_manager import ProxyManager


def test_proxy_manager_empty():
    """Test ProxyManager handles empty configuration gracefully."""
    pm = ProxyManager(single_proxy=None, proxy_pool=None)
    assert not pm.is_active
    assert pm.total_proxies == 0
    assert pm.get_proxy() is None


def test_proxy_manager_single_proxy():
    """Test ProxyManager with a single rotating gateway."""
    pm = ProxyManager(single_proxy="http://user:pass@proxy.gate:8000")
    assert pm.is_active
    assert pm.total_proxies == 1
    assert pm.get_proxy() == "http://user:pass@proxy.gate:8000"


def test_proxy_manager_pool_round_robin():
    """Test ProxyManager rotates through multiple proxies sequentially."""
    pool_str = "http://1.1.1.1:8080,http://2.2.2.2:8080,http://3.3.3.3:8080"
    pm = ProxyManager(proxy_pool=pool_str)
    assert pm.total_proxies == 3

    p1 = pm.get_proxy()
    p2 = pm.get_proxy()
    p3 = pm.get_proxy()
    p4 = pm.get_proxy()

    assert p1 == "http://1.1.1.1:8080"
    assert p2 == "http://2.2.2.2:8080"
    assert p3 == "http://3.3.3.3:8080"
    assert p4 == "http://1.1.1.1:8080"  # Wrapped around


def test_proxy_manager_file_reading(tmp_path):
    """Test ProxyManager loads proxy list from a file."""
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text(
        "# Comments should be ignored\n"
        "http://proxy1:8080\n"
        "\n"
        "http://proxy2:8080\n"
    )

    pm = ProxyManager(proxy_pool=str(proxy_file))
    assert pm.total_proxies == 2
    assert pm.get_proxy() == "http://proxy1:8080"
    assert pm.get_proxy() == "http://proxy2:8080"


def test_proxy_manager_cooldown_on_failure():
    """Test failing proxy enters cooldown and is skipped."""
    pool_str = "http://bad.proxy:8080,http://good.proxy:8080"
    pm = ProxyManager(proxy_pool=pool_str, cooldown_seconds=60)

    pm.report_failure("http://bad.proxy:8080")

    # Should skip bad.proxy and return good.proxy
    assert pm.get_proxy() == "http://good.proxy:8080"
    assert pm.get_proxy() == "http://good.proxy:8080"

    # Report success on good proxy
    pm.report_success("http://good.proxy:8080")
