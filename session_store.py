import threading
import time
import uuid
from config import SESSION_TTL


class SessionStore:
    def __init__(self, ttl=None):
        self._store = {}
        self._lock = threading.Lock()
        self._ttl = ttl if ttl is not None else SESSION_TTL

    def create(self):
        sid = str(uuid.uuid4())
        with self._lock:
            self._store[sid] = {
                "status": "created",
                "results": [],
                "progress_index": 0,
                "total": 0,
                "created_at": time.time(),
            }
        return sid

    def get(self, sid):
        with self._lock:
            data = self._store.get(sid)
            if data is None:
                return None
            snapshot = dict(data)
            snapshot["results"] = list(data["results"])
            return snapshot

    def update(self, sid, **kwargs):
        with self._lock:
            if sid in self._store:
                self._store[sid].update(kwargs)

    def add_result(self, sid, result):
        with self._lock:
            if sid in self._store:
                self._store[sid]["results"].append(result)
                self._store[sid]["progress_index"] += 1

    def cleanup(self):
        now = time.time()
        with self._lock:
            expired = [
                sid for sid, data in self._store.items()
                if now - data["created_at"] > self._ttl
            ]
            for sid in expired:
                del self._store[sid]

    def start_cleanup_thread(self, interval=300):
        def _loop():
            while True:
                time.sleep(interval)
                self.cleanup()
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
