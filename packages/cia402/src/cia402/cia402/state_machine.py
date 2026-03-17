"""CiA 402 state machine transitions.

Implements the standard CiA 402 state machine with device-specific
behavior injected via DeviceHooks.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

_LOGGER = logging.getLogger(__name__)

from ..od.controlword import (
    CWBit,
    cw_disable_voltage,
    cw_enable_operation,
    cw_fault_reset,
    cw_quick_stop as _cw_quick_stop,
    cw_set_bits,
    cw_shutdown,
    cw_switch_on,
)
from ..od.indices import ODIndex
from ..od.statusword import CiA402State, SWBit, infer_cia402_state
from ..od.accessor import AsyncODAccessor
from ..transport.clock import monotonic_s
from .bits import bit_is_set

if TYPE_CHECKING:
    from ..plugin import DeviceHooks

_U16_MASK = 0xFFFF


class StateMachineError(RuntimeError):
    """Base error for state machine failures."""


class StateMachineTimeout(StateMachineError):
    """Raised when the drive does not reach the expected state within the timeout."""


class InvalidBootStateError(StateMachineError):
    """Raised when the drive is detected in an invalid state (restart required)."""


@dataclass(frozen=True, slots=True)
class StateMachineConfig:
    poll_interval_s: float = 0.05
    step_timeout_s: float = 5.0
    fault_reset_timeout_s: float = 5.0
    require_remote: bool = True


def _fmt_u16(x: int) -> str:
    return f"0x{int(x) & _U16_MASK:04X}"


def _ensure_hold_bits(controlword: int) -> int:
    """Ensure bits 0..3 are present (hold) to maintain Operation Enabled."""
    return cw_set_bits(int(controlword), CWBit.SWITCH_ON, CWBit.ENABLE_VOLTAGE, CWBit.QUICK_STOP, CWBit.ENABLE_OPERATION)


class CiA402StateMachine:
    """Async CiA 402 state machine runner."""

    def __init__(
        self,
        od: AsyncODAccessor,
        *,
        config: StateMachineConfig | None = None,
        hooks: DeviceHooks | None = None,
    ) -> None:
        self._od = od
        self._cfg = config or StateMachineConfig()
        if hooks is None:
            from ..plugin import NullDeviceHooks
            hooks = NullDeviceHooks()
        self._hooks = hooks

    async def read_statusword(self) -> int:
        sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
        sw_u16 = int(sw) & _U16_MASK
        self._hooks.validate_statusword(sw_u16)
        if self._cfg.require_remote:
            self._hooks.pre_state_transition(sw_u16)
        return sw_u16

    async def write_controlword(self, value: int) -> None:
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(value) & _U16_MASK, 0)

    async def current_state(self) -> CiA402State:
        sw = await self.read_statusword()
        return infer_cia402_state(sw)

    async def _wait_for_states(self, desired: set[CiA402State], *, timeout_s: float | None = None) -> CiA402State:
        timeout = self._cfg.step_timeout_s if timeout_s is None else timeout_s
        deadline = monotonic_s() + float(timeout)
        last_state = CiA402State.UNKNOWN

        while True:
            loop_time = monotonic_s()
            sw = await self.read_statusword()
            last_state = infer_cia402_state(sw)
            if last_state in desired:
                return last_state
            if loop_time >= deadline:
                raise StateMachineTimeout(
                    f"Timeout waiting for states {sorted(s.value for s in desired)}; last_state={last_state.value}, statusword={_fmt_u16(sw)}"
                )
            await asyncio.sleep(self._cfg.poll_interval_s)

    # -----------------------
    # Basic transitions
    # -----------------------
    async def disable_voltage(self) -> None:
        await self.write_controlword(cw_disable_voltage())

    async def quick_stop(self) -> None:
        """Request quick stop (CiA 402: clear bit 2 while maintaining hold bits)."""
        await self.write_controlword(_cw_quick_stop())
        try:
            await self._wait_for_states({CiA402State.QUICK_STOP_ACTIVE}, timeout_s=2.0)
        except StateMachineTimeout:
            _LOGGER.warning("Quick stop: did not reach QUICK_STOP_ACTIVE within 2s")

    async def shutdown(self) -> None:
        await self.write_controlword(cw_shutdown())
        await self._wait_for_states({CiA402State.READY_TO_SWITCH_ON, CiA402State.SWITCH_ON_DISABLED})

    async def switch_on(self) -> None:
        await self.write_controlword(cw_switch_on())
        await self._wait_for_states({CiA402State.SWITCHED_ON, CiA402State.OPERATION_ENABLED})

    async def enable_operation(self) -> None:
        await self.write_controlword(_ensure_hold_bits(cw_enable_operation()))
        await self._wait_for_states({CiA402State.OPERATION_ENABLED})

    async def fault_reset(self) -> None:
        """Reset fault per CiA 402 standard.

        If a fault is active, pulses the fault reset bit and waits for
        the drive to leave the fault state.
        """
        sw = await self.read_statusword()
        if not bit_is_set(sw, SWBit.FAULT):
            return

        # Device hooks may enforce additional preconditions (e.g., REMOTE bit)
        self._hooks.pre_state_transition(sw)

        await self.write_controlword(cw_fault_reset())
        await asyncio.sleep(max(0.1, self._cfg.poll_interval_s))
        await self.write_controlword(cw_shutdown())
        safe_halt = cw_set_bits(cw_shutdown(), CWBit.HALT)
        await self.write_controlword(safe_halt)
        await self._wait_for_states({
            CiA402State.SWITCH_ON_DISABLED,
            CiA402State.READY_TO_SWITCH_ON,
            CiA402State.SWITCHED_ON,
            CiA402State.OPERATION_ENABLED,
        }, timeout_s=self._cfg.fault_reset_timeout_s)

    # -----------------------
    # High-level helpers
    # -----------------------
    async def run_to_operation_enabled(self) -> None:
        """Run the state machine to reach 'Operation enabled'."""
        st = await self.current_state()

        if st == CiA402State.FAULT or st == CiA402State.FAULT_REACTION_ACTIVE:
            await self.fault_reset()
            st = await self.current_state()

        if st == CiA402State.SWITCH_ON_DISABLED:
            await self.shutdown()
            st = await self.current_state()

        if st == CiA402State.NOT_READY_TO_SWITCH_ON:
            await self._wait_for_states({CiA402State.SWITCH_ON_DISABLED, CiA402State.READY_TO_SWITCH_ON})
            st = await self.current_state()

        if st == CiA402State.READY_TO_SWITCH_ON:
            await self.switch_on()
            st = await self.current_state()

        if st == CiA402State.SWITCHED_ON:
            await self.enable_operation()
            return

        if st == CiA402State.OPERATION_ENABLED:
            await self.write_controlword(_ensure_hold_bits(cw_enable_operation()))
            return

        if st == CiA402State.QUICK_STOP_ACTIVE:
            await self.shutdown()
            await self.switch_on()
            await self.enable_operation()
            return

        raise StateMachineError(f"Unhandled CiA402 state: {st.value}")
