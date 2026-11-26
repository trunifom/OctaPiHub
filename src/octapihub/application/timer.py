"""The ``timer`` plugin: a pause between the steps of one action sequence.

A face can say "wait 2 seconds, then start the music". The rule engine puts a ``timer wait`` step in
front of such an action; the per-cube queue then runs the steps one after another. Waits are limited
to 30 seconds so that a step cannot outlive the short validity of music and light actions.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping

from octapihub.core.config import MAX_DELAY_SECONDS
from octapihub.core.errors import ValidationError


class TimerPlugin:
    """Waits for ``seconds`` (0 to 30)."""

    kind = "timer"

    def __init__(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._sleep = sleep

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        if action != "wait":
            raise ValidationError(
                "Unsupported timer action", user_message="Diese Wartezeit-Aktion gibt es nicht."
            )
        seconds = parameters.get("seconds")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 <= seconds <= MAX_DELAY_SECONDS
        ):
            raise ValidationError(
                "Invalid wait time",
                user_message=(
                    f"Die Wartezeit muss zwischen 0 und {MAX_DELAY_SECONDS:g} Sekunden liegen."
                ),
            )
        await self._sleep(float(seconds))
