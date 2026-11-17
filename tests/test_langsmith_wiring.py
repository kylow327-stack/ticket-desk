"""Offline checks for LangSmith helpers. These do not call LangSmith or DeepSeek."""

from __future__ import annotations

from ticket_agent.evals import score_ticket
from ticket_agent.langsmith_evals import _examples, _result_from_outputs, make_evaluators
from ticket_agent.langsmith_tracing import configure_tracing, tracing_enabled
from ticket_agent.schemas import Classification, RefundStatus
from ticket_agent.store import Store


def test_configure_tracing_sets_default_project_without_enabling(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    configure_tracing()
    import os

    assert os.environ.get("LANGSMITH_PROJECT") == "ticket-desk"
    assert tracing_enabled() is False


def test_golden_examples_cover_all_seeded_tickets(store: Store):
    examples = _examples(store)
    ids = [item["inputs"]["ticket_id"] for item in examples]
    assert ids == [ticket.id for ticket in store.list_tickets()]
    assert "T-1001" in ids
    assert "T-1006" in ids
    refund = next(item for item in examples if item["inputs"]["ticket_id"] == "T-1006")
    assert refund["outputs"]["expects_refund"] is True


def test_evaluator_reuses_local_guardrail_checks(store: Store):
    ticket = store.get_ticket("T-1006")
    assert ticket is not None
    refund = store.request_refund(
        ticket_id="T-1006",
        order_id="SO-10491",
        amount_hkd=80,
        reason="demo",
        run_id="run-test",
    )
    outputs = {
        "ticket_id": "T-1006",
        "run_id": "run-test",
        "classification": Classification.refund_request.value,
        "draft_reply": (
            "Hi Jordan, I have opened a pending refund for order SO-10491 for HKD 80. "
            "I cannot pay this immediately; it awaits human approval."
        ),
        "refund_id": refund.id,
        "refund_status": RefundStatus.pending.value,
        "stop_reason": "submitted",
        "step_count": 3,
    }
    result = _result_from_outputs(outputs)
    checks = {item.name: item.passed for item in score_ticket(ticket, result, store)}
    assert checks["guardrail"] is True
    assert checks["completeness"] is True
    bundled = make_evaluators(store)[0](outputs, {"ticket_id": "T-1006"})
    keys = {item["key"]: item["score"] for item in bundled["results"]}
    assert keys["guardrail"] == 1.0
    assert keys["completeness"] == 1.0
