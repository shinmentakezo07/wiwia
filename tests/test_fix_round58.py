"""Regression tests for the OpenCode version refresh source.

The bug: ``opencode_version.refresh_version`` read the live version from
GitHub's ``releases/latest`` REST API. Anonymous calls to that API are capped
at 60 requests/hour **per IP**, and the 5-minute sweep alone spends 12 of
them; every other GitHub API consumer behind the same egress IP (a shared
NAT, another agent CLI on the host) takes the rest. On 2026-09-15 19:40:48
the ceiling was reached and the API answered a bare ``403``::

    [warning  ] opencode_version_fetch_bad_status status=403

The cache therefore never filled and ``OpencodeAdapter.headers()`` kept
sending ``User-Agent: opencode/unknown`` — the stale-fingerprint case the
live refresh exists to prevent.

The npm registry — the CLI's own distribution source, and the endpoint the
sibling Cline/WorkBuddy version helpers already read — publishes the same
version with no comparable per-IP budget.
"""
from __future__ import annotations

import time

import respx

from wiwi.providers import opencode_version as ov

# Any GitHub REST call: the previous source, which must not come back.
_GITHUB_RELEASES_RE = r"https://api\.github\.com/.*"


@respx.mock
async def test_refresh_version_reads_the_quota_free_npm_registry():
    """The 5-minute sweep must not depend on a per-IP-limited endpoint."""
    ov._set_cached_for_tests(None, 0.0)
    npm = respx.get(ov.NPM_LATEST_URL).respond(json={"version": "1.18.31"})
    github = respx.get(url__regex=_GITHUB_RELEASES_RE).respond(status_code=403)

    assert await ov.refresh_version() == "1.18.31"
    assert ov.get_cached_version() == "1.18.31"
    assert npm.called
    assert not github.called, (
        "GitHub's 60-req/hour-per-IP API is what starved the cache; the sweep "
        "must not read it"
    )


@respx.mock
async def test_registry_failure_keeps_the_stale_version():
    ov._set_cached_for_tests("1.18.30", time.monotonic())
    respx.get(ov.NPM_LATEST_URL).respond(status_code=500)

    assert await ov.refresh_version() is None
    assert ov.get_cached_version() == "1.18.30"


def test_parse_version_sanitizes_registry_input_for_the_header():
    """The registry is remote input and the value lands in a User-Agent."""
    assert ov._parse_version("1.18.31") == "1.18.31"
    assert ov._parse_version("  1.18.31  ") == "1.18.31"
    assert ov._parse_version("1.18.31\r\nX-Evil: 1") == "1.18.31X-Evil: 1"
    assert ov._parse_version(123) is None
    assert ov._parse_version("") is None
    assert ov._parse_version(None) is None
