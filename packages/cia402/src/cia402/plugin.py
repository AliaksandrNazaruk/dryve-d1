"""Device plugin system for the CiA 402 driver framework.

Device-specific behavior is injected via the ``DeviceHooks`` protocol.
Each hook method has a sensible default in ``NullDeviceHooks`` that
corresponds to standard CiA 402 behavior.

Usage::

    from cia402.plugin import DeviceHooks, NullDeviceHooks

    class MyDriveHooks(NullDeviceHooks):
        def system_cycle_delay_s(self) -> float:
            return 0.01  # my drive needs 10ms settle time
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cia402.od.accessor import AsyncODAccessor


@runtime_checkable
class DeviceHooks(Protocol):
    """Device-specific behavior injected into the generic CiA 402 framework."""

    def validate_statusword(self, sw: int) -> None:
        """Called after reading statusword. Raise to reject invalid states."""
        ...

    def pre_state_transition(self, sw: int) -> None:
        """Called before state machine transitions. E.g., check REMOTE/enable."""
        ...

    def system_cycle_delay_s(self) -> float:
        """Delay (seconds) after OD writes before pulsing controlword bits."""
        ...

    def require_new_setpoint_latch_in_pv(self) -> bool:
        """If True, Profile Velocity mode pulses NEW_SET_POINT after target write."""
        ...

    def homing_method_writable(self) -> bool:
        """If False, skip writing homing method register (0x6098)."""
        ...

    def verify_mode_display(self) -> bool:
        """If True, poll 0x6061 to verify mode. If False, use settle delay."""
        ...

    async def is_homed(self, od: AsyncODAccessor) -> bool | None:
        """Custom homing check. Return None to use standard CiA 402 method."""
        ...

    def tid_max_value(self) -> int:
        """Max Modbus transaction ID value. 0xFFFF for standard, 0xFF for some devices."""
        ...

    def keepalive_suppress_on_disable(self) -> bool:
        """If True, suppress keepalive I/O during disable_voltage transitions."""
        ...


class NullDeviceHooks:
    """Default hooks implementing standard CiA 402 behavior (no device quirks)."""

    def validate_statusword(self, sw: int) -> None:
        pass

    def pre_state_transition(self, sw: int) -> None:
        pass

    def system_cycle_delay_s(self) -> float:
        return 0.0

    def require_new_setpoint_latch_in_pv(self) -> bool:
        return False

    def homing_method_writable(self) -> bool:
        return True

    def verify_mode_display(self) -> bool:
        return True

    async def is_homed(self, od: AsyncODAccessor) -> bool | None:
        return None

    def tid_max_value(self) -> int:
        return 0xFFFF

    def keepalive_suppress_on_disable(self) -> bool:
        return False
