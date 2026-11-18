# -*- coding: utf-8 -*-
"""Prometheus instrumentation shared by the API and middleware."""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

etl_files_processed_total = Counter(
    "etl_files_processed_total",
    "Total ETL files processed",
    ["formato"],
)

etl_rows_loaded_total = Counter(
    "etl_rows_loaded_total",
    "Total ETL rows loaded",
)

__all__ = [
    "CONTENT_TYPE_LATEST",
    "http_requests_total",
    "http_request_duration_seconds",
    "etl_files_processed_total",
    "etl_rows_loaded_total",
    "generate_latest",
]
