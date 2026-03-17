"""igus dryve D1 precondition checks.

DI7 'Enable' must be HIGH (Statusword bit 9 'Remote' == 1) for the
state machine to run on the dryve D1.
"""

from __future__ import annotations

from cia402.od.statusword import SWBit
from cia402.cia402.bits import _U16_MASK, bit_is_set as _bit
from cia402.cia402.preconditions import PreconditionFailed


def require_remote_enabled(statusword: int) -> None:
    """Require Statusword bit 9 ('Remote') == 1.

    On dryve D1, this indicates DI7 'Enable' is logically HIGH.
    """
    if not _bit(statusword, SWBit.REMOTE):
        raise PreconditionFailed(
            "Remote not enabled: Statusword bit 9 is LOW (DI7 'Enable' must be HIGH). "
            f"statusword=0x{int(statusword) & _U16_MASK:04X}"
        )
