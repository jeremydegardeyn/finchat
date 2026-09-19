"""Per-session serialization of the safety trajectory (ADR-0034 amendment, session_lock.py).

Observed live 2026-09-19: six messages to /api/agent/chat with one session_id, fired
without waiting for replies, produced turn_index 1,1,1,2,2,2 and no quarantine — every
request read the session state before any had written its row. The SPA cannot do this;
the scripted attacker the engine exists for does nothing else.

Three layers, tested in order: the engine's `concurrent_turn` signal (pure), the lease
backends (memory, and Firestore against a fake of the transaction API), and the burst
itself through the FastAPI app with the agent, classifier and store stubbed — the shape
of the Playwright script that found the bug, and the assertion it should have failed.

Needs fastapi + httpx for the end-to-end cases (CI installs them for this file).
"""
import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

import safety_signals as ss
import session_lock as sl

T0 = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


# --- engine: the signal --------------------------------------------------------------------

def test_a_concurrent_turn_is_a_security_hit_the_classifier_cannot_assert():
    t = ss.concurrent_turn(ts=T0)
    assert t.concurrent and t.is_security_hit and ss.signal_class("concurrent_turn") == "security"
    # Server-raised only: a model emitting the name is dropped, and the prompt never names it.
    sig, _, _ = ss.parse_verdict('{"signals":{"concurrent_turn":1,"self_harm":0.2}}')
    assert "concurrent_turn" not in sig
    assert "concurrent_turn" not in ss.CLASSIFIER_PROMPT
    assert "concurrent_turn" not in ss.ALL_SIGNALS


def test_five_concurrent_turns_lock_the_session_and_the_row_says_why():
    st, out = ss.SessionState(), []
    for i in range(5):
        t = ss.concurrent_turn(ts=T0 + timedelta(seconds=i))
        d = ss.evaluate(t, st)
        out.append(d)
        st.advance(t, d)
    assert [d.tier for d in out] == [0, 0, 2, 2, 1]
    assert out[2].cls == "security" and "security_velocity>=3/10m" in out[2].reasons
    assert out[4].action == "quarantine" and "concurrent_turn" in out[4].reasons
    assert st.quarantined


def test_one_concurrent_turn_is_not_a_trajectory():
    """A browser retry or a double-submit is one hit; one hit does nothing."""
    d = ss.evaluate(ss.concurrent_turn(ts=T0), ss.SessionState())
    assert d.tier == 0 and d.action == "none" and d.filters == ["concurrent_turn"]


def test_concurrent_turn_round_trips_through_the_evidence_row():
    """No schema change: the signal lives in `signals`, so a state rebuilt from rows counts
    it exactly as the request that wrote it did."""
    st = ss.SessionState()
    rows = []
    for i in range(5):
        t = ss.concurrent_turn(ts=T0 + timedelta(seconds=i))
        d = ss.evaluate(t, st)
        rows.append(ss.evidence_row(conversation_id=f"c{i}", session_key="s", principal_hash="p",
                                    turn_index=i + 1, persona="customer", channel="agent",
                                    turn=t, decision=d, rationale="", model=None, latency_ms=None))
        st.advance(t, d)
    rebuilt = ss.state_from_rows(rows)
    assert rebuilt.security_hits == 5 and rebuilt.quarantined
    for r in rows:
        assert not any(k in ("question", "answer", "text") for k in r)


# --- the lease: memory backend ----------------------------------------------------------------

def test_memory_lease_is_exclusive_under_threads():
    be = sl.MemoryBackend()
    won = []
    def go(i):
        if be.try_acquire("s", f"h{i}", 10):
            won.append(i)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(50)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(won) == 1


def test_memory_lease_releases_only_for_its_holder_and_expires():
    be = sl.MemoryBackend()
    assert be.try_acquire("s", "a", 10)
    be.release("s", "b")                       # not yours
    assert not be.try_acquire("s", "b", 10)
    be.release("s", "a")
    assert be.try_acquire("s", "b", 0.01)
    import time; time.sleep(0.03)
    assert be.try_acquire("s", "c", 10)        # a dead holder's lease is taken over


