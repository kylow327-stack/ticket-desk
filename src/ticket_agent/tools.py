"""Agent tools. There is no tool that pays money or approves a refund."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pydantic import BaseModel, ValidationError


def inline_json_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline $defs so tool schemas are valid for DeepSeek/OpenAI (no dangling $ref)."""
    defs = dict(schema.get("$defs") or schema.get("definitions") or {})

    def resolve(obj: Any, seen: frozenset[str] = frozenset()) -> Any:
        if isinstance(obj, dict):
            ref = obj.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/"):
                name = ref.split("/")[-1]
                if name in defs and name not in seen:
                    return resolve(copy.deepcopy(defs[name]), seen | {name})
            return {k: resolve(v, seen) for k, v in obj.items() if k not in {"$defs", "definitions"}}
        if isinstance(obj, list):
            return [resolve(item, seen) for item in obj]
        return obj

    return resolve(schema)

from ticket_agent.schemas import (
    GetPolicyArgs,
    LookupOrderArgs,
    ReadTicketArgs,
    RequestRefundArgs,
    SubmitResolutionArgs,
)
from ticket_agent.store import Store


@dataclass
class ToolContext:
    store: Store
    ticket_id: str
    run_id: str
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], dict[str, Any]]

    def openai_schema(self) -> dict[str, Any]:
        schema = inline_json_schema_refs(self.args_model.model_json_schema())
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": schema.get("properties") or {},
                    "required": schema.get("required") or [],
                    "additionalProperties": False,
                },
            },
        }

    def run(self, raw_args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
        try:
            parsed = self.args_model.model_validate(raw_args or {})
        except ValidationError as exc:
            return {"ok": False, "error": f"Invalid arguments for {self.name}: {exc}"}
        return self.handler(parsed, ctx)


def _read_ticket(args: ReadTicketArgs, ctx: ToolContext) -> dict[str, Any]:
    ticket_id = args.ticket_id or ctx.ticket_id
    ticket = ctx.store.get_ticket(ticket_id)
    if not ticket:
        return {"ok": False, "error": f"Ticket {ticket_id} was not found."}
    return {
        "ok": True,
        "ticket": {
            "id": ticket.id,
            "subject": ticket.subject,
            "body": ticket.body,
            "created_at": ticket.created_at,
            "customer_first_name": ticket.customer_first_name,
            "order_id": ticket.order_id,
        },
    }


def _lookup_order(args: LookupOrderArgs, ctx: ToolContext) -> dict[str, Any]:
    order = ctx.store.get_order(args.order_id.strip())
    if not order:
        return {"ok": False, "error": f"Order {args.order_id} was not found."}
    return {
        "ok": True,
        "order": {
            "id": order.id,
            "status": order.status,
            "last_checkpoint": order.last_checkpoint,
            "eta": order.eta,
            "delivered_at": order.delivered_at,
            "item_summary": order.item_summary,
            "amount_hkd": order.amount_hkd,
            "currency": order.currency,
            "customer_first_name": order.customer_first_name,
            "payment_note": order.payment_note,
        },
    }


def _get_policy(args: GetPolicyArgs, ctx: ToolContext) -> dict[str, Any]:
    topic = args.topic.strip().lower()
    if topic in {"all", "*", "any"}:
        return {
            "ok": True,
            "policies": [
                {"id": p.id, "title": p.title, "body": p.body} for p in ctx.store.list_policies()
            ],
        }
    policy = ctx.store.get_policy(topic)
    if not policy:
        known = [p.id for p in ctx.store.list_policies()]
        return {"ok": False, "error": f"Unknown policy topic {topic!r}. Known topics: {known}"}
    return {"ok": True, "policy": {"id": policy.id, "title": policy.title, "body": policy.body}}


def _request_refund(args: RequestRefundArgs, ctx: ToolContext) -> dict[str, Any]:
    order = ctx.store.get_order(args.order_id.strip())
    if not order:
        return {"ok": False, "error": f"Cannot request a refund: order {args.order_id} was not found."}
    refund = ctx.store.request_refund(
        ticket_id=ctx.ticket_id,
        order_id=order.id,
        amount_hkd=args.amount_hkd,
        reason=args.reason,
        run_id=ctx.run_id,
    )
    ctx.state["refund_id"] = refund.id
    return {
        "ok": True,
        "refund": {
            "id": refund.id,
            "ticket_id": refund.ticket_id,
            "order_id": refund.order_id,
            "amount_hkd": refund.amount_hkd,
            "reason": refund.reason,
            "status": refund.status.value,
        },
        "note": (
            "The refund is pending human approval. It has not been paid and cannot be "
            "marked approved by this tool."
        ),
    }


def _submit_resolution(args: SubmitResolutionArgs, ctx: ToolContext) -> dict[str, Any]:
    ctx.state["classification"] = args.classification.value
    ctx.state["draft_reply"] = args.draft_reply
    ctx.state["rationale"] = args.rationale
    ctx.state["needs_human_approval"] = args.needs_human_approval
    ctx.state["submitted"] = True
    return {
        "ok": True,
        "accepted": True,
        "classification": args.classification.value,
        "needs_human_approval": args.needs_human_approval,
    }


def default_tools() -> dict[str, ToolSpec]:
    tools = [
        ToolSpec(
            name="read_ticket",
            description="Read the support ticket currently being handled, or another ticket by id.",
            args_model=ReadTicketArgs,
            handler=_read_ticket,
        ),
        ToolSpec(
            name="lookup_order",
            description="Look up an order's status, last checkpoint, ETA, item, and amount. Use the order id from the ticket when one is present.",
            args_model=LookupOrderArgs,
            handler=_lookup_order,
        ),
        ToolSpec(
            name="get_policy",
            description="Read a company policy. Topic should be 'refunds', 'privacy', 'shipping', or 'all'.",
            args_model=GetPolicyArgs,
            handler=_get_policy,
        ),
        ToolSpec(
            name="request_refund",
            description=(
                "Create a pending refund request for human approval. This does not pay the customer "
                "and cannot approve or execute a refund. Use only when a refund is warranted."
            ),
            args_model=RequestRefundArgs,
            handler=_request_refund,
        ),
        ToolSpec(
            name="submit_resolution",
            description=(
                "Submit the final classification and the customer-facing draft reply. "
                "Call this once you have enough information. This ends the run."
            ),
            args_model=SubmitResolutionArgs,
            handler=_submit_resolution,
        ),
    ]
    return {t.name: t for t in tools}


FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "execute_refund",
        "approve_refund",
        "pay_refund",
        "issue_payment",
        "mark_approved",
    }
)


def tools_to_openai(tools: dict[str, ToolSpec]) -> list[dict[str, Any]]:
    for name in tools:
        if name in FORBIDDEN_TOOL_NAMES:
            raise RuntimeError(f"Refusing to expose forbidden tool {name}")
    return [spec.openai_schema() for spec in tools.values()]


def parse_tool_arguments(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw, "_parse_error": True}
    return value if isinstance(value, dict) else {"value": value}
