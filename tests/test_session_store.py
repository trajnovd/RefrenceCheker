import time
from session_store import SessionStore

def test_create_session():
    store = SessionStore(ttl=60)
    sid = store.create()
    session = store.get(sid)
    assert session is not None
    assert session["status"] == "created"
    assert session["results"] == []
    assert session["progress_index"] == 0

def test_get_nonexistent_returns_none():
    store = SessionStore(ttl=60)
    assert store.get("nonexistent") is None

def test_update_session():
    store = SessionStore(ttl=60)
    sid = store.create()
    store.update(sid, status="processing", total=10)
    session = store.get(sid)
    assert session["status"] == "processing"
    assert session["total"] == 10

def test_add_result():
    store = SessionStore(ttl=60)
    sid = store.create()
    store.add_result(sid, {"title": "Test Paper", "status": "found_pdf"})
    session = store.get(sid)
    assert len(session["results"]) == 1
    assert session["results"][0]["title"] == "Test Paper"

def test_expired_session_returns_none():
    store = SessionStore(ttl=0)  # immediate expiry
    sid = store.create()
    time.sleep(0.1)
    store.cleanup()
    assert store.get(sid) is None
