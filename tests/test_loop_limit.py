"""Loop limit is enforced with a stubbed client. No API calls."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ticket_agent.agent import FALLBACK_DRAFT, run_agent
from ticket_agent.llm import ScriptedClient
from ticket_agent.tools import ToolContext, ToolSpec, default_tools


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _loop_call(call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": "loop_forever", "arguments": "{}"},
            }
        ],
    }


def test_loop_limit_stops_after_max_steps(store, tmp_path, monkeypatch):
    monkeypatch.setenv("TICKET_AGENT_TRACES", str(tmp_path / "runs.jsonl"))

    def ping(_args, _ctx: ToolContext):
        return {"ok": True, "message": "please call loop_forever again"}

    tools = default_tools()
    tools["loop_forever"] = ToolSpec(
        name="loop_forever",
        description="A stub that always asks to be called again.",
        args_model=EmptyArgs,
        handler=ping,
    )

    client = ScriptedClient([_loop_call(f"call_{i}") for i in range(1, 12)])
    result = run_agent(
        "T-1001",
        store=store,
        client=client,
        tools=tools,
        max_steps=5,
    )
    assert result.stop_reason == "loop_limit"
    assert result.step_count == 5
    assert client.calls == 5
    assert result.draft_reply == FALLBACK_DRAFT
    assert result.refund_id is None
    assert len(client.script) == 6


def test_submit_stops_before_hitting_the_limit(store, tmp_path, monkeypatch):
    monkeypatch.setenv("TICKET_AGENT_TRACES", str(tmp_path / "runs.jsonl"))

    client = ScriptedClient(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_submit",
                        "type": "function",
                        "function": {
                            "name": "submit_resolution",
                            "arguments": (
                                '{"classification":"general","draft_reply":'
                                '"Thanks, we will close this ticket.","rationale":"no action",'
                                '"needs_human_approval":false}'
                            ),
                        },
                    }
                ],
            }
        ]
    )
    result = run_agent("T-1009", store=store, client=client, max_steps=5)
    assert result.stop_reason == "submitted"
    assert result.step_count == 1
    assert result.classification is not None
    assert "close this ticket" in (result.draft_reply or "")
    assert client.calls == 1
