import os
from collections.abc import Callable
from pathlib import Path

from interview_assistant.config.settings import Settings
from interview_assistant.cost.pricing import PriceBook
from interview_assistant.cost.tracker import Budget, CostTracker, UsageStore


def data_dir(settings: Settings) -> Path:
    if settings.data_dir:
        return Path(settings.data_dir)
    return Path(os.environ.get("APPDATA") or Path.home()) / "InterviewAssistant"


def build_tracker(settings: Settings, on_change: Callable[[], None] | None = None) -> CostTracker:
    prices = PriceBook()
    if settings.pricing_file:
        prices.load_overrides(Path(settings.pricing_file))
    budget = (
        Budget(settings.budget_usd, settings.budget_scope, settings.budget_hard_stop)
        if settings.budget_usd is not None and settings.budget_usd > 0
        else None
    )
    store = UsageStore(data_dir(settings) / "usage.jsonl") if settings.usage_log else None
    return CostTracker(prices, store, budget, on_change)