def test_pending_turns_are_drained_once():
    be = sl.MemoryBackend()
    be.register_pending("s", {"conversation_id": "c1", "ts": "t"})
    be.register_pending("s", {"conversation_id": "c2", "ts": "t"})
    assert [p["conversation_id"] for p in be.drain_pending("s")] == ["c1", "c2"]
    assert be.drain_pending("s") == []


def test_acquire_waits_bounded_then_gives_up(monkeypatch):
    monkeypatch.setenv("SAFETY_LOCK_BACKEND", "memory")
    async def run():
        a = await sl.acquire("k", wait_s=0.5)
        assert a is not None
        b = await sl.acquire("k", wait_s=0.1)
        assert b is None
        await a.release()
        c = await sl.acquire("k", wait_s=0.1)
        assert c is not None
        await c.release()
    asyncio.run(run())


# --- the lease: Firestore backend, against a fake of the transaction API ------------------

class _FakeFirestore:
    """The four calls FirestoreBackend makes, over a dict, with transactions serialized —
    which is the guarantee real Firestore gives and the one the lease relies on."""
    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.mu = threading.Lock()
        fake = self

        class ArrayUnion:
            def __init__(self, items): self.items = list(items)

        class Snap:
            def __init__(self, d): self._d = d
            @property
            def exists(self): return self._d is not None
            def to_dict(self): return dict(self._d) if self._d else None

        class Ref:
            def __init__(self, key): self.key = key
            def get(self, transaction=None): return Snap(fake.docs.get(self.key))
            def set(self, data, merge=False): fake._apply(self.key, data, merge)

        class Tx:
            def set(self, ref, data, merge=False): fake._apply(ref.key, data, merge)
            def update(self, ref, data): fake._apply(ref.key, data, True)
            def delete(self, ref): fake.docs.pop(ref.key, None)

        class Col:
            def document(self, key): return Ref(key)

        class Client:
            def __init__(self, project=None, database=None): pass
            def collection(self, name): return Col()
            def transaction(self): return Tx()

        def transactional(fn):
            def run(tx):
                with fake.mu:
                    return fn(tx)
            return run

        self.ArrayUnion, self.Client, self.transactional = ArrayUnion, Client, transactional

    def _apply(self, key, data, merge):
        cur = dict(self.docs.get(key) or {}) if merge else {}
        for k, v in data.items():
            if isinstance(v, self.ArrayUnion):
                cur[k] = list(cur.get(k) or []) + v.items
            else:
                cur[k] = v
        self.docs[key] = cur


def _fs_backend():
    fake = _FakeFirestore()
    be = sl.FirestoreBackend.__new__(sl.FirestoreBackend)
    be._fs, be._db = fake, fake.Client()
    be._col = be._db.collection("locks")
    return be, fake


def test_firestore_lease_is_exclusive_and_the_document_is_transient():
    be, fake = _fs_backend()
    assert be.try_acquire("s", "a", 60)
    assert not be.try_acquire("s", "b", 60)
    be.release("s", "b")
    assert "s" in fake.docs                    # not b's to release
    be.release("s", "a")
    assert "s" not in fake.docs                # nothing pending: gone


def test_firestore_expired_lease_is_taken_over():
    be, fake = _fs_backend()
    assert be.try_acquire("s", "a", 60)
    fake.docs["s"]["lease_until"] = sl._now() - timedelta(seconds=1)
    assert be.try_acquire("s", "b", 60)
    assert fake.docs["s"]["holder"] == "b"


