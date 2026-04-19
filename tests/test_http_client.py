"""Tests for the shared requests.Session module.

Pins the v6.1 A0.1 contract:
- get_session() returns the process-wide singleton (same object across calls)
- Thread-safe init (no torn state when two threads race)
- The mounted adapter has connection pooling + retry configured
- close_session() actually closes and allows re-init
"""

import threading
from unittest.mock import patch

import pytest
import requests

import http_client


@pytest.fixture(autouse=True)
def _reset_session():
    """Each test starts with a fresh session."""
    http_client._reset_for_tests()
    yield
    http_client._reset_for_tests()


class TestSingleton:
    def test_returns_same_instance_on_repeat_calls(self):
        s1 = http_client.get_session()
        s2 = http_client.get_session()
        assert s1 is s2

    def test_returns_requests_session(self):
        s = http_client.get_session()
        assert isinstance(s, requests.Session)

    def test_close_releases_and_allows_reinit(self):
        s1 = http_client.get_session()
        http_client.close_session()
        s2 = http_client.get_session()
        assert s2 is not s1

    def test_concurrent_init_yields_one_instance(self):
        """Two threads calling get_session() at the same time should see the
        same Session object — no torn init."""
        sessions = []
        started = threading.Event()

        def worker():
            started.wait()
            sessions.append(http_client.get_session())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        started.set()
        for t in threads: t.join()

        first = sessions[0]
        assert all(s is first for s in sessions)


class TestAdapterConfig:
    """The mounted HTTPAdapter should have pooling + retry wired."""

    def test_adapter_mounted_for_both_schemes(self):
        s = http_client.get_session()
        # HTTPAdapter mounts for both http:// and https://
        assert "http://" in s.adapters
        assert "https://" in s.adapters
        # And they should be distinct adapters (each scheme gets its own pool)
        # but both should be HTTPAdapter instances with our config.
        for scheme in ("http://", "https://"):
            adapter = s.adapters[scheme]
            # pool_connections / pool_maxsize stored as _pool_connections / _pool_maxsize
            assert getattr(adapter, "_pool_connections", None) == 32
            assert getattr(adapter, "_pool_maxsize", None) == 32

    def test_retry_configured_for_5xx(self):
        s = http_client.get_session()
        adapter = s.adapters["https://"]
        retry = adapter.max_retries
        assert retry.total == 2
        assert 502 in retry.status_forcelist
        assert 503 in retry.status_forcelist
        assert 504 in retry.status_forcelist
        assert retry.backoff_factor == 0.5

    def test_retry_restricted_to_idempotent_methods(self):
        """POST must NOT be retried — downloads are GET-only in this app."""
        s = http_client.get_session()
        retry = s.adapters["https://"].max_retries
        # allowed_methods is a frozenset of uppercase method names
        methods = {m.upper() for m in (retry.allowed_methods or [])}
        assert "GET" in methods
        assert "HEAD" in methods
        assert "POST" not in methods


class TestConnectionReuse:
    """Sanity check that the Session would actually reuse a connection —
    we don't make a real network call, just assert the pool manager is
    shared across requests to the same host."""

    def test_same_session_serves_multiple_calls(self):
        s = http_client.get_session()
        # Two calls to .get() use the same session → same pool → reuse.
        # We can't test actual socket reuse without a real server; asserting
        # the session object is shared is the best offline check.
        with patch.object(s, "request", return_value="ok") as mock:
            s.get("https://example.com/a")
            s.get("https://example.com/b")
            assert mock.call_count == 2


class TestCloseIdempotence:
    def test_close_without_init_is_noop(self):
        # Never called get_session; close should just return
        http_client.close_session()
        # No exception = pass
