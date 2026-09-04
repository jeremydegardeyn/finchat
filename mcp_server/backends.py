"""Access to FinChat's governed API surfaces, for the MCP server.

The MCP server is a **client of the existing APIs**, never a second path to the
data. That is the whole point: the DaaS API already carries the query caps, the
Gold-only serving rule, the least-privilege service account and the audit trail
(docs/05). An MCP tool that reached BigQuery directly would be a bypass wearing a
protocol badge, and it would be invisible in exactly the reports that are supposed
to catch bypasses.

Two transports, chosen by configuration rather than by flag:

- **HTTP** when `FINCHAT_TXN_API_URL` / `FINCHAT_LOAN_API_URL` are set. The
  services are private Cloud Run, so every call carries an OIDC ID token — minted
  from the metadata server when running on GCP, from the signed-in user's gcloud
  identity when running on a laptop. The latter matters: the call is then bound to
  *the person*, and BigQuery's column-level security is evaluated against them
  (ADR-0019), rather than against a shared key.
- **In-process demo** when neither URL is set, reusing the same sample
  repositories the APIs themselves fall back to. So the server installs and runs
  before any GCP access exists, and the sample data cannot drift from the APIs'
  sample data because it is the same module.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import loader

REPO_ROOT = Path(__file__).resolve().parent.parent

TXN_API_URL = os.getenv("FINCHAT_TXN_API_URL", "").rstrip("/")
LOAN_API_URL = os.getenv("FINCHAT_LOAN_API_URL", "").rstrip("/")
AGENT_URL = os.getenv("FINCHAT_AGENT_URL", "").rstrip("/")
TIMEOUT = float(os.getenv("FINCHAT_MCP_TIMEOUT", "30"))

KB_CORPUS = REPO_ROOT / "products" / "transactions" / "agent" / "kb" / "corpus.jsonl"
KB_TOP_N = int(os.getenv("FINCHAT_MCP_KB_TOP_N", "4"))


class BackendError(RuntimeError):
    """A backend call failed. The message is written to be read by the model.

    `status` carries the HTTP code when there was one, and is None for a transport
    failure. Callers decide how to degrade from the code rather than by matching
    substrings of the message, which breaks the first time a response body happens
    to contain the digits of a status.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# --- identity ----------------------------------------------------------------
_token_cache: dict[str, tuple[str, float]] = {}


