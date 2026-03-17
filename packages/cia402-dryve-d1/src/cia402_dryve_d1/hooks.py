"""igus dryve D1 device hooks for the CiA 402 framework.

Implements all dryve D1-specific behavior:
- Boot state 0x2704 detection
- DI7 REMOTE (bit 9) precondition
- System cycle delay for gateway
- Homing via proprietary register 0x2014
- Transaction ID wrapping at 0xFF
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .preconditions import require_remote_enabled

if TYPE_CHECKING:
    from cia402.od.accessor import AsyncODAccessor

# dryve D1 proprietary homing status register
_HOMING_STATUS_INDEX = 0x2014

# Invalid boot state requiring controller restart
_INVALID_BOOT_STATE = 0x2704


class InvalidBootStateError(RuntimeError):
    """Drive is in documented invalid state 0x2704 (restart required)."""


class DryveD1Hooks:
    """DeviceHooks implementation for igus dryve D1."""

    def validate_statusword(self, sw: int) -> None:
        if (sw & 0xFFFF) == _INVALID_BOOT_STATE:
            raise InvalidBootStateError(
                "Drive is in documented invalid state 0x2704 "
                "(likely from previous non-CiA402 mode). Restart the motor controller."
            )

    def pre_state_transition(self, sw: int) -> None:
        require_remote_enabled(sw)

    def system_cycle_delay_s(self) -> float:
        return 0.01  # 10ms per dryve D1 manual

    def require_new_setpoint_latch_in_pv(self) -> bool:
        return True  # dryve D1 needs bit4 pulse in Profile Velocity mode

    def homing_method_writable(self) -> bool:
        return False  # 0x6098 is read-only on dryve D1 (configured via CPG web UI)

    def verify_mode_display(self) -> bool:
        return False  # gateway returns stale 0x6061 values

    async def is_homed(self, od: AsyncODAccessor) -> bool | None:
        """Check homing via dryve D1 proprietary register 0x2014."""
        try:
            val = await od.read_u16(_HOMING_STATUS_INDEX, 0)
            return bool(val & 0x01)
        except Exception:
            return False

    def tid_max_value(self) -> int:
        return 0xFF  # dryve D1 echoes only lower 8 bits of transaction ID

    def keepalive_suppress_on_disable(self) -> bool:
        return True  # confirmed on real hardware
