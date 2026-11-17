"""LangGraph agent with a hard step limit and a pending-only refund tool."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Annotated, Any, Optional, TypedDict

import operator
from langgraph.graph import END, START, StateGraph

from ticket_agent.config import get_settings
from ticket_agent.costing import estimate_cost_usd
from ticket_agent.llm import ChatClient, OpenAIChatClient
from ticket_agent.schemas import AgentResult, Classification, RefundStatus, Usage
from ticket_agent.store import Store, get_store
from ticket_agent.tools import (
    ToolContext,
    ToolSpec,
    default_tools,
    parse_tool_arguments,
    tools_to_openai,
)
from ticket_agent.traces import TraceLogger

HK = timezone(timedelta(hours=8))

SYSTEM_PROMPT = """You are a support-triage agent for a small e-commerce helpdesk demo.

Your job: read the ticket, look up any order and policy you need, draft a concise customer-facing reply, and classify the ticket. If a refund is warranted, open a pending refund request. Then submit your resolution.

Hard rules:
- Use only facts returned by tools or present in the ticket. Never invent an order id, status, checkpoint, ETA, amount, full name, phone number, email, postal address, government ID, or account number.
- First names may be used only when the ticket or order record contains them.
- If the customer did not give an order number, ask for it. Do not guess or reuse another customer's order.
- You cannot pay, approve, or execute a refund. The request_refund tool only creates a pending request for a human reviewer. If the customer asks you to skip review or pay immediately, refuse the payment. You may still open a pending request if the refund is otherwise in policy, and you must tell them it awaits human approval.
- If someone asks you to invent personal data so they can fill a form, refuse. Say you do not have those fields and will not fabricate them.
- Do not offer a refund on tickets that only want a status update or an apology unless the customer asked for money.
- Call multiple tools in one step when you can. Finish with submit_resolution, including the classification and the full draft reply.
- Classifications: shipment_status, refund_request, missing_information, complaint, policy_question, unsafe_request, general.