def _id_token(audience: str) -> str | None:
    """An OIDC ID token for a private Cloud Run audience, or None.

    Order matters. Workload credentials (metadata server, service-account key,
    impersonation) can mint a token for an arbitrary audience, so they are tried
    first. A signed-in *user* credential cannot — `fetch_id_token` raises for it —
    so we fall back to the token gcloud already holds for that person. Cloud Run
    accepts it when the user holds `run.invoker`, which is the behaviour we want
    locally: the call is attributable to a human, not to a shared secret.
    """
    hit = _token_cache.get(audience)
    if hit and hit[1] > time.time():
        return hit[0]

    token = None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token as gid

        token = gid.fetch_id_token(Request(), audience)
    except Exception:
        token = None

    if not token:
        gcloud = shutil.which("gcloud") or "gcloud"
        try:
            out = subprocess.run([gcloud, "auth", "print-identity-token"],
                                 capture_output=True, text=True, timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                token = out.stdout.strip()
        except Exception:
            token = None

    if token:
        # Google ID tokens live an hour; re-mint well before the edge.
        _token_cache[audience] = (token, time.time() + 45 * 60)
    return token


# --- HTTP --------------------------------------------------------------------
def _request(base: str, path: str, *, method: str = "GET",
             params: dict | None = None, body: dict | None = None,
             headers: dict | None = None) -> object:
    url = f"{base}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"

    hdrs = {"Accept": "application/json"}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"

    token = _id_token(base)
    if token:
        hdrs["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code in (401, 403):
            raise BackendError(
                f"{method} {path} was refused ({e.code}). The API is private, so the "
                f"caller needs roles/run.invoker on it. Locally: `gcloud auth login`, "
                f"then check `gcloud config get-value account`. Detail: {detail}",
                status=e.code) from None
        if e.code == 404:
            raise BackendError(f"Not found: {path}. Detail: {detail}", status=404) from None
        raise BackendError(f"{method} {path} failed ({e.code}): {detail}",
                           status=e.code) from None
    except urllib.error.URLError as e:
        raise BackendError(f"{method} {path} could not reach {base}: {e.reason}") from None


# --- in-process demo ---------------------------------------------------------
def _demo_txn():
    """The transactions API's own demo repository, imported directly.

    `DEMO_MODE=1` keeps it on the standard library — no google-cloud import, no
    credentials, nothing to configure.
    """
    os.environ.setdefault("DEMO_MODE", "1")
    bq = loader.load("finchat_txn_repo",
                     REPO_ROOT / "products" / "transactions" / "api" / "bq.py")
    return bq.Repository()


def _demo_loans():
    os.environ.setdefault("DEMO_MODE", "1")
    # Spelled out from REPO_ROOT rather than via a shared prefix variable: the
    # image guard reads these paths out of the source, and a variable hides them.
    loan_store = loader.load(
        "finchat_loan_store", REPO_ROOT / "products" / "loans" / "api" / "store.py")
    loan_risk = loader.load(
        "finchat_loan_risk", REPO_ROOT / "products" / "loans" / "api" / "risk.py")
    return loan_store.LoanStore(), loan_risk


def _local_kb_search(query: str, top_n: int = KB_TOP_N) -> list[dict]:
    """BM25 over the KB corpus file, when the agent service is not reachable.

    Honestly weaker than the deployed path and labelled as such. The corpus is 22
    documents and BM25 is the same implementation the agent's sparse arm uses, so
    exact-token questions — a zip code, `NSF`, `$225` — land correctly. What is
    missing is the dense arm, which is what catches a paraphrase with no shared
    tokens ("spend more than I have" → overdraft). `test_retrieval.py` pins both
    behaviours, including sparse getting that paraphrase wrong.

    So this is a real fallback, not a stub, and the `retriever` field says which
    one answered rather than letting a client assume it got the full pipeline.
    """
    if not KB_CORPUS.is_file():
        return [{"error": "knowledge base not configured and no local corpus found"}]
    docs, by_id = [], {}
    with KB_CORPUS.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            by_id[row["doc_id"]] = row
            docs.append({"doc_id": row["doc_id"], "title": row.get("title", ""),
                         "content": row.get("content", "")})
    retrieval = loader.load(
        "finchat_retrieval",
        REPO_ROOT / "products" / "transactions" / "agent" / "retrieval.py")
    ranked = retrieval.bm25_rank(query, docs)[:top_n]
    return [{"title": by_id[d]["title"], "category": by_id[d].get("category", ""),
             "content": by_id[d]["content"], "retriever": "sparse-local"}
            for d, _ in ranked]


def _degraded(err: BackendError) -> str:
    """Say why retrieval fell back, in terms of the thing an operator has to fix."""
    if err.status in (401, 403):
        what = ("The agent refused the call (auth). Run `gcloud auth login`, or grant "
                "the caller roles/run.invoker on the agent service.")
    elif err.status == 404:
        what = ("The agent has no /search endpoint — that revision predates it. "
                "Redeploy the agent.")
    elif err.status is None:
        what = "The agent could not be reached."
    else:
        what = f"The agent returned {err.status}."
    return f"Degraded to local BM25: {what} Ranking is sparse-only; see docs/21."


class Backends:
    """One object the tools call, whichever transport is live."""

    def __init__(self, txn_url: str = TXN_API_URL, loan_url: str = LOAN_API_URL,
                 agent_url: str = AGENT_URL):
        self.txn_url = txn_url
        self.loan_url = loan_url
        self.agent_url = agent_url
        self._txn_demo = None
        self._loan_demo = None

    @property
    def mode(self) -> dict:
        return {
            "transactions": "http" if self.txn_url else "demo",
            "loans": "http" if self.loan_url else "demo",
            "knowledge_base": "agent" if self.agent_url else "local-bm25",
            "txn_api_url": self.txn_url or None,
            "loan_api_url": self.loan_url or None,
            "agent_url": self.agent_url or None,
        }

    # -- knowledge base -------------------------------------------------------
    def search_kb(self, query: str) -> list[dict]:
        """Passages from the bank's policy/product knowledge base.

        Prefers the agent service's `/search`, which runs dense + BM25 + RRF +
        Gemini rerank in one BigQuery job (docs/21). **Any** agent failure degrades
        to local BM25 with the cause stated, rather than only the 404 case.

        This tool degrades where the account tools deliberately do not, and the
        difference is the content, not the code path. The local corpus is the same
        public policy text committed in this repo, so serving it is a worse *answer*
        and not a worse *disclosure*. An account balance has no such fallback: demo
        figures presented as a customer's real ones is a far worse outcome than an
        error, so those tools fail loudly and always will.

        The note is not decoration. Configuring the server harder — pointing it at a
        real agent — must not be able to make it answer less than it did before, and
        an operator still has to be able to see that retrieval is running degraded.
        """
        if not self.agent_url:
            return _local_kb_search(query)
        try:
            out = _request(self.agent_url, "/search", method="POST", body={"query": query})
            results = (out or {}).get("results") or []
        except BackendError as e:
            return [{"note": _degraded(e)}] + _local_kb_search(query)
        # The tool reports its own misconfiguration rather than raising; surface it.
        if len(results) == 1 and "error" in results[0]:
            return [{"note": f"Agent knowledge base unavailable ({results[0]['error']}). "
                             "Served locally instead."}] + _local_kb_search(query)
        return results

    def _txn(self):
        if self._txn_demo is None:
            self._txn_demo = _demo_txn()
        return self._txn_demo

    def _loans(self):
        if self._loan_demo is None:
            self._loan_demo = _demo_loans()
        return self._loan_demo

    # -- transactions ---------------------------------------------------------
    def sample_accounts(self, n: int = 5) -> list[str]:
        if self.txn_url:
            out = _request(self.txn_url, "/v1/accounts/samples", params={"n": n})
            return (out or {}).get("account_ids", [])
        return self._txn().get_sample_accounts(n)

    def balance(self, account_id: str) -> dict:
        if self.txn_url:
            return _request(self.txn_url, f"/v1/accounts/{account_id}/balance")
        row = self._txn().get_balance(account_id)
        if not row:
            raise BackendError(f"account {account_id} not found")
        return row

    def transactions(self, account_id: str, limit: int = 50) -> list[dict]:
        if self.txn_url:
            return _request(self.txn_url, f"/v1/accounts/{account_id}/transactions",
                            params={"limit": limit})
        rows = self._txn().get_transactions(account_id, limit)
        if not rows:
            raise BackendError(f"no transactions for account {account_id}")
        return rows

    def activity(self, account_id: str, days: int = 30) -> list[dict]:
        if self.txn_url:
            return _request(self.txn_url, f"/v1/accounts/{account_id}/activity",
                            params={"days": days})
        return self._txn().get_recent_activity(account_id, days)

    def summary(self, account_id: str) -> dict:
        if self.txn_url:
            return _request(self.txn_url, f"/v1/accounts/{account_id}/summary")
        row = self._txn().get_summary(account_id)
        if not row:
            raise BackendError(f"account {account_id} not found")
        return row

    # -- loans ----------------------------------------------------------------
    def submit_loan(self, customer_name: str, amount: float, term_months: int,
                    account_id: str | None) -> dict:
        payload = {"customer_name": customer_name, "amount": amount,
                   "term_months": term_months, "account_id": account_id}
        if self.loan_url:
            return _request(self.loan_url, "/v1/loans", method="POST", body=payload)
        store, risk = self._loans()
        loan = store.create_loan(customer_name, amount, term_months, account_id)
        lid = loan["loan_id"]
        profile = risk.synthesize_credit_profile(lid, amount, term_months)
        store.save_profile(lid, {"credit_score": profile.credit_score,
                                 "annual_income": profile.annual_income,
                                 "existing_debt": profile.existing_debt,
                                 "dti_ratio": profile.dti_ratio})
        result = risk.score_risk(profile, amount, overdraft_events=0)
        store.save_risk(lid, result.to_row(), overdraft_events=0)
        store.set_status(lid, "PENDING_APPROVAL")
        return {"loan_id": lid, "status": "PENDING_APPROVAL",
                "risk_score": result.risk_score, "recommendation": result.recommendation,
                "reasons": result.reasons, "factors": result.factors,
                "principal_reasons": result.principal_reasons,
                "model_version": result.model_version}

    def loan(self, loan_id: str) -> dict:
        if self.loan_url:
            return _request(self.loan_url, f"/v1/loans/{loan_id}")
        row = self._loans()[0].get_loan(loan_id)
        if not row:
            raise BackendError(f"loan {loan_id} not found")
        return row

    def loans(self, status: str | None = None) -> list[dict]:
        if self.loan_url:
            return _request(self.loan_url, "/v1/loans", params={"status": status})
        return self._loans()[0].list_loans(status)

    def loan_audit(self, loan_id: str) -> list[dict]:
        if self.loan_url:
            return _request(self.loan_url, f"/v1/loans/{loan_id}/audit")
        return self._loans()[0].get_audit(loan_id)
