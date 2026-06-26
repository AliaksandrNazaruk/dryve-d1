"""Motion commands for DryveD1."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from ..cia402.dominance import PreconditionFailed
from ..od.controlword import CWBit, cw_clear_bits, cw_disable_voltage, cw_enable_operation, cw_set_bits

if TYPE_CHECKING:
    from ..cia402.state_machine import CiA402StateMachine
    from ..motion.homing import Homing, HomingResult
    from ..motion.jog import JogController
    from ..motion.profile_position import ProfilePosition
    from ..motion.profile_velocity import ProfileVelocity
    from ..od.dictionary import ObjectDictionary
    from .status_queries import StatusQueries
    from .drive import DryveD1Config

_LOGGER = logging.getLogger(__name__)


class MotionCommands:
    """Motion execution commands: move, jog, home, stop."""

    def __init__(self, od: ObjectDictionary, queries: StatusQueries, config: DryveD1Config):
        self._od = od
        self._queries = queries
        self._cfg = config

        # Helpers de mouvement injectés par la façade
        self._sm: CiA402StateMachine | None = None
        self._pp: ProfilePosition | None = None
        self._pv: ProfileVelocity | None = None
        self._homing: Homing | None = None
        self._jog: JogController | None = None

        self._abort_event = asyncio.Event()
        self._abort_token: str = uuid.uuid4().hex
        self._session: Any = None  # Injecté depuis DryveD1 pour la gestion keepalive

    def link_components(self, sm, pp, pv, homing, jog, session):
        """Liaison des dépendances d'exécution après connexion."""
        self._sm = sm
        self._pp = pp
        self._pv = pv
        self._homing = homing
        self._jog = jog
        self._session = session

    # ---- require helpers ----

    def _require(self, component: Any, name: str) -> Any:
        if component is None:
            raise RuntimeError(f"Not connected ({name} unavailable)")
        return component

    # ---- high-level controls ----

    async def enable_operation(self) -> None:
        sm = self._require(self._sm, "state machine")
        await sm.run_to_operation_enabled()

    async def disable_voltage(self) -> None:
        if self._session is not None:
            self._session.suppress_keepalive(2.0)
        await self._od.CONTROLWORD.write(cw_disable_voltage())
        await asyncio.sleep(0.3)

    async def stop(self, *, op_id: str | None = None) -> None:
        await self._execute_stop(mode="normal", op_id=op_id)

    async def quick_stop(self, *, op_id: str | None = None) -> None:
        await self._execute_stop(mode="quick", op_id=op_id)

    async def _execute_stop(self, mode: str, op_id: str | None = None) -> None:
        try:
            op_id = op_id or uuid.uuid4().hex[:8]
            label = "quick_stop" if mode == "quick" else "stop"

            self._abort_token = uuid.uuid4().hex
            self._abort_event.set()

            halt_ok = await self._halt_motor()
            if not halt_ok:
                _LOGGER.warning("%s[%s]: HALT write failed", label, op_id)

            status = await self._queries.get_status()
            if not (status.get("operation_enabled", False) or status.get("quick_stop", False)):
                return

            sm = self._require(self._sm, "state machine")
            if mode == "quick":
                await sm.quick_stop()
            else:
                current_mode = await self._od.MODES_OF_OPERATION_DISPLAY.read()
                if current_mode == 1 and self._pp is not None:
                    await self._pp.stop()
                elif current_mode == 3 and self._pv is not None:
                    await self._pv.stop()
                else:
                    await sm.quick_stop()
        except Exception:
            _LOGGER.error("_execute_stop failure", exc_info=True)

    async def _halt_motor(self) -> bool:
        halt_cw = cw_set_bits(cw_enable_operation(), CWBit.HALT)
        for attempt in range(2):
            try:
                await self._od.CONTROLWORD.write(int(halt_cw) & 0xFFFF)
                return True
            except Exception:
                await asyncio.sleep(0.05)
        return False

    async def fault_reset(self, *, recover: bool = True, op_id: str | None = None) -> None:
        op_id = op_id or uuid.uuid4().hex[:8]
        sm = self._require(self._sm, "state machine")
        status_before = await self._queries.get_status()

        await sm.fault_reset()
        if recover and status_before.get("fault", False):
            await sm.run_to_operation_enabled()
            halt_word = cw_set_bits(cw_enable_operation(), CWBit.HALT)
            await self._od.CONTROLWORD.write(int(halt_word) & 0xFFFF)

    # ---- move_to_position ----

    async def move_to_position(
        self, *, target_position: int, velocity: int, accel: int, decel: int,
        timeout_s: float = 20.0, require_homing: bool = True, op_id: str | None = None
    ) -> None:
        op_id = op_id or uuid.uuid4().hex[:8]

        if not self._queries.is_connected:  # Délégation à l'état de session via queries
            raise RuntimeError("Not connected")

        status = await self._queries.get_status_live()

        # Logique Jog pré-mouvement
        if self._jog is not None and self._jog.state.active:
            await self._jog.release()
            await asyncio.sleep(self._cfg.motion_precheck_delay_s)

        # Vérification mode PP
        try:
            current_mode = await self._od.MODES_OF_OPERATION_DISPLAY.read()
            wrong_mode = (current_mode != 1)
        except Exception:
            wrong_mode = True

        if not status.get("operation_enabled", False) or wrong_mode:
            await self._od.MODES_OF_OPERATION.write(1)
            await asyncio.sleep(self._cfg.mode_settle_delay_s)
            await self.enable_operation()
            status = await self._queries.get_status_live()

        if not status.get("remote", False):
            raise PreconditionFailed(f"Remote low. Statusword: {await self._od.STATUSWORD.read():04X}")

        # Validations limites
        _min_pos, _max_pos = self._queries._resolve_position_limits()
        if not (_min_pos <= target_position <= _max_pos):
            raise ValueError(f"Target {target_position} out of boundaries [{_min_pos}..{_max_pos}]")

        halt_cleared = cw_clear_bits(cw_enable_operation(), CWBit.HALT)
        await self._od.CONTROLWORD.write(int(halt_cleared) & 0xFFFF)
        await asyncio.sleep(self._cfg.mode_settle_delay_s)

        pp = self._require(self._pp, "profile position")
        motion_token = uuid.uuid4().hex
        self._abort_token = motion_token
        self._abort_event.clear()

        if require_homing and not await self._queries.is_homed():
            _LOGGER.warning("move_to_position[%s]: drive not homed", op_id)

        await pp.move_to_position(
            target_position=target_position, profile_velocity=velocity,
            profile_accel=accel, profile_decel=decel, timeout_s=timeout_s
        )

    # ---- homing & jog ----
    async def home(self, *, timeout_s: float = 30.0, op_id: str | None = None) -> HomingResult:
        status = await self._queries.get_status_live()
        if not status.get("remote", False):
            raise PreconditionFailed("Remote not enabled")
        homing = self._require(self._homing, "homing")
        self._abort_event.clear()
        return await homing.run(timeout_s=timeout_s)

    async def jog_start(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        jog = self._require(self._jog, "jog")
        ttl_s = ttl_ms / 1000.0 if ttl_ms is not None else None

        # Validation butée logicielle avant jog
        _min_pos, _max_pos = self._queries._resolve_position_limits()
        current_pos = await self._queries.get_position_live()
        if (velocity > 0 and current_pos >= _max_pos) or (velocity < 0 and current_pos <= _min_pos):
            raise RuntimeError("Target boundary reached, jog blocked")

        if jog.state.active or await self._is_jog_warm():
            await jog.press(velocity=velocity, ttl_s=ttl_s)
            return

        await jog.invalidate_mode()
        await self._od.CONTROLWORD.write(int(cw_clear_bits(cw_enable_operation(), CWBit.HALT)) & 0xFFFF)
        await asyncio.sleep(self._cfg.mode_settle_delay_s)
        await self._require(self._pv, "profile velocity").stop_velocity_zero()
        await jog.press(velocity=velocity, ttl_s=ttl_s)

    async def _is_jog_warm(self) -> bool:
        status = await self._queries.get_status()
        if not status.get("operation_enabled", False) or status.get("fault", False):
            return False
        return await self._od.MODES_OF_OPERATION_DISPLAY.read() == 3

    async def jog_update(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        jog = self._require(self._jog, "jog")
        if not jog.state.active: return
        ttl_s = ttl_ms / 1000.0 if ttl_ms is not None else None
        await jog.keepalive(velocity=velocity, ttl_s=ttl_s)

    async def jog_stop(self, *, op_id: str | None = None) -> None:
        if self._jog is not None and self._jog.state.active:
            await self._jog.release()