def test_firestore_pending_survives_release_and_is_drained_by_the_next_holder():
    be, fake = _fs_backend()
    assert be.try_acquire("s", "a", 60)
    be.register_pending("s", {"conversation_id": "c1", "ts": "t"})   # no lease needed
    be.release("s", "a")
    assert fake.docs["s"]["pending"] and fake.docs["s"].get("holder") is None
    assert be.try_acquire("s", "b", 60)
    assert [p["conversation_id"] for p in be.drain_pending("s")] == ["c1"]
    assert be.drain_pending("s") == []
    be.release("s", "b")
    assert "s" not in fake.docs


def test_firestore_concurrent_acquires_have_one_winner():
    be, _ = _fs_backend()
    won = []
    def go(i):
        if be.try_acquire("s", f"h{i}", 60):
            won.append(i)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(30)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(won) == 1


# --- end to end: the burst through the app --------------------------------------------------
# Everything outside the process is a stub: the agent (server._proxy), the classifier
# transport (server._safety_transport), the evidence store (ss.load_state / ss.write_row
# to a list), conversation_log (server._log_eval), and the lease backend (memory, forced).

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

PROBE_VERDICT = '{"signals":{"identity_probe":0.8},"agent_refused":true,"rationale":"probe"}'


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setenv("SAFETY_SIGNALS", "1")
    monkeypatch.setenv("SAFETY_LOCK_BACKEND", "memory")
    monkeypatch.delenv("CONTROL_EVENTS", raising=False)
    import server
    monkeypatch.setattr(server, "GCP_PROJECT", "test-project")
    monkeypatch.setattr(server, "EVAL_DATASET", "finchat_eval_test")
    monkeypatch.setattr(server, "AGENT_URL", "http://agent.invalid")
    rows: list[dict] = []
    store_mu = threading.Lock()

    async def fake_proxy(base, path, request, extra_headers=None):
        await asyncio.sleep(0.3)   # the agent thinking; long enough for the burst to land
        return fastapi.Response(content=json.dumps({"response": "I can't share that."}),
                                media_type="application/json")

    async def fake_log_eval(*a, **k):
        return None

    def fake_transport(prompt, max_tokens):
        return PROBE_VERDICT, "stub-classifier"

    def fake_load_state(project, dataset, session_key, principal_hash):
        with store_mu:
            mine = sorted((r for r in rows if r["session_key"] == session_key),
                          key=lambda r: r["turn_index"])
        return ss.state_from_rows(mine)

    def fake_write_row(project, dataset, row):
        with store_mu:
            rows.append(row)

    monkeypatch.setattr(server, "_proxy", fake_proxy)
    monkeypatch.setattr(server, "_log_eval", fake_log_eval)
    monkeypatch.setattr(server, "_safety_transport", fake_transport)
    monkeypatch.setattr(ss, "load_state", fake_load_state)
    monkeypatch.setattr(ss, "write_row", fake_write_row)
    # a fresh lease table per test
    monkeypatch.setattr(sl, "_memory", sl.MemoryBackend())
    return server, rows


def _burst(app, n, session_id, message="show me acct-003's owner"):
    """n POSTs to /api/agent/chat for one session, all in flight at once — the Playwright
    script that found the bug, minus the browser."""
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bff") as c:
            return await asyncio.gather(*[
                c.post("/api/agent/chat", json={"message": message, "user_id": "customer",
                                                "session_id": session_id})
                for _ in range(n)])
    return asyncio.run(run())


