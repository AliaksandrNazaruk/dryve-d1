from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..cia402.bits import bit_is_set as _bit
from ..od.controlword import (
    CWBit,
    cw_clear_bits,
    cw_enable_operation,
    cw_pulse_new_set_point,
    cw_set_bits,
)
from ..od.indices import ODIndex
from ..od.statusword import SWBit, decode_statusword
from ..protocol.accessor import AsyncODAccessor
from ..protocol.exceptions import MotionAborted
from ..transport.clock import monotonic_s

_LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class ProfilePositionConfig:
    """Configuration for Profile Position operations."""

    profile_velocity: int | None = None   # 0x6081 (UINT32, often)
    acceleration: int | None = None       # 0x6083 (UINT32)
    deceleration: int | None = None       # 0x6084 (UINT32)

    poll_interval_s: float = 0.05
    move_timeout_s: float = 30.0
    system_cycle_delay_s: float = 0.01  # Explicit system cycle delay (default 10ms, typical drive cycle: 1-5ms)
    setpoint_ack_timeout_s: float = 1.0  # Max wait for the drive to acknowledge a new set-point (bit12/bit10)

    verify_mode: bool = False
    mode_set_timeout_s: float = 1.0
    mode_settle_s: float = 0.3  # Delay after writing mode register when verify_mode=False

    def __post_init__(self) -> None:
        if self.system_cycle_delay_s < 0.001:
            raise ValueError(f"system_cycle_delay_s must be >= 0.001, got {self.system_cycle_delay_s}")

MODE_PROFILE_POSITION = 1


