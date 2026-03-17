"""Fault decode and reset routines (CiA 402 standard).

Reads diagnostic objects (0x603F Error Code, 0x1001 Error Register,
0x1003 Pre-defined Error Field) and performs a fault reset sequence.
Device-specific preconditions are injected via DeviceHooks.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

_LOGGER = logging.getLogger(__name__)

from ..od.controlword import CWBit, cw_fault_reset, cw_set_bits, cw_shutdown
from ..od.indices import ODIndex
from ..od.statusword import SWBit
from ..od.accessor import AsyncODAccessor
from ..transport.clock import monotonic_s
from .bits import _U16_MASK
from .bits import bit_is_set as _bit

if TYPE_CHECKING:
    from ..plugin import DeviceHooks

# Standard CANopen diagnostic objects
OD_ERROR_REGISTER = 0x1001
OD_PREDEFINED_ERROR_FIELD = 0x1003


class FaultResetError(RuntimeError):
    """Raised when a fault reset attempt fails or times out."""


@dataclass(frozen=True, slots=True)
class FaultInfo:
    statusword: int
    error_code: int | None = None
    error_register: int | None = None
    history: list[int] | None = None

    def as_dict(self) -> dict:
        return {
            "statusword": f"0x{int(self.statusword) & _U16_MASK:04X}",
            "error_code": None if self.error_code is None else f"0x{int(self.error_code) & _U16_MASK:04X}",
            "error_register": None if self.error_register is None else f"0x{int(self.error_register) & 0xFF:02X}",
            "history": None if self.history is None else [f"0x{int(x) & _U16_MASK:04X}" for x in self.history],
        }


class FaultManager:
    """Reads fault diagnostics and performs a fault reset sequence."""

    def __init__(self, od: AsyncODAccessor, *, hooks: DeviceHooks | None = None) -> None:
        self._od = od
        if hooks is None:
            from ..plugin import NullDeviceHooks
            hooks = NullDeviceHooks()
        self._hooks = hooks

    async def read_statusword(self) -> int:
        return int(await self._od.read_u16(int(ODIndex.STATUSWORD), 0)) & _U16_MASK

    async def read_error_code(self) -> int:
        return int(await self._od.read_u16(int(ODIndex.ERROR_CODE), 0)) & _U16_MASK

    async def read_error_register(self) -> int:
        return int(await self._od.read_u16(OD_ERROR_REGISTER, 0)) & 0x00FF

    async def read_error_history(self, *, max_entries: int = 8) -> list[int]:
        """Read Pre-defined Error Field (0x1003) if present."""
        try:
            count = int(await self._od.read_u16(OD_PREDEFINED_ERROR_FIELD, 0)) & 0x00FF
        except (TimeoutError, OSError, ConnectionError):
            _LOGGER.debug("Error history unavailable (connection issue)", exc_info=True)
            return []
        except Exception:
            _LOGGER.warning("Unexpected error reading error history count", exc_info=True)
            return []
        count = max(0, min(int(count), int(max_entries)))
        hist: list[int] = []
        for si in range(1, count + 1):
            try:
                hist.append(int(await self._od.read_u16(OD_PREDEFINED_ERROR_FIELD, si)) & _U16_MASK)
            except (TimeoutError, OSError, ConnectionError):
                _LOGGER.debug("Error history entry %d unavailable (connection issue)", si, exc_info=True)
                break
            except Exception:
                _LOGGER.warning("Unexpected error reading error history entry %d", si, exc_info=True)
                break
        return hist

    async def read_fault_info(self, *, include_history: bool = True) -> FaultInfo:
        sw = await self.read_statusword()
        info = FaultInfo(statusword=sw)
        if _bit(sw, SWBit.FAULT):
            try:
                ec = await self.read_error_code()
            except (TimeoutError, OSError, ConnectionError):
                _LOGGER.debug("Error code unavailable (connection issue)", exc_info=True)
                ec = None
            except Exception:
                _LOGGER.warning("Unexpected error reading error code", exc_info=True)
                ec = None
            try:
                er = await self.read_error_register()
            except (TimeoutError, OSError, ConnectionError):
                _LOGGER.debug("Error register unavailable (connection issue)", exc_info=True)
                er = None
            except Exception:
                _LOGGER.warning("Unexpected error reading error register", exc_info=True)
                er = None
            hist = None
            if include_history:
                try:
                    hist_list = await self.read_error_history()
                    hist = hist_list if hist_list else None
                except (TimeoutError, OSError, ConnectionError):
                    _LOGGER.debug("Error history unavailable (connection issue)", exc_info=True)
                    hist = None
                except Exception:
                    _LOGGER.warning("Unexpected error reading error history", exc_info=True)
                    hist = None
            return FaultInfo(statusword=sw, error_code=ec, error_register=er, history=hist)
        return info

    async def reset_fault(self, *, timeout_s: float = 5.0, poll_interval_s: float = 0.05) -> None:
        """Attempt to reset a fault (standalone, without state machine context).

        Device hooks may enforce preconditions (e.g., REMOTE bit must be high).
        """
        sw0 = await self.read_statusword()
        if not _bit(sw0, SWBit.FAULT):
            return
        self._hooks.pre_state_transition(sw0)

        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(cw_fault_reset()) & _U16_MASK, 0)
        await asyncio.sleep(poll_interval_s)

        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(cw_shutdown()) & _U16_MASK, 0)

        safe_halt_shutdown = cw_set_bits(cw_shutdown(), CWBit.HALT)
        await self._od.write_u16(int(ODIndex.CONTROLWORD), int(safe_halt_shutdown) & _U16_MASK, 0)

        deadline = monotonic_s() + float(timeout_s)
        while True:
            sw = await self.read_statusword()
            if not _bit(sw, SWBit.FAULT):
                return
            if monotonic_s() >= deadline:
                raise FaultResetError(f"Fault reset timed out; statusword=0x{sw:04X}")
            await asyncio.sleep(poll_interval_s)
