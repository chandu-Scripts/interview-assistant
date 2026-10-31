"""Serialize account library mutations with account revocation."""

from collections.abc import Callable
from contextlib import nullcontext
from functools import wraps
from typing import ParamSpec, TypeVar

from interview_assistant.account.context import current_context

P = ParamSpec("P")
R = TypeVar("R")


def account_mutation(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        context = current_context()
        try:
            with context.mutation() if context is not None else nullcontext():
                return function(*args, **kwargs)
        except PermissionError as exc:
            # Library UI callers already display ValueError validation failures safely.
            raise ValueError("The account workspace no longer permits this change.") from exc

    return guarded