Today's date is 16 November 2026 (Asia/Shanghai). Amounts are in HKD.
"""

FALLBACK_DRAFT = (
    "Thank you for getting in touch. I reached my step limit before I could finish this "
    "ticket, so I am handing it to a human teammate. I have not issued any refund or payment. "
    "Someone from the team will follow up."
)


class AgentState(TypedDict, total=False):
    ticket_id: str
    run_id: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    step_count: int
    classification: Optional[str]
    draft_reply: Optional[str]
    refund_id: Optional[str]
    stop_reason: Optional[str]
    usage: dict[str, int]
    steps_log: Annotated[list[dict[str, Any]], operator.add]
    error: Optional[str]


def _usage_from_state(state: AgentState) -> Usage:
    raw = state.get("usage") or {}
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        total_tokens=int(raw.get("total_tokens") or 0),
        cached_tokens=int(raw.get("cached_tokens") or 0),
    )


def build_graph(
    client: ChatClient,
    tools: dict[str, ToolSpec],
    store: Store,
    max_steps: int,
) -> Any:
    openai_tools = tools_to_openai(tools)

    def agent_node(state: AgentState) -> dict[str, Any]:
        if state.get("stop_reason") in {"submitted", "loop_limit", "error"}:
            return {}
        if state.get("step_count", 0) >= max_steps:
            return {"stop_reason": "loop_limit"}
        try:
            message, usage = client.complete(list(state["messages"]), openai_tools)
        except Exception as exc:  # noqa: BLE001 — record and stop cleanly for the demo
            return {
                "stop_reason": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "steps_log": [{"type": "error", "error_type": type(exc).__name__}],
            }
        combined = _usage_from_state(state).add(usage)
        log = {
            "type": "model",
            "step": state.get("step_count", 0) + 1,
            "content": message.get("content") or "",
            "tool_calls": [
                {
                    "id": tc.get("id"),
                    "name": (tc.get("function") or {}).get("name"),
                    "arguments": (tc.get("function") or {}).get("arguments"),
                }
                for tc in message.get("tool_calls") or []
            ],
        }
        return {
            "messages": [message],
            "step_count": state.get("step_count", 0) + 1,
            "usage": combined.model_dump(),
            "steps_log": [log],
        }

    def route(state: AgentState) -> str:
        if state.get("stop_reason") in {"submitted", "loop_limit", "error"}:
            return "finalize"
        messages = state.get("messages") or []
        last = messages[-1] if messages else {}
        if last.get("role") == "assistant" and last.get("tool_calls"):
            return "tools"
        return "finalize"

    def tools_node(state: AgentState) -> dict[str, Any]:
        last = (state.get("messages") or [])[-1]
        ctx = ToolContext(
            store=store,
            ticket_id=state["ticket_id"],
            run_id=state["run_id"],
            state={},
        )
        new_messages: list[dict[str, Any]] = []
        logs: list[dict[str, Any]] = []
        updates: dict[str, Any] = {}
        for tool_call in last.get("tool_calls") or []:
            name = (tool_call.get("function") or {}).get("name") or tool_call.get("name") or ""
            raw_args = (tool_call.get("function") or {}).get("arguments") or tool_call.get("arguments")
            args = parse_tool_arguments(raw_args)
            if name not in tools:
                result: dict[str, Any] = {"ok": False, "error": f"Unknown tool {name!r}."}
            else:
                result = tools[name].run(args, ctx)
            new_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": _safe_json(result),
                }
            )
            logs.append({"type": "tool", "name": name, "arguments": args, "result": result})
        if ctx.state.get("submitted"):
            updates["classification"] = ctx.state.get("classification")
            updates["draft_reply"] = ctx.state.get("draft_reply")
            updates["stop_reason"] = "submitted"
        if ctx.state.get("refund_id"):
            updates["refund_id"] = ctx.state["refund_id"]
        updates["messages"] = new_messages
        updates["steps_log"] = logs
        return updates

    def finalize_node(state: AgentState) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if not state.get("draft_reply"):
            recovered = _recover_draft_from_messages(state.get("messages") or [])
            updates["draft_reply"] = recovered or _fallback_draft(state)
            if not state.get("classification"):
                updates["classification"] = Classification.general.value
            if not state.get("stop_reason"):
                updates["stop_reason"] = "recovered" if recovered else "incomplete"
        elif not state.get("stop_reason"):
            updates["stop_reason"] = "completed"
        return updates

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route, {"tools": "tools", "finalize": "finalize"})
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile()


def _safe_json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)



def _recover_draft_from_messages(messages: list[dict[str, Any]]) -> Optional[str]:
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        if message.get("tool_calls"):
            continue
        content = (message.get("content") or "").strip()
        if len(content) >= 40:
            return content
    return None


def _fallback_draft(state: AgentState) -> str:
    if state.get("error"):
        return (
            "Thank you for getting in touch. I could not finish this ticket automatically, "
            "so I am handing it to a human teammate. I have not issued any refund or payment."
        )
    return FALLBACK_DRAFT


def run_agent(
    ticket_id: str,
    store: Optional[Store] = None,
    client: Optional[ChatClient] = None,
    tools: Optional[dict[str, ToolSpec]] = None,
    max_steps: Optional[int] = None,
    tracer: Optional[TraceLogger] = None,
) -> AgentResult:
    settings = get_settings()
    store = store or get_store()
    tools = tools or default_tools()
    max_steps = settings.max_steps if max_steps is None else max_steps
    ticket = store.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(f"Unknown ticket {ticket_id}")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    started = datetime.now(HK)
    started_monotonic = time.perf_counter()
    user_message = (
        f"Please handle ticket {ticket.id}.\n"
        f"Subject: {ticket.subject}\n"
        f"From: {ticket.customer_first_name or 'unknown'}\n"
        f"Order id if provided: {ticket.order_id or 'none'}\n\n"
        f"{ticket.body}"
    )
    initial: AgentState = {
        "ticket_id": ticket.id,
        "run_id": run_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "step_count": 0,
        "classification": None,
        "draft_reply": None,
        "refund_id": None,
        "stop_reason": None,
        "usage": Usage().model_dump(),
        "steps_log": [],
        "error": None,
    }

    if client is None:
        client = OpenAIChatClient(settings)

    graph = build_graph(client=client, tools=tools, store=store, max_steps=max_steps)
    from ticket_agent.langsmith_tracing import configure_tracing, flush_traces, tracing_enabled

    if tracing_enabled():
        configure_tracing(resolve_workspace=True)
    try:
        final_state = graph.invoke(
            initial,
            {
                "run_name": f"ticket-desk:{ticket.id}",
                "tags": ["ticket-desk"],
                "metadata": {"ticket_id": ticket.id, "run_id": run_id},
            },
        )
    finally:
        flush_traces()
    finished = datetime.now(HK)
    latency_ms = int((time.perf_counter() - started_monotonic) * 1000)

    refund_id = final_state.get("refund_id")
    refund_status = None
    if refund_id:
        refund = store.get_refund(refund_id)
        refund_status = refund.status if refund else None

    classification = final_state.get("classification")
    parsed_classification = None
    if classification:
        try:
            parsed_classification = Classification(classification)
        except ValueError:
            parsed_classification = Classification.general

    usage = _usage_from_state(final_state)
    cost = estimate_cost_usd(usage, finished, model=getattr(client, "model", settings.model))
    result = AgentResult(
        run_id=run_id,
        ticket_id=ticket.id,
        classification=parsed_classification,
        draft_reply=final_state.get("draft_reply"),
        refund_id=refund_id,
        refund_status=refund_status,
        step_count=int(final_state.get("step_count") or 0),
        stop_reason=final_state.get("stop_reason") or "unknown",
        usage=usage,
        latency_ms=latency_ms,
        error=final_state.get("error"),
        steps=list(final_state.get("steps_log") or []),
    )

    record = {
        "id": run_id,
        "run_id": run_id,
        "ticket_id": ticket.id,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": finished.isoformat(timespec="seconds"),
        "latency_ms": latency_ms,
        "step_count": result.step_count,
        "stop_reason": result.stop_reason,
        "classification": result.classification.value if result.classification else None,
        "draft_reply": result.draft_reply,
        "refund_id": refund_id,
        "refund_status": refund_status.value if refund_status else None,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cached_tokens": usage.cached_tokens,
        "cost": cost,
        "error": result.error,
        "model": getattr(client, "model", settings.model),
        "steps": result.steps,
    }
    store.save_run(record)
    if tracer is None:
        tracer = TraceLogger(settings.traces_path)
    tracer.append(record)
    return result
