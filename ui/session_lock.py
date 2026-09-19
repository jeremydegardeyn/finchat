"""
Per-session serialization for the conversation-safety trajectory (ADR-0034, amendment).

Why this exists
---------------
The trajectory engine reads the session's prior `turn_signals` rows at the start of a
request and writes one row at the end. Two requests for the same session in flight at
once each read a state that does not yet contain the other, so neither sees the other's
hit, both write the same `turn_index`, and the count that is supposed to reach five never
does. Observed live 2026-09-19: six messages fired without waiting for replies produced
turn indexes 1,1,1,2,2,2 and no quarantine. The SPA cannot do this — it waits for each
reply — but the engine exists for the scripted attacker, and a script does not wait.

The fix is the rule the SPA already follows, enforced where it can be trusted: **one
message at a time per session.** A request takes a short lease on its session key before
anything else happens; a second request for the same session waits a bounded time for
it and, if it does not get it, is answered "one message at a time" and **still counted**:
it registers itself as a pending concurrent turn, and the lease holder folds every
pending turn into the session state, under the lease, as a `concurrent_turn` security
signal. The burst therefore reaches the same five-hit lock a patient attacker would,
and it reaches it *because* it was a burst.

Where the lease lives
---------------------
Cloud Run runs at minScale=0 and may scale out, so an in-process lock is not a lock. The
lease is a Firestore document keyed by the session key — the BFF's service account
already holds `roles/datastore.user` for the refresh-token store (ADR-0025), the client
library is already installed, and the database is the same one. A lease is one
transaction to take, one to give back, and a rejected turn is one array-union write; at
the free tier that is nothing. The document carries an expiry, so a holder that dies
mid-request (the agent call can take 90 s) blocks its session for `SAFETY_LOCK_TTL_S`
at most, not forever.

Without a project (sandbox, tests) or when Firestore is unreachable the lease degrades to
an in-process table. That is per-instance serialization: correct on one instance, and
on several the same race as before — reported on stdout, never a reason to refuse the
turn. The lock is a control on the evidence; it must not become an outage of the product.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

GCP_PROJECT = os.getenv("GCP_PROJECT", "")
FIRESTORE_DATABASE = os.getenv("FIRESTORE_DATABASE", "ai-gateway")
COLLECTION = os.getenv("SAFETY_LOCK_COLLECTION", "finchat_session_locks")


def wait_seconds() -> float:
    """How long a second request waits for the session before it is turned away. Long
    enough for a human's quick follow-up to be served in order; short enough that a
    script firing a burst gets its answer ("one at a time") rather than a queue."""
    try:
        return float(os.getenv("SAFETY_LOCK_WAIT_S", "3"))
    except ValueError:
        return 3.0


def ttl_seconds() -> float:
    """Lease lifetime. Longer than the slowest honest turn (90 s agent + classifier), so
    a live holder is never stolen from; short enough that a dead one does not lock its
    customer out of the session for long."""
    try:
        return float(os.getenv("SAFETY_LOCK_TTL_S", "120"))
    except ValueError:
        return 120.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- backends ---------------------------------------------------------------------------
# Each backend answers the same four questions. `try_acquire` is the only one that races,
# and it must be atomic in the backend: two callers may not both hear "yes".

class MemoryBackend:
    """One process. Correct on one Cloud Run instance, and the only option without GCP."""
    poll_s = 0.02

    def __init__(self):
        self._mu = threading.Lock()
        self._leases: dict[str, tuple[str, float]] = {}   # key -> (holder, expires_at)
        self._pending: dict[str, list[dict]] = {}

    def try_acquire(self, key: str, holder: str, ttl_s: float) -> bool:
        with self._mu:
            cur = self._leases.get(key)
            if cur and cur[1] > time.monotonic():
                return False
            self._leases[key] = (holder, time.monotonic() + ttl_s)
            return True

    def release(self, key: str, holder: str) -> None:
        with self._mu:
            cur = self._leases.get(key)
            if cur and cur[0] == holder:
                del self._leases[key]

    def register_pending(self, key: str, entry: dict) -> None:
        with self._mu:
            self._pending.setdefault(key, []).append(entry)

    def drain_pending(self, key: str) -> list[dict]:
        with self._mu:
            return self._pending.pop(key, [])


class FirestoreBackend:
    """Across instances. One document per session while a turn is in flight or a
    rejected turn awaits accounting; deleted when neither is true."""
    poll_s = 0.25

    def __init__(self, project: str, database: str, collection: str):
        from google.cloud import firestore
        self._fs = firestore
        self._db = firestore.Client(project=project, database=database)
        self._col = self._db.collection(collection)

    def try_acquire(self, key: str, holder: str, ttl_s: float) -> bool:
        ref = self._col.document(key)
        now = _now()

        @self._fs.transactional
        def _tx(tx):
            snap = ref.get(transaction=tx)
            d = snap.to_dict() if snap.exists else {}
            lease = d.get("lease_until")
            if d.get("holder") and lease is not None and lease > now:
                return False
            tx.set(ref, {"holder": holder,
                         "lease_until": now + timedelta(seconds=ttl_s)}, merge=True)
            return True
        return bool(_tx(self._db.transaction()))

    def release(self, key: str, holder: str) -> None:
        ref = self._col.document(key)

        @self._fs.transactional
        def _tx(tx):
            snap = ref.get(transaction=tx)
            d = snap.to_dict() if snap.exists else {}
            if d.get("holder") != holder:
                return  # expired and taken over, or never ours: leave it alone
            if d.get("pending"):
                # A turn was rejected between the holder's last drain and now; keep the
                # document so the next holder finds it. Only the lease is given back.
                tx.update(ref, {"holder": None, "lease_until": None})
            else:
                tx.delete(ref)
        _tx(self._db.transaction())

    def register_pending(self, key: str, entry: dict) -> None:
        # One write, no read, no lease: the rejected request must not wait for anything.
        self._col.document(key).set({"pending": self._fs.ArrayUnion([entry])}, merge=True)

    def drain_pending(self, key: str) -> list[dict]:
        ref = self._col.document(key)

        @self._fs.transactional
        def _tx(tx):
            snap = ref.get(transaction=tx)
            d = snap.to_dict() if snap.exists else {}
            pending = list(d.get("pending") or [])
            if pending:
                tx.update(ref, {"pending": []})
            return pending
        return list(_tx(self._db.transaction()))


_memory = MemoryBackend()
_firestore: FirestoreBackend | None = None
_firestore_failed = False


def backend():
    """Firestore when there is a project and the client imports; memory otherwise.
    `SAFETY_LOCK_BACKEND=memory` forces the in-process table (sandbox on GCP)."""
    global _firestore, _firestore_failed
    forced = os.getenv("SAFETY_LOCK_BACKEND", "").lower()
    if forced == "memory" or not GCP_PROJECT or _firestore_failed:
        return _memory
    if _firestore is None:
        try:
            _firestore = FirestoreBackend(GCP_PROJECT, FIRESTORE_DATABASE, COLLECTION)
        except Exception as e:  # no client library, no credentials: say so once
            print(f"session_lock: firestore unavailable ({type(e).__name__}); "
                  f"per-instance lock only")
            _firestore_failed = True
            return _memory
    return _firestore


# --- the lease -------------------------------------------------------------------------------

@dataclass
class Lease:
    key: str
    holder: str
    backend: object

    async def drain(self) -> list[dict]:
        """Turns rejected for this session that nobody has yet counted. Under the lease
        only: the caller folds them into the state it holds, in order."""
        try:
            return await asyncio.to_thread(self.backend.drain_pending, self.key)
        except Exception as e:
            print(f"session_lock: drain failed ({type(e).__name__})")
            return []

    async def release(self) -> None:
        try:
            await asyncio.to_thread(self.backend.release, self.key, self.holder)
        except Exception as e:
            print(f"session_lock: release failed ({type(e).__name__}); lease expires")


async def acquire(key: str, wait_s: float | None = None,
                  ttl_s: float | None = None) -> Lease | None:
    """Take the session's lease, waiting up to `wait_s` for a holder to finish. None when
    the wait ran out: the caller answers "one message at a time" and registers the turn.

    A backend failure while acquiring falls back to the in-process table for this call —
    the turn is served, serialized as well as this instance can, and the failure is on
    stdout for the reconciliation to see.
    """
    wait_s = wait_seconds() if wait_s is None else wait_s
    ttl_s = ttl_seconds() if ttl_s is None else ttl_s
    holder = uuid.uuid4().hex
    be = backend()
    deadline = time.monotonic() + wait_s
    while True:
        try:
            got = await asyncio.to_thread(be.try_acquire, key, holder, ttl_s)
        except Exception as e:
            print(f"session_lock: acquire failed ({type(e).__name__}); per-instance lock")
            be = _memory
            got = be.try_acquire(key, holder, ttl_s)
        if got:
            return Lease(key=key, holder=holder, backend=be)
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(be.poll_s)


async def register_concurrent(key: str, conversation_id: str) -> None:
    """Record that a turn for this session arrived while another was in flight and was
    turned away. No text: the conversation id is the FK to `conversation_log`, the
    timestamp is what the velocity window needs."""
    entry = {"conversation_id": conversation_id, "ts": _now().isoformat()}
    be = backend()
    try:
        await asyncio.to_thread(be.register_pending, key, entry)
    except Exception as e:
        print(f"session_lock: register failed ({type(e).__name__}); counting in-process")
        _memory.register_pending(key, entry)