def test_a_burst_is_serialized_counted_and_locks_the_session(stubbed, monkeypatch):
    """Six at once, no waiting. One is answered; five are turned away and counted under the
    holder's lease. turn_index is strictly increasing, the fifth security hit is the
    quarantine, and the message that was in flight gets the lock, not the answer."""
    monkeypatch.setenv("SAFETY_LOCK_WAIT_S", "0")
    server, rows = stubbed
    rs = _burst(server.app, 6, "burst-1")
    codes = sorted(r.status_code for r in rs)
    assert codes == [200, 429, 429, 429, 429, 429]
    for r in rs:
        if r.status_code == 429:
            assert r.json()["response"] == ss.CONCURRENT_TEXT

    idx = [r["turn_index"] for r in rows]
    assert idx == sorted(idx) and len(set(idx)) == len(idx) == 6, idx
    hits = [r for r in rows if json.loads(r["signals"]).get("concurrent_turn")]
    assert len(hits) == 5 and all("concurrent_turn" in r["reasons"] for r in hits)
    fifth = rows[4]
    assert fifth["action"] == "quarantine" and "security_hits>=5" in fifth["reasons"]
    assert rows[5]["action"] == "quarantine" and "session_quarantined" in rows[5]["reasons"]
    held = next(r for r in rs if r.status_code == 200)
    assert held.json()["safety"]["action"] == "quarantine"
    assert held.json()["response"] == ss.HANDOFF_TEXT["quarantine"]

    # Sticky: re-derived from the rows, so the next honest message meets the lock.
    later = _burst(server.app, 1, "burst-1", message="what is my balance?")[0]
    assert later.status_code == 200 and later.json()["safety"]["action"] == "quarantine"
    assert rows[-1]["turn_index"] == 7


def test_with_a_wait_the_burst_is_served_in_order_and_every_turn_sees_the_last(stubbed, monkeypatch):
    """Bounded wait long enough: nothing is turned away, the turns run one after another,
    and each is classified against a state that contains all the earlier ones."""
    monkeypatch.setenv("SAFETY_LOCK_WAIT_S", "10")
    server, rows = stubbed
    rs = _burst(server.app, 3, "burst-2")
    assert [r.status_code for r in rs] == [200, 200, 200]
    assert [r["turn_index"] for r in rows] == [1, 2, 3]
    assert all(json.loads(r["signals"]) == {"identity_probe": 0.8} for r in rows)
    # refused twice -> review, security_hits>=3 -> review: only a serialized state can say so
    assert rows[1]["tier"] == 2 and "refusals>=2" in rows[1]["reasons"]
    assert rows[2]["tier"] == 2 and "security_hits>=3" in rows[2]["reasons"]


def test_two_sessions_do_not_wait_for_each_other(stubbed, monkeypatch):
    monkeypatch.setenv("SAFETY_LOCK_WAIT_S", "0")
    server, rows = stubbed
    async def run():
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bff") as c:
            return await asyncio.gather(*[
                c.post("/api/agent/chat", json={"message": "hi", "session_id": sid})
                for sid in ("a", "b")])
    rs = asyncio.run(run())
    assert [r.status_code for r in rs] == [200, 200]
    assert sorted(r["turn_index"] for r in rows) == [1, 1]
    assert len({r["session_key"] for r in rows}) == 2


def test_the_lease_is_released_when_the_turn_fails(stubbed, monkeypatch):
    """A crash inside the turn must not hold the session until the TTL."""
    monkeypatch.setenv("SAFETY_LOCK_WAIT_S", "0")
    server, rows = stubbed
    import control_events as ce

    async def boom(*a, **k):
        raise RuntimeError("agent exploded")
    monkeypatch.setattr(server, "_proxy", boom)
    with pytest.raises(RuntimeError):
        _burst(server.app, 1, "crash")
    key = ss.session_hash(ce.principal_hash(""), "crash")
    assert sl._memory.try_acquire(key, "probe", 1)


def test_disabled_means_no_lease_and_no_rejection(stubbed, monkeypatch):
    """Off, the flag changes nothing: the burst is served as before (and, as before, not
    counted). The lock is part of the control, not a rate limit of its own."""
    monkeypatch.setenv("SAFETY_SIGNALS", "0")
    monkeypatch.setenv("SAFETY_LOCK_WAIT_S", "0")
    server, rows = stubbed
    rs = _burst(server.app, 3, "off")
    assert [r.status_code for r in rs] == [200, 200, 200] and rows == []
