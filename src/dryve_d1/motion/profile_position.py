from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from dryve_d1.utils.debug import Debug

from ..cia402.bits import bit_is_set as _bit
from ..od.controlword import (
    CWBit,
    cw_clear_bits,
    cw_enable_operation,
    cw_pulse_new_set_point,
    cw_set_bits,
)
from ..od.indices import ODIndex, ObjectDictionary
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
    position_reached_window: int = 100  # |actual-target| tolerance that confirms bit10 "target reached" (rejects stale bit10)

    verify_mode: bool = False
    mode_set_timeout_s: float = 1.0
    mode_settle_s: float = 0.3  # Delay after writing mode register when verify_mode=False

    def __post_init__(self) -> None:
        if self.system_cycle_delay_s < 0.001:
            raise ValueError(f"system_cycle_delay_s must be >= 0.001, got {self.system_cycle_delay_s}")

MODE_PROFILE_POSITION = 1


class ProfilePosition:
    """Profile Position mode helper (6060=1)."""

    def __init__(self, accessor: AsyncODAccessor, *, config: ProfilePositionConfig | None = None,
                 abort_event: asyncio.Event | None = None) -> None:
        self._accessor = accessor
        self._od = ObjectDictionary(accessor)
        self._debug = Debug(accessor, "PP")
        self._cfg = config or ProfilePositionConfig()
        self._abort: asyncio.Event | None = abort_event
        # Drive's own Position Window (0x6067), discovered at connect. Used to
        # size the completion tolerance so we never demand a TIGHTER window than
        # the drive itself (which would false-timeout a move the drive already
        # considers reached). None until set; falls back to config default.
        self._drive_position_window: int | None = None

    def set_drive_position_window(self, window: int | None) -> None:
        """Record the drive's Position Window (0x6067), read once at connect."""
        try:
            w = int(window) if window is not None else 0
        except (TypeError, ValueError):
            w = 0
        self._drive_position_window = w if w > 0 else None

    def _reached_window(self) -> int:
        """Completion tolerance: max(drive's 0x6067, config default).

        Never tighter than the drive's own Position Window — bit10 is the gate,
        so a looser tolerance is safe, but a tighter one false-timeouts.
        """
        cfg_window = int(self._cfg.position_reached_window)
        if self._drive_position_window:
            return max(cfg_window, self._drive_position_window)
        return cfg_window

    async def ensure_mode(self) -> None:
        # Manual §5.6.10: the operation mode is only guaranteed active once
        # object 0x6061 "Modes of Operation Display" mirrors 0x6060. We always
        # write the mode register first (never skip the write), then confirm
        # the display — the dryve D1 gateway can briefly return a stale 0x6061,
        # so we poll with a settle delay rather than reading once.
        await self._od.MODES_OF_OPERATION.write(MODE_PROFILE_POSITION)
        await asyncio.sleep(max(0.01, float(self._cfg.mode_settle_s)))
        deadline = monotonic_s() + float(self._cfg.mode_set_timeout_s)
        while True:
            try:
                mode_disp = await self._od.MODES_OF_OPERATION_DISPLAY.read()
            except Exception as e:
                # The 0x6061 read itself failed. With verify_mode=False this must
                # not abort the move — fall back to the delay-only path (the
                # set-point handshake gates the move). verify_mode=True is strict.
                if self._cfg.verify_mode:
                    raise
                _LOGGER.warning(
                    "PP: mode display 0x6061 read failed (%s); proceeding after "
                    "settle — set-point handshake will gate the move", e,
                )
                return
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
            await self._od.PROFILE_VELOCITY.write(int(pv))
        if acc is not None:
            await self._od.PROFILE_ACCELERATION.write(int(acc))
        if dec is not None:
            await self._od.PROFILE_DECELERATION.write(int(dec))

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
        await self._od.TARGET_POSITION.write(int(target_position))

        # Barrier cycle: per manual, wait one system cycle after configuration before start
        # Per manual requirement: after parameterizing mode objects, wait one system cycle
        # before sending Start Command via Controlword bit 4.
        # We ensure this by: (1) reading statusword as a round-trip barrier to ensure
        # the drive has processed parameter changes, (2) adding explicit system cycle delay.
        await self._od.STATUSWORD.read()
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
        # 2. The drive confirms it captured the set-point by setting bit12
        #    "Set-point Acknowledge" (and eventually clearing bit10). We wait
        #    for that acknowledge before releasing bit4 — a wrong-mode/rejected
        #    Start yields no acknowledge and a loud TimeoutError.
        # 3. Release bit4; the drive clears bit12 and runs the move.
        # NOTE: bit10 "Target Reached" is STALE right after the set-point — real
        # hardware sets bit12 while bit10 is still 1 from the previous motion,
        # even for a genuine move. So completion is NOT inferred from the bits
        # alone (that caused a false "no motion required"); wait_target_reached
        # confirms bit10 against the actual position.
        await self._od.CONTROLWORD.write(int(set_word) & 0xFFFF)
        try:
            await self._wait_setpoint_ack(timeout_s=self._cfg.setpoint_ack_timeout_s)
        finally:
            # Always release bit4, even if the acknowledge timed out, so the
            # drive is not left latched waiting on a stale Start signal.
            await self._od.CONTROLWORD.write(int(clear_word) & 0xFFFF)

        await self.wait_target_reached(target_position=target_position, timeout_s=timeout_s)

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
        await self._od.CONTROLWORD.write(int(word) & 0xFFFF)

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

    async def _wait_setpoint_ack(self, *, timeout_s: float) -> None:
        """Wait for the drive to acknowledge the new set-point (manual §5.6.10.2).

        Called while Controlword bit4 "New Set-point" is held HIGH. Per the
        manual, after Start the drive sets Statusword bit12 "Set-point
        Acknowledge" and (eventually) resets bit10 "Target Reached". Either
        confirms the set-point was *accepted* — but NOT that it is complete:
        real hardware sets bit12 while bit10 is still 1 (stale from the previous
        motion) for a genuine move. So this only gates the bit4 release; actual
        completion is decided later by wait_target_reached() against position.

        A set-point that is never acknowledged (e.g. wrong mode / rejected
        Start) raises TimeoutError rather than silently proceeding.

        Raises:
            TimeoutError: set-point never acknowledged.
            RuntimeError: drive entered FAULT while waiting.
        """
        deadline = monotonic_s() + timeout_s
        while True:
            sw = await self._od.STATUSWORD.read()
            target_reached = _bit(sw, int(SWBit.TARGET_REACHED))
            setpoint_ack = _bit(sw, int(SWBit.OP_MODE_SPECIFIC))

            if setpoint_ack or not target_reached:
                _LOGGER.debug(
                    "PP: set-point acknowledged (bit12=%s, bit10=%s)",
                    setpoint_ack, target_reached,
                )
                return

            if _bit(sw, int(SWBit.FAULT)):
                decoded = decode_statusword(sw)
                raise RuntimeError(
                    f"Fault while waiting for set-point acknowledge. "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, flags={decoded}"
                )
            if monotonic_s() >= deadline:
                target_pos = await self._od.TARGET_POSITION.read()
                actual_pos = await self._od.POSITION_ACTUAL_VALUE.read()
                decoded = decode_statusword(sw)
                raise TimeoutError(
                    f"PP: set-point not acknowledged within {timeout_s:.2f}s "
                    f"(Start not accepted — drive may not be in PP mode). "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, flags={decoded}, "
                    f"target_position={target_pos}, actual_position={actual_pos}, "
                    f"delta={abs(actual_pos - target_pos)}"
                )
            await asyncio.sleep(self._cfg.poll_interval_s)

    async def wait_target_reached(self, *, target_position: int | None = None,
                                  timeout_s: float | None = None) -> None:
        """Wait until the move physically completes.

        Completion = Statusword bit10 "Target Reached" set AND the actual
        position within ``position_reached_window`` of ``target_position``.

        The position cross-check is essential: right after a new set-point the
        dryve D1 leaves bit10 STALE at 1 (from the previous motion) for several
        cycles, even for a genuine move. Trusting bit10 alone there reports a
        phantom "reached" without the axis moving. Anchoring bit10 to the actual
        position against the *new* target rejects that stale window — and, when
        ``target_position`` is omitted (or no window is configured), this falls
        back to the bare bit10 check.
        """
        timeout = self._cfg.move_timeout_s if timeout_s is None else float(timeout_s)
        deadline = monotonic_s() + timeout
        window = self._reached_window()

        async def _read_mode_display_safe() -> int | None:
            try:
                return await self._od.MODES_OF_OPERATION_DISPLAY.read()
            except Exception:
                return None

        while True:
            # ── Abort check (highest priority) ──────────────────────
            if self._abort is not None and self._abort.is_set():
                raise MotionAborted("Motion aborted by stop command")

            loop_time = monotonic_s()
            sw = await self._od.STATUSWORD.read()
            if _bit(sw, int(SWBit.TARGET_REACHED)):
                if target_position is None or window <= 0:
                    _LOGGER.info("PP: target reached")
                    return
                actual_pos = await self._od.POSITION_ACTUAL_VALUE.read()
                if abs(actual_pos - int(target_position)) <= window:
                    _LOGGER.info("PP: target reached (pos=%d, target=%d)", actual_pos, int(target_position))
                    return
                # bit10 set but position far from target → stale bit10; keep waiting.

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
                target_pos = await self._od.TARGET_POSITION.read()
                actual_pos = await self._od.POSITION_ACTUAL_VALUE.read()
                mode_display = await _read_mode_display_safe()
                position_error = abs(actual_pos - target_pos)
                # If the drive reported Target Reached the whole time yet the
                # axis never approached the target, the most likely cause is a
                # drive-side config issue, not the command — surface a hint.
                hint = ""
                if decoded.get("target_reached", False) and position_error > window:
                    hint = (
                        " — drive reports Target Reached but the axis did not move; "
                        "check the drive's Position Window (0x6067) and that the "
                        "motor is powered/enabled."
                    )
                raise TimeoutError(
                    f"Timeout waiting for target reached after {timeout:.1f}s. "
                    f"statusword=0x{int(sw) & 0xFFFF:04X}, "
                    f"mode_display={mode_display}, "
                    f"target_reached={decoded.get('target_reached', False)}, "
                    f"op_mode_specific={decoded.get('op_mode_specific', False)}, "
                    f"target_position={target_pos}, actual_position={actual_pos}, "
                    f"position_error={position_error}, reached_window={window}{hint}"
                )
            await asyncio.sleep(self._cfg.poll_interval_s)
