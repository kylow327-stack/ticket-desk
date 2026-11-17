"""Create the Ticket Desk golden dataset in LangSmith and run an experiment."""

from __future__ import annotations

import os
from typing import Any, Optional

from ticket_agent.agent import run_agent
from ticket_agent.config import get_settings, project_root
from ticket_agent.evals import score_ticket
from ticket_agent.langsmith_tracing import DEFAULT_PROJECT, configure_tracing, tracing_enabled
from ticket_agent.llm import OpenAIChatClient
from ticket_agent.schemas import AgentResult, Classification, RefundStatus, Usage
from ticket_agent.store import Store

DATASET_NAME = "ticket-desk-golden"
EXPERIMENT_PREFIX = "ticket-desk-golden"


def _examples(store: Store) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for ticket in store.list_tickets():
        ev = ticket.eval or {}
        examples.append(
            {
                "inputs": {"ticket_id": ticket.id},
                "outputs": {
                    "kind": ev.get("kind"),
                    "expects_refund": bool(ev.get("expects_refund")),
                    "allowed_classifications": ev.get("allowed_classifications") or [],
                    "required_phrases": ev.get("required_phrases") or [],
                },
                "metadata": {"ticket_id": ticket.id, "kind": ev.get("kind")},
            }
        )
    return examples


def ensure_dataset(client: Any, store: Store) -> Any:
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        existing = list(client.list_examples(dataset_id=dataset.id))
        if not existing:
            client.create_examples(dataset_id=dataset.id, examples=_examples(store))
        return dataset
    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description=(
            "Ticket Desk golden tickets. Evaluators reuse the local groundedness, "
            "completeness, safety, and guardrail checks."
        ),
    )
    client.create_examples(dataset_id=dataset.id, examples=_examples(store))
    return dataset


def _result_from_outputs(outputs: dict[str, Any]) -> AgentResult:
    classification = outputs.get("classification")
    parsed = None
    if classification:
        try:
            parsed = Classification(classification)
        except ValueError:
            parsed = Classification.general
    refund_status = outputs.get("refund_status")
    parsed_refund = None
    if refund_status:
        try:
            parsed_refund = RefundStatus(refund_status)
        except ValueError:
            parsed_refund = None
    usage_raw = outputs.get("usage") or {}
    return AgentResult(
        run_id=str(outputs.get("run_id") or "langsmith-eval"),
        ticket_id=str(outputs.get("ticket_id") or ""),
        classification=parsed,
        draft_reply=outputs.get("draft_reply"),
        refund_id=outputs.get("refund_id"),
        refund_status=parsed_refund,
        step_count=int(outputs.get("step_count") or 0),
        stop_reason=str(outputs.get("stop_reason") or "unknown"),
        usage=Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
            total_tokens=int(usage_raw.get("total_tokens") or 0),
            cached_tokens=int(usage_raw.get("cached_tokens") or 0),
        ),
        latency_ms=int(outputs.get("latency_ms") or 0),
        error=outputs.get("error"),
        steps=[],
    )


def make_target(store: Store, client: OpenAIChatClient):
    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        ticket_id = inputs["ticket_id"]
        result = run_agent(ticket_id, store=store, client=client)
        return {
            "ticket_id": result.ticket_id,
            "run_id": result.run_id,
            "classification": result.classification.value if result.classification else None,
            "draft_reply": result.draft_reply,
            "refund_id": result.refund_id,
            "refund_status": result.refund_status.value if result.refund_status else None,
            "stop_reason": result.stop_reason,
            "step_count": result.step_count,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "usage": result.usage.model_dump(),
        }

    return target


def make_evaluators(store: Store):
    def score_existing_checks(outputs: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
        ticket = store.get_ticket(inputs["ticket_id"])
        if ticket is None:
            return {"results": [{"key": "run", "score": 0.0, "comment": "Unknown ticket id"}]}
        result = _result_from_outputs({**outputs, "ticket_id": inputs["ticket_id"]})
        checks = score_ticket(ticket, result, store)
        return {
            "results": [
                {"key": check.name, "score": 1.0 if check.passed else 0.0, "comment": check.detail}
                for check in checks
            ]
        }

    return [score_existing_checks]


def run_langsmith_evals(store: Optional[Store] = None) -> dict[str, Any]:
    """Create or reuse the golden dataset and run evaluate(). Requires DEEPSEEK_API_KEY."""
    from langsmith import Client

    settings = get_settings()
    if not settings.api_key_present():
        raise RuntimeError("DEEPSEEK_API_KEY is not set; cannot run live LangSmith evals.")
    if not os.environ.get("LANGSMITH_API_KEY", "").strip():
        raise RuntimeError("LANGSMITH_API_KEY is not set; cannot upload a LangSmith experiment.")

    os.environ["LANGSMITH_TRACING"] = "true"
    configure_tracing(resolve_workspace=True)
    if not os.environ.get("LANGSMITH_WORKSPACE_ID", "").strip():
        raise RuntimeError(
            "LANGSMITH_WORKSPACE_ID is not set. Organisation-scoped service keys need the "
            "workspace id from LangSmith Settings → General. A personal org with a single "
            "workspace is resolved automatically when the API can list it."
        )

    if store is None:
        db_path = get_settings().data_dir / "langsmith-eval.db"
        if db_path.exists():
            db_path.unlink()
        store = Store(db_path)
        store.seed_from_fixtures(reset_operational=True)

    ls_client = Client()
    if not ls_client.has_project(DEFAULT_PROJECT):
        ls_client.create_project(
            DEFAULT_PROJECT,
            description="Ticket Desk support-triage LangGraph demo. Traces and golden evals.",
        )
    project = ls_client.read_project(project_name=DEFAULT_PROJECT)
    dataset = ensure_dataset(ls_client, store)
    chat = OpenAIChatClient(settings)
    results = ls_client.evaluate(
        make_target(store, chat),
        data=DATASET_NAME,
        evaluators=make_evaluators(store),
        experiment_prefix=EXPERIMENT_PREFIX,
        description="Golden ticket checks: groundedness, completeness, safety, and the refund guardrail.",
        max_concurrency=1,
        metadata={"model": settings.model, "project": DEFAULT_PROJECT},
    )
    return {
        "project": DEFAULT_PROJECT,
        "project_url": project.url,
        "dataset": DATASET_NAME,
        "dataset_url": dataset.url,
        "experiment_name": results.experiment_name,
        "experiment_id": str(results.experiment_id) if results.experiment_id else None,
        "experiment_url": results.url,
        "tracing_enabled": tracing_enabled(),
    }


def render_langsmith_summary(payload: dict[str, Any]) -> str:
    lines = [
        "LangSmith experiment submitted.",
        f"Project: {payload.get('project_url') or payload.get('project')}",
        f"Dataset: {payload.get('dataset_url') or payload.get('dataset')}",
        f"Experiment: {payload.get('experiment_url') or payload.get('experiment_name')}",
        "Repeatability remains in the local live eval runner (`python -m ticket_agent.evals`).",
    ]
    return "\n".join(lines) + "\n"
