"""DryveD1 facade — structured API via composition over Modbus TCP."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from ..cia402.state_machine import CiA402StateMachine
from ..motion.jog import JogController
from ..motion.profile_position import ProfilePosition
from ..motion.profile_velocity import ProfileVelocity
from ..telemetry.poller import TelemetryConfig, TelemetryPoller
from ..telemetry.snapshots import DriveSnapshot
from ..transport import ModbusSession

# Notre nouveau protocole abstrait pur et notre dictionnaire d'objets statiques
from ..protocol.accessor import AsyncODAccessor
from ..od.dictionary import ObjectDictionary

# Nos deux nouveaux composants de service issus du refactoring
from .status_queries import StatusQueries
from .motion_commands import MotionCommands
from dataclasses import dataclass
from typing import Any

from ..cia402.state_machine import CiA402StateMachine, StateMachineConfig
from ..config.models import DriveConfig as UserDriveConfig
from ..motion.homing import HomingConfig, HomingResult
from ..motion.jog import JogConfig as MotionJogConfig
from ..cia402.state_machine import CiA402StateMachine
from ..motion.jog import JogController
from ..motion.profile_position import ProfilePosition, ProfilePositionConfig
from ..motion.profile_velocity import ProfileVelocity, ProfileVelocityConfig
from ..od.indices import ODIndex

_LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class DryveD1Config:
    """High-level configuration for the DryveD1 facade."""

    drive: UserDriveConfig
    state_machine: StateMachineConfig = StateMachineConfig()
    profile_position: ProfilePositionConfig = ProfilePositionConfig()
    profile_velocity: ProfileVelocityConfig = ProfileVelocityConfig()
    homing: HomingConfig = HomingConfig()
    jog: MotionJogConfig = MotionJogConfig()
    idle_shutdown_delay_s: float = 8.0
    velocity_threshold: int = 10  # drive units; below this the axis is "stationary"
    mode_settle_delay_s: float = 0.05  # delay after mode/controlword writes
    motion_precheck_delay_s: float = 0.1  # delay when stopping motion before a new command

class DryveD1(AsyncODAccessor):
    """Async facade for dryve D1.

    This class strictly implements the AsyncODAccessor interface contract.
    All high-level business capabilities are split into specialized components
    via composition rather than procedural mixins.
    """

    def __init__(self, *, config: DryveD1Config) -> None:
        self._cfg = config
        self._session: ModbusSession | None = None
        self._modbus_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="dryve-modbus"
        )

        from ..protocol import SDOClient
        self._sdo = SDOClient(unit_id=config.drive.connection.unit_id)

        # LE CONTRAT EST REMPLI : 'self' est injecté de manière statique et sécurisée.
        # Le vérificateur de type valide car DryveD1 réalise l'interface AsyncODAccessor.
        self.od = ObjectDictionary(accessor=self)

        # Composition des couches fonctionnelles supérieures
        self._queries = StatusQueries(self.od, self._cfg)
        self._commands = MotionCommands(self.od, self._queries, self._cfg)

        self._telemetry_poller: TelemetryPoller | None = None
        self._reconnect_loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_stop_scheduled = False

    # =========================================================================
    # RÉALISATION DU CONTRAT AsyncODAccessor (Transport Modbus Réseau)
    # =========================================================================

    async def read_i16(self, index: int, subindex: int = 0) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_read_int(
            index=index,
            subindex=subindex,
            size=2,
            signed=True,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        return self._sdo.decode_read_int(response_adu, request=telegram, signed=True)


    async def read_u16(self, index: int, subindex: int = 0) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_read_int(
            index=index,
            subindex=subindex,
            size=2,
            signed=False,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        return self._sdo.decode_read_int(response_adu, request=telegram, signed=False)

    async def read_i32(self, index: int, subindex: int = 0) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_read_int(
            index=index,
            subindex=subindex,
            size=4,
            signed=True,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        return self._sdo.decode_read_int(response_adu, request=telegram, signed=True)

    async def read_u32(self, index: int, subindex: int = 0) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_read_int(
            index=index,
            subindex=subindex,
            size=4,
            signed=False,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        return self._sdo.decode_read_int(response_adu, request=telegram, signed=False)

    async def read_i8(self, index: int, subindex: int = 0) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_read_int(
            index=index,
            subindex=subindex,
            size=1,
            signed=True,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        return self._sdo.decode_read_int(response_adu, request=telegram, signed=True)

    async def read_u8(self, index: int, subindex: int = 0) -> int:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_read_int(
            index=index,
            subindex=subindex,
            size=1,
            signed=False,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        return self._sdo.decode_read_int(response_adu, request=telegram, signed=False)

    async def write_i16(self, index: int, value: int, subindex: int = 0) -> None:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_write_int(
            index=index,
            subindex=subindex,
            value=value,
            size=2,
            signed=True,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        self._sdo.parse_write_response(response_adu, request=telegram)

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_write_int(
            index=index,
            subindex=subindex,
            value=value,
            size=2,
            signed=False,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        self._sdo.parse_write_response(response_adu, request=telegram)

    async def write_i8(self, index: int, value: int, subindex: int = 0) -> None:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_write_int(
            index=index,
            subindex=subindex,
            value=value,
            size=1,
            signed=True,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        self._sdo.parse_write_response(response_adu, request=telegram)

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_write_int(
            index=index,
            subindex=subindex,
            value=value,
            size=1,
            signed=False,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        self._sdo.parse_write_response(response_adu, request=telegram)

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_write_int(
            index=index,
            subindex=subindex,
            value=value,
            size=4,
            signed=True,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        self._sdo.parse_write_response(response_adu, request=telegram)

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        if self._session is None:
            raise RuntimeError("Not connected")

        telegram = self._sdo.build_write_int(
            index=index,
            subindex=subindex,
            value=value,
            size=4,
            signed=False,
            transaction_id=self._session.next_transaction_id()
        )

        response_adu = await asyncio.get_running_loop().run_in_executor(
            self._modbus_executor,
            self._session.transceive,
            telegram.adu
        )
        self._sdo.parse_write_response(response_adu, request=telegram)


    # =========================================================================
    # API PUBLIQUE ET REDIRECTION VERS LES COMPOSANTS (Backward Compatibility)
    # =========================================================================

    @property
    def is_connected(self) -> bool:
        return self._session is not None and self._session.is_connected

    # =========================================================================
    # GESTION DU LIFECYCLE (Connexion, Initialisation, Télémétrie)
    # =========================================================================

    async def connect(self, *, telemetry_callback: Callable[[DriveSnapshot], None] | None = None) -> None:
        if self._session is not None:
            return

        # [...] Configuration et établissement de la ModbusSession (Code inchangé)

        # Instanciation de la cinématique et de la machine à états de bas niveau
        sm = CiA402StateMachine(self, config=self._cfg.state_machine)
        pp = ProfilePosition(self, config=self._cfg.profile_position, abort_event=self._commands._abort_event)
        pv = ProfileVelocity(self, config=self._cfg.profile_velocity, abort_event=self._commands._abort_event)

        from ..motion.homing import Homing
        homing = Homing(self, config=self._cfg.homing, abort_event=self._commands._abort_event)
        jog = JogController(self, config=self._cfg.jog, abort_event=self._commands._abort_event)

        # Couplage des blocs opérationnels internes dans les modules de commande et requête
        self._commands.link_components(sm, pp, pv, homing, jog, self._session)
        self._queries.set_jog_controller(jog)

        # Initialisation de la boucle de télémétrie asynchrone
        telemetry_cfg = TelemetryConfig(
            interval_s=0.5,
            read_position=True,
            read_velocity=True,
            read_mode_display=True,
            tolerate_errors=True
        )
        self._telemetry_poller = TelemetryPoller(self, config=telemetry_cfg, on_snapshot=telemetry_callback)
        self._telemetry_poller.start()
        self._queries.set_telemetry_poller(self._telemetry_poller)

        # Validation de sécurité immédiate après branchement
        sw = await self.od.STATUSWORD.read()
        _LOGGER.info("Post-connect validation complete: statusword=0x%04X", sw)

    async def close(self) -> None:
        if self._session is None:
            return
        if self._telemetry_poller is not None:
            await self._telemetry_poller.stop()

        # [...] Nettoyage des sockets, fermeture de l'exécuteur de threads Modbus
        self._modbus_executor.shutdown(wait=True)
        self._session = None


    async def get_position(self) -> int:
        """Garantit la compatibilité avec l'ancien StatusQueriesMixin.get_position"""
        return await self._queries.get_position()

    async def get_position_live(self) -> int:
        return await self._queries.get_position_live()

    async def is_moving(self) -> bool:
        return await self._queries.is_moving()

    async def get_status(self) -> dict[str, bool]:
        return await self._queries.get_status()

    async def get_status_live(self) -> dict[str, bool]:
        return await self._queries.get_status_live()

    async def get_statusword(self) -> int:
        return await self._queries.get_statusword()

    async def get_cia402_state(self) -> Any:
        return await self._queries.get_cia402_state()

    async def get_velocity_actual(self) -> int:
        return await self._queries.get_velocity_actual()

    async def get_mode_display(self) -> int:
        return await self._queries.get_mode_display()

    async def is_homed(self) -> bool:
        return await self._queries.is_homed()

    async def read_fault_info(self, *, include_history: bool = True) -> dict:
        return await self._queries.read_fault_info(include_history=include_history)

    async def set_position_limits(self, min_position: int, max_position: int) -> None:
        await self._queries.set_position_limits(min_position, max_position)

    async def get_position_limits(self) -> tuple[int, int]:
        return await self._queries.get_position_limits()

    # =========================================================================
    # RESTAURATION DE L'API PUBLIQUE HISTORIQUE (Délégation de MotionCommands)
    # =========================================================================

    async def enable_operation(self) -> None:
        await self._commands.enable_operation()

    async def disable_voltage(self) -> None:
        await self._commands.disable_voltage()

    async def stop(self, *, op_id: str | None = None) -> None:
        await self._commands.stop(op_id=op_id)

    async def quick_stop(self, *, op_id: str | None = None) -> None:
        await self._commands.quick_stop(op_id=op_id)

    async def fault_reset(self, *, recover: bool = True, op_id: str | None = None) -> None:
        await self._commands.fault_reset(recover=recover, op_id=op_id)

    async def move_to_position(self, **kwargs) -> None:
        await self._commands.move_to_position(**kwargs)

    async def home(self, *, timeout_s: float = 30.0, op_id: str | None = None) -> HomingResult:
        return await self._commands.home(timeout_s=timeout_s, op_id=op_id)

    async def jog_start(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        await self._commands.jog_start(velocity=velocity, ttl_ms=ttl_ms, op_id=op_id)

    async def jog_update(self, *, velocity: int, ttl_ms: int | None = None, op_id: str | None = None) -> None:
        await self._commands.jog_update(velocity=velocity, ttl_ms=ttl_ms, op_id=op_id)

    async def jog_stop(self, *, op_id: str | None = None) -> None:
        await self._commands.jog_stop(op_id=op_id)
