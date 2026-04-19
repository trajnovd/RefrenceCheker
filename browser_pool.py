"""Singleton pool of Playwright browser instances (v6.1 §3.19, Phase C).

Each Tier-3 download ACQUIRES a browser, navigates / extracts, then RELEASES.
Prevents spawning N Chromiums in parallel even when the rest of the pipeline
runs many workers.

Lazily imports `playwright` so the module is safe to load when Playwright
isn't installed — `BrowserPool.instance()` returns None in that case and
the Tier 3 caller short-circuits with `not_installed`.
"""

import logging
import queue
import threading

logger = logging.getLogger(__name__)


class BrowserPool:
    _instance = None
    _class_lock = threading.Lock()

    @classmethod
    def instance(cls, size=1):
        """Return the singleton pool (or None if Playwright isn't available)."""
        if cls._instance is not None:
            return cls._instance
        with cls._class_lock:
            if cls._instance is not None:
                return cls._instance
            pool = cls._try_create(size)
            if pool is not None:
                cls._instance = pool
            return cls._instance

    @classmethod
    def _try_create(cls, size):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("playwright not installed — Tier 3 disabled")
            return None
        try:
            pool = cls.__new__(cls)
            pool._pw = sync_playwright().start()
            pool._queue = queue.Queue()
            for _ in range(max(1, int(size))):
                browser = pool._pw.chromium.launch(headless=True)
                pool._queue.put(browser)
            logger.info("BrowserPool initialized: size=%d", size)
            return pool
        except Exception as e:
            logger.warning("Failed to initialize BrowserPool: %s", e)
            return None

    def acquire(self, timeout=60):
        """Acquire a browser from the pool. Returns None on timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def release(self, browser):
        """Return a browser to the pool."""
        if browser is not None:
            self._queue.put(browser)

    def shutdown(self):
        """Close all browsers + the Playwright runtime. Call at app shutdown."""
        try:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait().close()
                except Exception:
                    pass
        finally:
            try:
                self._pw.stop()
            except Exception:
                pass
        BrowserPool._instance = None

    @classmethod
    def _reset_for_tests(cls):
        """Test-only: drop the singleton reference."""
        if cls._instance is not None:
            try:
                cls._instance.shutdown()
            except Exception:
                pass
        cls._instance = None
