import pytest
from helpers.metrics import metrics

def test_metrics_increment():
    metrics.counters.clear()
    metrics.increment("test_counter")
    assert metrics.counters["test_counter"] == 1
    metrics.increment("test_counter", 5)
    assert metrics.counters["test_counter"] == 6

def test_metrics_set_gauge():
    metrics.gauges.clear()
    metrics.set_gauge("test_gauge", 10.5)
    assert metrics.gauges["test_gauge"] == 10.5
    metrics.set_gauge("test_gauge", 42.0)
    assert metrics.gauges["test_gauge"] == 42.0

def test_metrics_get_all_and_summary():
    metrics.counters.clear()
    metrics.gauges.clear()
    metrics.increment("test_c", 2)
    metrics.set_gauge("test_g", 3.14)
    
    data = metrics.get_all()
    assert data["counters"]["test_c"] == 2
    assert data["gauges"]["test_g"] == 3.14
    
    summary = metrics.get_summary()
    assert "test_c: 2" in summary
    assert "test_g: 3.14" in summary
