"""Status and telemetry queries for DryveD1."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from ..od.statusword import decode_statusword, infer_cia402_state
from ..transport.clock import monotonic_s

if TYPE_CHECKING:
    from ..motion.jog import JogController
    from ..telemetry.poller import TelemetryPoller
    from ..telemetry.snapshots import DriveSnapshot
    from ..od.dictionary import ObjectDictionary  # Notre OD statique
    from .drive import DryveD1Config

_LOGGER = logging.getLogger(__name__)


class StatusQueries:
    """Read-only status and telemetry queries via object composition."""

    def __init__(self, od: ObjectDictionary, config: DryveD1Config, poller: TelemetryPoller | None = None):
        self._od = od
        self._cfg = config
        self._telemetry_poller = poller
        self._jog: JogController | None = None

    def set_jog_controller(self, jog: JogController | None) -> None:
        """Injection du contrôleur de jog après sa création."""
        self._jog = jog

    def set_telemetry_poller(self, poller: TelemetryPoller | None) -> None:
        self._telemetry_poller = poller

    # ---- position ----

    async def get_position(self) -> int:
        """Get current position (cached or live)."""
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None and snapshot.position is not None:
                return snapshot.position
        return await self._od.POSITION_ACTUAL_VALUE.read()

    async def get_position_live(self) -> int:
        """Read current position directly (bypass telemetry cache)."""
        return await self._od.POSITION_ACTUAL_VALUE.read()

    # ---- is_moving (mode-aware) ----

    _MODE_PROFILE_POSITION = 1
    _MODE_PROFILE_VELOCITY = 3

    async def is_moving(self) -> bool:
        """Check if the drive is currently in motion."""
        snapshot = None
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
        if snapshot is not None and snapshot.decoded_status is not None:
            decoded = snapshot.decoded_status
            mode_disp = snapshot.mode_display
        else:
            sw = await self._od.STATUSWORD.read()
            decoded = decode_statusword(sw)
            mode_disp = None

        if self._jog is not None:
            jog_state = self._jog.state
            if jog_state.active and monotonic_s() < jog_state.deadline_s:
                return True

        if mode_disp is None:
            try:
                mode_disp = await self._od.MODES_OF_OPERATION_DISPLAY.read()
            except Exception:
                return not decoded["target_reached"]

        if mode_disp == self._MODE_PROFILE_POSITION:
            return await self._is_motion_pp(decoded, snapshot)
        if mode_disp == self._MODE_PROFILE_VELOCITY:
            return await self._is_motion_pv(snapshot)

        if mode_disp not in (0, 6):
            _LOGGER.warning("is_moving: unexpected mode_display=%s, using target_reached fallback", mode_disp)
        return not decoded["target_reached"]

    async def _is_motion_pp(self, decoded: dict[str, bool], snapshot: DriveSnapshot | None) -> bool:
        if decoded["target_reached"]:
            return False
        vel = await self._read_velocity_or_none(snapshot)
        if vel is None:
            return not decoded["target_reached"]
        return abs(vel) > self._cfg.velocity_threshold

    async def _is_motion_pv(self, snapshot: DriveSnapshot | None) -> bool:
        vel = await self._read_velocity_or_none(snapshot)
        if vel is None:
            return True
        return abs(vel) > self._cfg.velocity_threshold

    async def _read_velocity_or_none(self, snapshot: DriveSnapshot | None) -> int | None:
        if snapshot is not None and snapshot.velocity is not None:
            return snapshot.velocity
        try:
            return await self._od.VELOCITY_ACTUAL_VALUE.read()
        except Exception:
            return None

    # ---- status / statusword ----

    async def get_status(self) -> dict[str, bool]:
        if self._telemetry_poller is not None:
            snapshot = self._telemetry_poller.latest
            if snapshot is not None and snapshot.decoded_status is not None:
                return snapshot.decoded_status
        sw = await self._od.STATUSWORD.read()
        return decode_statusword(sw)

    async def get_status_live(self) -> dict[str, bool]:
        sw = await self._od.STATUSWORD.read()
        return decode_statusword(sw)

    async def get_statusword(self) -> int:
        return await self._od.STATUSWORD.read()

    async def get_cia402_state(self):
        sw = await self._od.STATUSWORD.read()
        return infer_cia402_state(sw)

    async def get_velocity_actual(self) -> int:
        return await self._od.VELOCITY_ACTUAL_VALUE.read()

    async def get_mode_display(self) -> int:
        return await self._od.MODES_OF_OPERATION_DISPLAY.read()

    async def is_homed(self) -> bool:
        try:
            homing_status = await self._od.HOMING_STATUS.read()
            return bool(homing_status & 0x01)
        except (TimeoutError, ConnectionError, OSError):
            raise
        except Exception:
            _LOGGER.debug("is_homed: read failed, assuming not homed", exc_info=True)
            return False

    # ---- position limits ----

    async def set_position_limits(self, min_position: int, max_position: int) -> None:
        if min_position >= max_position:
            raise ValueError(f"min_position ({min_position}) must be less than max_position ({max_position})")
        if min_position != 0:
            _LOGGER.warning("set_position_limits: dryve D1 requires sub1 (min) == 0; got %d", min_position)

        await self._od.LIMIT_MIN.write(int(min_position))
        await self._od.LIMIT_MAX.write(int(max_position))

    async def get_position_limits(self) -> tuple[int, int]:
        min_pos = await self._od.LIMIT_MIN.read()
        max_pos = await self._od.LIMIT_MAX.read()
        return (min_pos, max_pos)

    def _resolve_position_limits(self) -> tuple[int, int]:
        limits = self._cfg.drive.limits
        min_pos = limits.min_position_limit if limits.min_position_limit is not None else 0
        max_pos = limits.max_position_limit if limits.max_position_limit is not None else 120000
        return min_pos, max_pos