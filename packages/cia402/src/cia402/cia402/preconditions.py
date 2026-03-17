"""CiA 402 precondition checks.

Generic preconditions that apply to any CiA 402 drive.
Device-specific preconditions (e.g., vendor enable input checks)
belong in the device plugin's hooks.
"""

from __future__ import annotations

from ..od.statusword import SWBit
from .bits import _U16_MASK
from .bits import bit_is_set as _bit


class PreconditionFailed(RuntimeError):
    """Raised when a required drive precondition is not met."""


def require_not_in_fault(statusword: int) -> None:
    """Require Statusword bit 3 ('Fault') == 0."""
    if _bit(statusword, SWBit.FAULT):
        raise PreconditionFailed(
            f"Drive in fault (Statusword bit 3 HIGH). statusword=0x{int(statusword) & _U16_MASK:04X}"
        )


def require_operation_enabled(statusword: int) -> None:
    """Require 'Operation enabled' bit == 1."""
    if not _bit(statusword, SWBit.OPERATION_ENABLED):
        raise PreconditionFailed(
            "Drive not in 'Operation enabled'. "
            f"statusword=0x{int(statusword) & _U16_MASK:04X}"
        )
