"""FastAPI app with a simple server-rendered UI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ticket_agent.agent import run_agent
from ticket_agent.config import get_settings, project_root
from ticket_agent.store import get_store
from ticket_agent.traces import TraceLogger

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
app = FastAPI(title="Ticket Guardrail Agent", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")


@app.on_event("startup")
def _startup() -> None:
    from ticket_agent.langsmith_tracing import api_key_present, configure_tracing, tracing_requested

    if tracing_requested() and api_key_present():
        configure_tracing(resolve_workspace=True)
    get_store().seed_from_fixtures()


def _ctx(request: Request, **extra):
    store = get_store()
    pending = store.list_refunds(status="pending")
    base = {
        "request": request,
        "pending_count": len(pending),
        "model": get_settings().model,
        "key_present": get_settings().api_key_present(),
    }
    base.update(extra)
    return base


@app.get("/health")
def health():
    settings = get_settings()
    return {"ok": True, "model": settings.model, "key_present": settings.api_key_present()}


@app.get("/", response_class=HTMLResponse)
def ticket_list(request: Request):
    store = get_store()
    rows = []
    for ticket in store.list_tickets():
        last = store.latest_run_for_ticket(ticket.id)
        refunds = store.list_refunds(ticket_id=ticket.id)
        pending = [r for r in refunds if r.status.value == "pending"]
        rows.append({"ticket": ticket, "last": last, "pending": pending, "refunds": refunds})
    return templates.TemplateResponse(request, "tickets.html", _ctx(request, rows=rows, page="tickets"))


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse)
def ticket_detail(request: Request, ticket_id: str, error: Optional[str] = None):
    store = get_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    order = store.get_order(ticket.order_id) if ticket.order_id else None
    last = store.latest_run_for_ticket(ticket.id)
    refunds = store.list_refunds(ticket_id=ticket.id)
    trace = None
    if last:
        trace = TraceLogger(get_settings().traces_path).get(last["id"])
    return templates.TemplateResponse(
        request,
        "ticket.html",
        _ctx(
            request,
            page="tickets",
            ticket=ticket,
            order=order,
            last=last,
            refunds=refunds,
            trace=trace,
            error=error,
        ),
    )


@app.post("/tickets/{ticket_id}/run")
def run_ticket(ticket_id: str):
    store = get_store()
    if not store.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    settings = get_settings()
    if not settings.api_key_present():
        return RedirectResponse(f"/tickets/{ticket_id}?error=missing-key", status_code=303)
    try:
        run_agent(ticket_id, store=store)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/tickets/{ticket_id}?error=run-failed", status_code=303)
    return RedirectResponse(f"/tickets/{ticket_id}", status_code=303)


@app.get("/refunds", response_class=HTMLResponse)
def refund_list(request: Request):
    store = get_store()
    refunds = store.list_refunds()
    return templates.TemplateResponse(request, "refunds.html", _ctx(request, page="refunds", refunds=refunds))


@app.post("/refunds/{refund_id}/approve")
def approve_refund(refund_id: str, actor: str = Form("human-reviewer"), next: str = Form("/refunds")):
    store = get_store()
    try:
        store.approve_refund(refund_id, actor=actor)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(next, status_code=303)


@app.post("/refunds/{refund_id}/reject")
def reject_refund(refund_id: str, actor: str = Form("human-reviewer"), next: str = Form("/refunds")):
    store = get_store()
    try:
        store.reject_refund(refund_id, actor=actor)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(next, status_code=303)


@app.get("/traces", response_class=HTMLResponse)
def traces(request: Request):
    store = get_store()
    runs = store.list_runs(limit=200)
    return templates.TemplateResponse(request, "traces.html", _ctx(request, page="traces", runs=runs))


@app.get("/traces/{run_id}", response_class=HTMLResponse)
def trace_detail(request: Request, run_id: str):
    store = get_store()
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    trace = TraceLogger(get_settings().traces_path).get(run_id)
    pretty = json.dumps(trace or run, indent=2, ensure_ascii=False, default=str)
    return templates.TemplateResponse(
        request,
        "trace.html",
        _ctx(request, page="traces", run=run, trace=trace, pretty=pretty),
    )


@app.get("/evals", response_class=HTMLResponse)
def evals_page(request: Request):
    path = project_root() / "evals" / "latest-results.md"
    json_path = project_root() / "evals" / "latest-results.json"
    markdown = path.read_text(encoding="utf-8") if path.exists() else None
    summary = None
    if json_path.exists():
        summary = json.loads(json_path.read_text(encoding="utf-8"))
    return templates.TemplateResponse(
        request,
        "evals.html",
        _ctx(request, page="evals", markdown=markdown, summary=summary),
    )


@app.post("/demo/reset")
def reset_demo():
    store = get_store()
    store.seed_from_fixtures(reset_operational=True)
    traces = get_settings().traces_path
    if traces.exists():
        traces.write_text("", encoding="utf-8")
    return RedirectResponse("/", status_code=303)
