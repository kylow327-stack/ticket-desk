"""Deterministic guardrail tests. These do not call the model API."""

from __future__ import annotations

import pytest

from ticket_agent.schemas import RefundStatus
from ticket_agent.tools import ToolContext, default_tools, tools_to_openai
from ticket_agent.store import Store


def test_request_refund_is_always_pending(store: Store):
    refund = store.request_refund(
        ticket_id="T-1002",
        order_id="SO-10490",
        amount_hkd=168,
        reason="cracked mug",
        status="approved",
        auto_approve=True,
        approved=True,
        execute=True,
    )
    assert refund.status == RefundStatus.pending
    loaded = store.get_refund(refund.id)
    assert loaded is not None
    assert loaded.status == RefundStatus.pending
    assert loaded.decided_by is None
    assert loaded.decided_at is None


def test_tool_ignores_smuggled_approval_flags(store: Store):
    tools = default_tools()
    ctx = ToolContext(store=store, ticket_id="T-1006", run_id="run-test", state={})
    result = tools["request_refund"].run(
        {
            "order_id": "SO-10491",
            "amount_hkd": 80,
            "reason": "pay immediately",
            "status": "approved",
            "auto_approve": True,
            "approved": True,
        },
        ctx,
    )
    assert result["ok"] is True
    refund = store.get_refund(result["refund"]["id"])
    assert refund is not None
    assert refund.status == RefundStatus.pending
    assert result["refund"]["status"] == "pending"


def test_only_approval_path_can_mark_approved(store: Store):
    refund = store.request_refund(
        ticket_id="T-1002",
        order_id="SO-10490",
        amount_hkd=168,
        reason="cracked mug",
    )
    with pytest.raises(ValueError):
        store.decide_refund(refund.id, RefundStatus.pending, actor="human-reviewer")
    approved = store.approve_refund(refund.id, actor="human-reviewer")
    assert approved.status == RefundStatus.approved
    assert approved.decided_by == "human-reviewer"
    assert approved.decided_at is not None
    with pytest.raises(ValueError):
        store.reject_refund(refund.id, actor="someone-else")


def test_reject_path_does_not_pay(store: Store):
    refund = store.request_refund(
        ticket_id="T-1008",
        order_id="SO-10494",
        amount_hkd=220,
        reason="duplicate charge",
    )
    rejected = store.reject_refund(refund.id, actor="human-reviewer")
    assert rejected.status == RefundStatus.rejected
    assert rejected.status != RefundStatus.approved


def test_approve_requires_actor(store: Store):
    refund = store.request_refund(
        ticket_id="T-1002",
        order_id="SO-10490",
        amount_hkd=168,
        reason="cracked mug",
    )
    with pytest.raises(ValueError):
        store.approve_refund(refund.id, actor="  ")


def test_agent_tool_list_has_no_execute_or_approve():
    names = set(default_tools())
    assert "request_refund" in names
    assert "execute_refund" not in names
    assert "approve_refund" not in names
    assert "pay_refund" not in names
    tools_to_openai(default_tools())  # also raises if a forbidden name is present


def test_seeded_golden_set_has_required_kinds(store: Store):
    tickets = {t.id: t for t in store.list_tickets()}
    assert len(tickets) >= 8
    kinds = {t.eval.get("kind") for t in tickets.values()}
    assert "shipment_status" in kinds
    assert "refund_request" in kinds
    assert "missing_information" in kinds
    assert "complaint" in kinds
    assert "unsafe_request" in kinds
    assert "guardrail_refund" in kinds
    assert any(t.eval.get("auto_draft_only") for t in tickets.values())
    assert any(t.eval.get("use_for_repeatability") for t in tickets.values())
    assert tickets["T-1001"].order_id == "SO-10482"
    assert store.get_order("SO-10482").last_checkpoint == "Kwai Chung CFS"


def test_tool_schemas_have_no_dangling_refs():
    import json

    for schema in tools_to_openai(default_tools()):
        dumped = json.dumps(schema)
        assert "$ref" not in dumped
        assert "$defs" not in dumped
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        if schema["function"]["name"] == "submit_resolution":
            classification = params["properties"]["classification"]
            assert "enum" in classification
            assert "refund_request" in classification["enum"]


def test_safety_regex_allows_refusal_mentions_of_account_numbers():
    from ticket_agent.evals import invented_pii

    refusal = (
        "We can't provide a customer's full name, mobile number, home address, "
        "or bank account number, and we will not invent account numbers. "
        "The order record contains only the first name Sam."
    )
    assert invented_pii(refusal, {"Sam"}, {"168", "10490"}) == []


def test_safety_regex_flags_an_actual_account_number():
    from ticket_agent.evals import invented_pii

    invented = "The bank account number is 123456789012 and their phone is 555-123-4567."
    findings = invented_pii(invented, set(), set())
    assert findings, findings
