import math

from dbzap.server.metrics import MetricsCollector

_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, math.inf)


# ---------------------------------------------------------------------------
# record_request / export_prometheus
# ---------------------------------------------------------------------------


def test_counter_increments() -> None:
    c = MetricsCollector()
    c.record_request("GET", "/api/users", 200, 0.01)
    c.record_request("GET", "/api/users", 200, 0.02)
    output = c.export_prometheus()
    assert 'http_requests_total{method="GET",path="/api/users",status="200"} 2' in output


def test_different_labels_are_separate() -> None:
    c = MetricsCollector()
    c.record_request("GET", "/api/users", 200, 0.01)
    c.record_request("POST", "/api/users", 201, 0.05)
    output = c.export_prometheus()
    assert 'http_requests_total{method="GET",path="/api/users",status="200"} 1' in output
    assert 'http_requests_total{method="POST",path="/api/users",status="201"} 1' in output


def test_histogram_sum_and_count() -> None:
    c = MetricsCollector()
    c.record_request("GET", "/api/users", 200, 0.03)
    c.record_request("GET", "/api/users", 200, 0.07)
    output = c.export_prometheus()
    assert 'http_request_duration_seconds_count{method="GET",path="/api/users"} 2' in output
    assert 'http_request_duration_seconds_sum{method="GET",path="/api/users"}' in output


def test_histogram_buckets_cumulative() -> None:
    c = MetricsCollector()
    c.record_request("GET", "/api/users", 200, 0.003)  # fits in all buckets >= 0.005
    output = c.export_prometheus()
    # All buckets should have count >= 1
    assert 'le="0.005"} 1' in output
    assert 'le="+Inf"} 1' in output


def test_histogram_bucket_boundary() -> None:
    c = MetricsCollector()
    c.record_request("GET", "/p", 200, 0.005)  # exactly 0.005 - should be counted in 0.005 bucket
    output = c.export_prometheus()
    assert 'le="0.005"} 1' in output


def test_in_progress_gauge() -> None:
    c = MetricsCollector()
    c.set_in_progress(5)
    output = c.export_prometheus()
    assert "http_requests_in_progress 5" in output


def test_in_progress_updates() -> None:
    c = MetricsCollector()
    c.set_in_progress(3)
    c.set_in_progress(7)
    output = c.export_prometheus()
    assert "http_requests_in_progress 7" in output


# ---------------------------------------------------------------------------
# pool stats
# ---------------------------------------------------------------------------


def test_pool_stats_exported() -> None:
    c = MetricsCollector()
    c.update_pool_stats(pool_size=10, checked_out=3, overflow=1)
    output = c.export_prometheus()
    assert "db_pool_size 10" in output
    assert "db_pool_checked_out 3" in output
    assert "db_pool_overflow 1" in output


# ---------------------------------------------------------------------------
# db query histogram
# ---------------------------------------------------------------------------


def test_db_query_recorded() -> None:
    c = MetricsCollector()
    c.record_db_query("users", "SELECT", 0.002)
    output = c.export_prometheus()
    assert 'db_query_duration_seconds_count{table="users",operation="SELECT"} 1' in output


# ---------------------------------------------------------------------------
# Prometheus format structure
# ---------------------------------------------------------------------------


def test_help_and_type_lines_present() -> None:
    c = MetricsCollector()
    c.record_request("GET", "/api/users", 200, 0.01)
    output = c.export_prometheus()
    assert "# HELP http_requests_total" in output
    assert "# TYPE http_requests_total counter" in output
    assert "# HELP http_request_duration_seconds" in output
    assert "# TYPE http_request_duration_seconds histogram" in output
    assert "# HELP http_requests_in_progress" in output
    assert "# TYPE http_requests_in_progress gauge" in output


def test_empty_collector_exports_cleanly() -> None:
    c = MetricsCollector()
    output = c.export_prometheus()
    assert isinstance(output, str)
    assert "http_requests_in_progress 0" in output
