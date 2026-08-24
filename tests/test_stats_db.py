"""Tests for DB-backed stats and bucket sizing helpers."""

from __future__ import annotations

import pytest

from wiwi.server.stats import bucket_size_for


@pytest.mark.parametrize("minutes,expected", [
    (0, 86400),       # all-time
    (15, 60),         # 15 min
    (60, 60),         # 1 hour
    (360, 60),        # 6 hours
    (1440, 60),       # 24 hours
    (1441, 3600),     # just over 24h -> 1 hour buckets
    (10080, 3600),    # 7 days
    (10081, 21600),   # just over 7d -> 6 hour buckets
    (43200, 21600),   # 30 days
    (43201, 86400),   # over 30d -> 1 day buckets
])
def test_bucket_size_for(minutes, expected):
    assert bucket_size_for(minutes) == expected
