"""
Persistence for the Loan Approval product.

BigQuery-backed (finchat_loans_*) with an in-memory demo fallback so the API runs
offline. Decisions and audit entries are APPEND-ONLY (immutable, versioned).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

PROJECT = os.getenv("GCP_PROJECT", "")
DATASET = os.getenv("LOANS_DATASET", "finchat_loans_dev")
DEMO_MODE = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


class LoanStore:
    def __init__(self):
        self._client = None
        self._demo = DEMO_MODE
        # in-memory tables (demo)
        self._loans: dict[str, dict] = {}
        self._profiles: dict[str, dict] = {}
        self._risks: list[dict] = []
        self._decisions: list[dict] = []
        self._audit: list[dict] = []
        if not self._demo:
            try:
                from google.cloud import bigquery
                if not PROJECT:
                    raise RuntimeError("GCP_PROJECT not set")
                self._client = bigquery.Client(project=PROJECT)
            except Exception:
                self._demo = True

    @property
    def mode(self) -> str:
        return "demo" if self._demo else "bigquery"

    def _t(self, name: str) -> str:
        return f"{PROJECT}.{DATASET}.{name}"

    def _insert(self, table: str, row: dict):
        if self._demo:
            return
        errors = self._client.insert_rows_json(self._t(table), [row])
        if errors:
            raise RuntimeError(f"BigQuery insert errors on {table}: {errors}")

    # --- audit ---------------------------------------------------------------
    def audit(self, loan_id: str | None, actor: str, action: str, detail: str = ""):
        row = {"audit_id": new_id("aud"), "loan_id": loan_id, "actor": actor,
               "action": action, "detail": detail, "event_time": _now()}
        self._audit.append(row)
        self._insert("loan_audit_log", row)

    # --- loan request --------------------------------------------------------
    def create_loan(self, customer_name: str, amount: float, term_months: int,
                    account_id: str | None = None) -> dict:
        loan_id = new_id("loan")
        row = {"loan_id": loan_id, "customer_name": customer_name, "account_id": account_id,
               "amount": amount, "term_months": term_months, "status": "CREATED",
               "submitted_at": _now(), "updated_at": _now()}
        self._loans[loan_id] = row
        self._insert("loan_request", row)
        self.audit(loan_id, "loan-api", "CREATE_LOAN", f"amount={amount}, term={term_months}")
        return row

    def set_status(self, loan_id: str, status: str):
        if loan_id in self._loans:
            self._loans[loan_id]["status"] = status
            self._loans[loan_id]["updated_at"] = _now()
        if not self._demo:
            sql = f"UPDATE `{self._t('loan_request')}` SET status=@s, updated_at=CURRENT_TIMESTAMP() WHERE loan_id=@id"
            from google.cloud import bigquery
            self._client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("s", "STRING", status),
                bigquery.ScalarQueryParameter("id", "STRING", loan_id),
            ])).result()
        self.audit(loan_id, "loan-api", "STATUS_CHANGE", status)

    def save_profile(self, loan_id: str, profile: dict):
        row = {"profile_id": new_id("prof"), "loan_id": loan_id, **profile, "generated_at": _now()}
        self._profiles[loan_id] = row
        self._insert("credit_profile", row)
        self.audit(loan_id, "credit-agent", "CREDIT_PROFILE", f"score={profile.get('credit_score')}")

    def save_risk(self, loan_id: str, risk_row: dict, overdraft_events: int) -> dict:
        version = sum(1 for r in self._risks if r["loan_id"] == loan_id) + 1
        row = {"assessment_id": new_id("risk"), "loan_id": loan_id, "version": version,
               "overdraft_events": overdraft_events, **risk_row, "created_at": _now()}
        self._risks.append(row)
        self._insert("risk_assessment", row)
        self.audit(loan_id, "approval-agent", "RISK_ASSESSMENT",
                   f"score={risk_row.get('risk_score')}, rec={risk_row.get('recommendation')}")
        return row

    # --- decisions (APPEND-ONLY) --------------------------------------------
    def record_decision(self, loan_id: str, decision: str, approver: str,
                        rationale: str = "", counteroffer_amount: float | None = None) -> dict:
        version = sum(1 for d in self._decisions if d["loan_id"] == loan_id) + 1
        row = {"decision_id": new_id("dec"), "loan_id": loan_id, "version": version,
               "decision": decision, "counteroffer_amount": counteroffer_amount,
               "approver": approver, "rationale": rationale, "decided_at": _now()}
        self._decisions.append(row)
        self._insert("approval_decision", row)  # INSERT-only: full history preserved
        status = {"APPROVE": "APPROVED", "REJECT": "REJECTED",
                  "REQUEST_MODIFICATION": "MODIFIED", "COUNTEROFFER": "MODIFIED"}.get(decision, "PENDING_APPROVAL")
        self.set_status(loan_id, status)
        self.audit(loan_id, approver, f"DECISION_{decision}", rationale)
        return row

    # --- reads ---------------------------------------------------------------
    def get_loan(self, loan_id: str) -> dict | None:
        if self._demo:
            loan = self._loans.get(loan_id)
            if not loan:
                return None
            risks = [r for r in self._risks if r["loan_id"] == loan_id]
            decs = [d for d in self._decisions if d["loan_id"] == loan_id]
            return {**loan,
                    "risk": risks[-1] if risks else None,
                    "decisions": decs}
        sql = f"SELECT * FROM `{self._t('loan_status')}` WHERE loan_id=@id"
        from google.cloud import bigquery
        rows = list(self._client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("id", "STRING", loan_id)])).result())
        return dict(rows[0]) if rows else None

    def list_loans(self, status: str | None = None) -> list[dict]:
        if self._demo:
            items = list(self._loans.values())
            return [x for x in items if not status or x["status"] == status]
        clause = "WHERE status=@s" if status else ""
        from google.cloud import bigquery
        params = [bigquery.ScalarQueryParameter("s", "STRING", status)] if status else []
        sql = f"SELECT * FROM `{self._t('loan_status')}` {clause} ORDER BY submitted_at DESC LIMIT 200"
        return [dict(r) for r in self._client.query(
            sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()]

    def get_audit(self, loan_id: str) -> list[dict]:
        if self._demo:
            return [a for a in self._audit if a["loan_id"] == loan_id]
        from google.cloud import bigquery
        sql = f"SELECT * FROM `{self._t('loan_audit_log')}` WHERE loan_id=@id ORDER BY event_time"
        return [dict(r) for r in self._client.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", loan_id)])).result()]
