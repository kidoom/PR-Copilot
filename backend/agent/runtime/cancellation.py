"""Cooperative cancellation probe for agent runtime.

Provides a lightweight, shared cancellation signal that can be checked at
bounded points throughout the agent execution pipeline.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable


class Cancelled(Exception):
    """Raised when a cooperative cancellation is observed."""
    pass


class CancellationProbe:
    """A lightweight cooperative cancellation signal.

    Callers check ``is_cancelled()`` at bounded checkpoints. The probe is
    typically backed by an ``asyncio.Event`` so that cancellation from the
    API layer can be observed promptly.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._event = asyncio.Event()

    def cancel(self) -> None:
        """Mark the probe as cancelled."""
        self._cancelled = True
        self._event.set()

    def is_cancelled(self) -> bool:
        """Check whether cancellation has been requested."""
        return self._cancelled

    def check(self) -> None:
        """Raise ``Cancelled`` if cancellation has been requested."""
        if self._cancelled:
            raise Cancelled("Operation was cancelled")

    async def wait(self) -> None:
        """Wait until cancellation is requested."""
        await self._event.wait()


def check_cancellation(probe: CancellationProbe | None) -> None:
    """Convenience function to check a probe, handling None."""
    if probe is not None:
        probe.check()


def is_cancelled(probe: CancellationProbe | None) -> bool:
    """Convenience function to check if a probe is cancelled."""
    if probe is not None:
        return probe.is_cancelled()
    return False
