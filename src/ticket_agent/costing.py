"""Approximate USD cost for deepseek-flash from official list prices."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ticket_agent.config import FLASH_PRICING_USD
from ticket_agent.schemas import Usage

# Peak hours: 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday, excluding Chinese public holidays.
# Source: https://api-docs.deepseek.com/quick_start/pricing (retrieved 2026-11-16)
PEAK_WINDOWS = ((1, 4), (6, 10))

# Chinese public holidays that fall on weekdays in late 2026 and are relevant if a run lands on one.
# New Year's Day 2027 is included for completeness. This list is not exhaustive historically.
CN_PUBLIC_HOLIDAYS = {
    "2026-01-01",
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-02-20",
    "2026-04-05",
    "2026-04-06",
    "2026-05-01",
    "2026-06-19",
    "2026-10-01",
    "2026-10-02",
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
    "2027-01-01",
}


def is_peak(when: datetime) -> bool:
    moment = when.astimezone(timezone.utc)
    if moment.date().isoformat() in CN_PUBLIC_HOLIDAYS:
        return False
    if moment.weekday() >= 5:
        return False
    hour = moment.hour
    for start, end in PEAK_WINDOWS:
        if start <= hour < end:
            return True
    return False


def estimate_cost_usd(usage: Usage, when: datetime, model: str = "deepseek-flash") -> dict[str, Any]:
    """Estimate cost. Cache-miss tokens are prompt_tokens minus cached_tokens when available."""
    peak = is_peak(when)
    if model not in {"deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
        return {
            "usd": None,
            "note": f"No list price table coded for model {model!r}; tokens are reported without a dollar estimate.",
            "peak": peak,
        }
    cached = min(usage.cached_tokens, usage.prompt_tokens)
    miss = max(usage.prompt_tokens - cached, 0)
    if peak:
        input_hit = FLASH_PRICING_USD["input_cache_hit_peak"]
        input_miss = FLASH_PRICING_USD["input_cache_miss_peak"]
        output = FLASH_PRICING_USD["output_peak"]
        band = "peak"
    else:
        input_hit = FLASH_PRICING_USD["input_cache_hit_offpeak"]
        input_miss = FLASH_PRICING_USD["input_cache_miss_offpeak"]
        output = FLASH_PRICING_USD["output_offpeak"]
        band = "off-peak"
    usd = (cached / 1_000_000) * input_hit + (miss / 1_000_000) * input_miss + (
        usage.completion_tokens / 1_000_000
    ) * output
    return {
        "usd": round(usd, 6),
        "peak": peak,
        "band": band,
        "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing",
        "priced_at": when.astimezone(timezone.utc).isoformat(),
        "cached_tokens": cached,
        "cache_miss_tokens": miss,
    }