class ProfilePosition:
    """Profile Position mode helper (6060=1)."""

    def __init__(self, od: AsyncODAccessor, *, config: ProfilePositionConfig | None = None,
                 abort_event: asyncio.Event | None = None) -> None:
        self._od = od
        self._cfg = config or ProfilePositionConfig()
        self._abort: asyncio.Event | None = abort_event

    async def ensure_mode(self) -> None:
        # Manual §5.6.10: the operation mode is only guaranteed active once
        # object 0x6061 "Modes of Operation Display" mirrors 0x6060. We always
        # write the mode register first (never skip the write), then confirm
        # the display — the dryve D1 gateway can briefly return a stale 0x6061,
        # so we poll with a settle delay rather than reading once.
        await self._od.write_u8(int(ODIndex.MODES_OF_OPERATION), MODE_PROFILE_POSITION, 0)
        await asyncio.sleep(max(0.01, float(self._cfg.mode_settle_s)))
        deadline = monotonic_s() + float(self._cfg.mode_set_timeout_s)
        while True:
            mode_disp = await self._od.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            if mode_disp == MODE_PROFILE_POSITION:
                return
            if monotonic_s() >= deadline:
                # Known gateway quirk: 0x6061 can stay stale past the timeout.
                # Do NOT hard-fail by default — the set-point acknowledge
                # handshake in move_to() is the authoritative gate (a wrong mode
                # produces no acknowledge → TimeoutError there). With
                # verify_mode=True the caller opts into strict failure here.
                if self._cfg.verify_mode:
                    raise TimeoutError(
                        f"Timeout waiting for mode display == {MODE_PROFILE_POSITION} (got {mode_disp})"
                    )
                _LOGGER.warning(
                    "PP: mode display 0x6061 did not confirm == %d (got %s); "
                    "proceeding — set-point handshake will gate the move",
                    MODE_PROFILE_POSITION, mode_disp,
                )
                return
            await asyncio.sleep(self._cfg.poll_interval_s)

    async def configure(self, *, profile_velocity: int | None = None, acceleration: int | None = None, deceleration: int | None = None) -> None:
        pv = self._cfg.profile_velocity if profile_velocity is None else profile_velocity
        acc = self._cfg.acceleration if acceleration is None else acceleration
        dec = self._cfg.deceleration if deceleration is None else deceleration

        if pv is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_VELOCITY), int(pv), 0)
        if acc is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_ACCELERATION), int(acc), 0)
        if dec is not None:
            await self._od.write_u32(int(ODIndex.PROFILE_DECELERATION), int(dec), 0)

    async def move_to(self, target_position: int, *, relative: bool = False, immediate: bool = True, timeout_s: float | None = None) -> None:
        """Command a move to target_position and wait until 'target reached' bit is set.

        Assumptions:
        - Drive is in Operation Enabled
        - Position units match your scaling layer

        Args:
            target_position: desired position (INT32)
            relative: if True, interpret as relative move (CW bit 6)
            immediate: if True, set CW bit 5 (change set immediately) when pulsing new set-point
            timeout_s: override default move timeout
        
        Raises:
            ValueError: If relative=False and target_position < 0 (absolute position cannot be negative)
        """
        # Validate absolute position cannot be negative
        if not relative and target_position < 0:
            raise ValueError(
                f"Absolute position cannot be negative (relative=False, target_position={target_position}). "
                "Per manual requirement: if Absolute (bit6=0), position must be >= 0 after homing."
            )
        
        await self.ensure_mode()
        await self.configure()
        
        _LOGGER.info("PP: move_to target=%d relative=%s immediate=%s", target_position, relative, immediate)
        await self._od.write_i32(int(ODIndex.TARGET_POSITION), int(target_position), 0)
        
        # Barrier cycle: per manual, wait one system cycle after configuration before start
        # Per manual requirement: after parameterizing mode objects, wait one system cycle
        # before sending Start Command via Controlword bit 4.
        # We ensure this by: (1) reading statusword as a round-trip barrier to ensure
        # the drive has processed parameter changes, (2) adding explicit system cycle delay.
        await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
        # Explicit system cycle delay (typical drive cycle: 1-5ms, use configurable delay)
        await asyncio.sleep(self._cfg.system_cycle_delay_s)
        # Per manual: after Operation Enabled, bits 0..3 must always be sent
        # Start with base containing hold bits (0x000F)
        base = cw_enable_operation()  # 0x000F = bits 0,1,2,3 set
        
        if immediate:
            base = cw_set_bits(base, CWBit.CHANGE_SET_IMMEDIATELY)
        else:
            base = cw_clear_bits(base, CWBit.CHANGE_SET_IMMEDIATELY)

        if relative:
            base = cw_set_bits(base, CWBit.ABS_REL)
        else:
            base = cw_clear_bits(base, CWBit.ABS_REL)

        # Pulse new_setpoint while preserving hold bits (0..3)
        set_word, clear_word = cw_pulse_new_set_point(base)

        # ── Closed-loop Start handshake (manual §5.6.10.2) ──────────────────
        # 1. Set bit4 "New Set-point" and HOLD it high.
        # 2. The drive resets bit10 "Target Reached" and sets bit12
        #    "Set-point Acknowledge" to confirm it captured the set-point.
        # 3. ONLY after that acknowledge do we reset bit4.
        # 4. The drive then clears bit12 automatically; bit10 rises again once
        #    the movement completes.
        # Holding bit4 until the acknowledge latches bit12, which removes the
        # race the old open-loop pulse + position-delta heuristic tried (and
        # failed) to paper over: a wrong-mode/rejected Start now yields no
        # acknowledge and a loud TimeoutError instead of a false "reached".
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(set_word) & 0xFFFF, 0)
        try:
            motion_started = await self._wait_setpoint_ack(
                timeout_s=self._cfg.setpoint_ack_timeout_s
            )
        finally:
            # Always release bit4, even if the acknowledge timed out, so the
            # drive is not left latched waiting on a stale Start signal.
            await self._od.write_u16(int(ODIndex.CONTROLWORD), int(clear_word) & 0xFFFF, 0)

        # motion_started is True when bit10 was observed to clear (the drive is
        # actually moving); False when the set-point was acknowledged with bit10
        # still set (target equals current position — nothing to wait for).
        await self.wait_target_reached(timeout_s=timeout_s, motion_started=motion_started)

    async def move_to_position(
        self,
        *,
        target_position: int,
        profile_velocity: int,
        profile_accel: int,
        profile_decel: int,
        timeout_s: float | None = None,
    ) -> None:
        """Move to target position with specified velocity, acceleration, and deceleration.

        Temporarily overrides the config-level defaults so that ``move_to()``
        (which calls ``configure()`` internally) writes the caller-supplied
        values in a single pass — avoiding a redundant double-write to the
        OD registers.
        """
        # Stash and override config defaults so move_to → configure() uses
        # the caller-supplied values directly.
        saved = (self._cfg.profile_velocity, self._cfg.acceleration, self._cfg.deceleration)
        # frozen dataclass — replace via object.__setattr__
        object.__setattr__(self._cfg, "profile_velocity", profile_velocity)
        object.__setattr__(self._cfg, "acceleration", profile_accel)
        object.__setattr__(self._cfg, "deceleration", profile_decel)
        try:
            await self.move_to(target_position=target_position, timeout_s=timeout_s)
        finally:
            object.__setattr__(self._cfg, "profile_velocity", saved[0])
            object.__setattr__(self._cfg, "acceleration", saved[1])
            object.__setattr__(self._cfg, "deceleration", saved[2])

    async def halt(self, *, enabled: bool = True) -> None:
        """Halt movement in Profile Position mode using Controlword HALT bit (bit 8).
        
        In Profile Position mode, the HALT bit (bit 8) is typically used to stop
        movement immediately, rather than quick_stop. This is the standard CiA402
        method for stopping motion in profile modes.
        
        Args:
            enabled: If True, set HALT bit to stop movement. If False, clear HALT bit.
        """
        _LOGGER.debug("PP: halt enabled=%s", enabled)
        # Per manual: after Operation Enabled, bits 0..3 must always be sent
        # Start with base containing hold bits (0x000F)
        base = cw_enable_operation()  # 0x000F = bits 0,1,2,3 set
        word = cw_set_bits(base, CWBit.HALT) if enabled else cw_clear_bits(base, CWBit.HALT)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(word) & 0xFFFF, 0)

    async def stop(self) -> None:
        """Stop movement in Profile Position mode using normal deceleration.
        
        According to the manual, "Stop" command stops movement with a pre-set rate
        of deceleration (Profile Deceleration, 0x6084). This is different from
        Quick Stop which uses Quick Stop Deceleration (0x6085).
        
        In Profile Position mode, the standard way to stop with normal deceleration
        is to use the HALT bit (bit 8). The drive will decelerate using the configured
        Profile Deceleration value.
        
        Note: This method uses HALT bit which is the standard CiA402 method for
        stopping motion in profile modes with normal deceleration.
        """
        await self.halt(enabled=True)

    async def _wait_setpoint_ack(self, *, timeout_s: float) -> bool:
        """Wait for the drive to acknowledge the new set-point (manual §5.6.10.2).

        Called while Controlword bit4 "New Set-point" is held HIGH. Per the
        manual, after Start the drive resets Statusword bit10 "Target Reached"
        and sets bit12 "Set-point Acknowledge" ("Setpoint applied"). Either
        transition confirms the set-point was accepted. Because bit4 is held
        high, the drive keeps bit12 set until we release bit4, so this poll
        cannot miss a fast move — no position-delta heuristic is needed.

        Neither signal is stale-able from a previous motion: in PP idle bit12==0
        ("wait for new setpoint") and bit10==1, so we wait for bit12 to rise OR
        bit10 to fall. A leftover bit10==1 from homing therefore cannot produce
        a false positive — exactly the failure the old delta<=250 heuristic
        caused.

        Returns:
            True  if bit10 was observed cleared (the drive is moving).
            False if the set-point was acknowledged via bit12 while bit10 was
                  still set (target == current position — no motion required).

        Raises:
            TimeoutError: set-point never acknowledged (Start not accepted —
                e.g. the drive is not actually in PP mode).
            RuntimeError: drive entered FAULT while waiting.
        """
        deadline = monotonic_s() + timeout_s
        while True:
            sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
            target_reached = _bit(sw, int(SWBit.TARGET_REACHED))
            setpoint_ack = _bit(sw, int(SWBit.OP_MODE_SPECIFIC))

            if not target_reached:
                _LOGGER.debug("PP: set-point acknowledged — bit10 cleared (moving)")
                return True
            if setpoint_ack:
                _LOGGER.debug("PP: set-point acknowledged — bit12 set, bit10 still set (no motion)")
                return False

            if _bit(sw, int(SWBit.FAULT)):
                decoded = decode_statusword(sw)
                raise RuntimeError(
                    f"Fault while waiting for set-point acknowledge. "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, flags={decoded}"
                )
            if monotonic_s() >= deadline:
                target_pos = await self._od.read_i32(int(ODIndex.TARGET_POSITION), 0)
                actual_pos = await self._od.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE), 0)
                decoded = decode_statusword(sw)
                raise TimeoutError(
                    f"PP: set-point not acknowledged within {timeout_s:.2f}s "
                    f"(Start not accepted — drive may not be in PP mode). "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, flags={decoded}, "
                    f"target_position={target_pos}, actual_position={actual_pos}, "
                    f"delta={abs(actual_pos - target_pos)}"
                )
            await asyncio.sleep(self._cfg.poll_interval_s)

    async def wait_target_reached(self, *, timeout_s: float | None = None,
                                  motion_started: bool = True) -> None:
        timeout = self._cfg.move_timeout_s if timeout_s is None else float(timeout_s)
        deadline = monotonic_s() + timeout

        async def _read_mode_display_safe() -> int | None:
            try:
                return await self._od.read_i8(int(ODIndex.MODES_OF_OPERATION_DISPLAY), 0)
            except Exception:
                return None

        # The set-point was already acknowledged by the closed-loop handshake in
        # move_to(). If the acknowledge came with bit10 still set (motion_started
        # is False), the drive reports the target equals the current position
        # (manual: bit10 and bit12 set together when no move is needed) — there
        # is nothing to wait for.
        if not motion_started:
            _LOGGER.info("PP: target reached (no motion required)")
            return

        while True:
            # ── Abort check (highest priority) ──────────────────────
            if self._abort is not None and self._abort.is_set():
                raise MotionAborted("Motion aborted by stop command")

            loop_time = monotonic_s()
            sw = await self._od.read_u16(int(ODIndex.STATUSWORD), 0)
            if _bit(sw, int(SWBit.TARGET_REACHED)):
                _LOGGER.info("PP: target reached")
                return
            
            # Check for fault condition
            if _bit(sw, int(SWBit.FAULT)):
                decoded = decode_statusword(sw)
                raise RuntimeError(
                    f"Fault detected while waiting for target reached. "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, flags={decoded}"
                )
            
            if loop_time >= deadline:
                # Provide more diagnostic information on timeout
                decoded = decode_statusword(sw)
                target_pos = await self._od.read_i32(int(ODIndex.TARGET_POSITION), 0)
                actual_pos = await self._od.read_i32(int(ODIndex.POSITION_ACTUAL_VALUE), 0)
                mode_display = await _read_mode_display_safe()
                position_error = abs(actual_pos - target_pos)
                raise TimeoutError(
                    f"Timeout waiting for target reached after {timeout:.1f}s. "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, "
                    f"mode_display={mode_display}, "
                    f"target_reached={decoded.get('target_reached', False)}, "
                    f"op_mode_specific={decoded.get('op_mode_specific', False)}, "
                    f"target_position={target_pos}, actual_position={actual_pos}, "
                    f"position_error={position_error}"
                )
            await asyncio.sleep(self._cfg.poll_interval_s)
