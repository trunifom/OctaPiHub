"""The rules of a play queue: order, shuffle, repeat (pure; no audio, no files).

Repeat ``off`` plays the queue once, ``all`` starts over after the last track, ``one`` repeats the
current track until somebody stops it. The user's own *next* and *previous* always move on, also
in ``one`` mode, because a button that does nothing looks broken.
"""

from __future__ import annotations

import random

from octapihub.core.config import RepeatMode


def play_order(count: int, *, shuffle: bool, seed: int = 0, first: int | None = None) -> list[int]:
    """Order in which tracks ``0..count-1`` play (reproducible for the same ``seed``).

    With a shuffle, ``first`` (the track the user picked) is moved to the front.
    """
    order = list(range(max(count, 0)))
    if shuffle and len(order) > 1:
        random.Random(seed).shuffle(order)  # noqa: S311 - a play order, not a secret
        if first is not None and first in order:
            order.remove(first)
            order.insert(0, first)
    return order


def following(
    position: int, count: int, repeat: RepeatMode, *, by_user: bool = False
) -> int | None:
    """Position after ``position`` in the order, or ``None`` when playback is over."""
    if count <= 0:
        return None
    if repeat is RepeatMode.ONE and not by_user:
        return position
    if position + 1 < count:
        return position + 1
    return 0 if repeat is not RepeatMode.OFF else None


def preceding(position: int, count: int, repeat: RepeatMode) -> int:
    """Position the *previous* button goes to (the first track starts over)."""
    if count <= 0:
        return 0
    if position > 0:
        return position - 1
    return count - 1 if repeat is RepeatMode.ALL else 0
