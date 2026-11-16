"""Pydantic schemas for tickets, orders, refunds, and agent results."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class Classification(str, Enum):
    shipment_status = "shipment_status"
    refund_request = "refund_request"
    missing_information = "missing_information"
    complaint = "complaint"
    policy_question = "policy_question"
    unsafe_request = "unsafe_request"
    general = "general"


class RefundStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Ticket(BaseModel):
    id: str
    subject: str
    body: str
    created_at: str
    customer_first_name: Optional[str] = None
    order_id: Optional[str] = None
    eval: dict[str, Any] = Field(default_factory=dict)


class Order(BaseModel):
    id: str
    status: str
    last_checkpoint: Optional[str] = None
    eta: Optional[str] = None
    delivered_at: Optional[str] = None
    item_summary: str
    amount_hkd: float
    currency: str = "HKD"
    customer_first_name: str
    payment_note: Optional[str] = None


class Policy(BaseModel):
    id: str
    title: str
    body: str


class Refund(BaseModel):
    id: str
    ticket_id: str
    order_id: str
    amount_hkd: float
    reason: str
    status: RefundStatus
    requested_at: str
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    run_id: Optional[str] = None


class RequestRefundArgs(BaseModel):
    """Arguments the model may pass. Extra keys such as auto_approve are ignored."""

    model_config = ConfigDict(extra="ignore")

    order_id: str
    amount_hkd: float
    reason: str


class SubmitResolutionArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    classification: Classification
    draft_reply: str = Field(min_length=1)
    rationale: str
    needs_human_approval: bool = False


class ReadTicketArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: Optional[str] = None


class LookupOrderArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    order_id: str


class GetPolicyArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: str = Field(description="One of: refunds, privacy, shipping, or 'all'.")


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


class AgentResult(BaseModel):
    run_id: str
    ticket_id: str
    classification: Optional[Classification] = None
    draft_reply: Optional[str] = None
    refund_id: Optional[str] = None
    refund_status: Optional[RefundStatus] = None
    step_count: int
    stop_reason: str
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    error: Optional[str] = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
