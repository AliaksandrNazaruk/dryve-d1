"""Canonical AsyncODAccessor Protocol for the CiA 402 driver framework.

All driver sub-modules (state machine, motion, telemetry) depend on this
single definition rather than declaring local copies.
"""

from __future__ import annotations

from typing import Protocol


class AsyncODAccessor(Protocol):
    """Minimal async Object-Dictionary accessor.

    Every method corresponds to a typed SDO read or write.  The suffix
    encodes the CiA 402 data type: u8/u16/u32 = unsigned, i8/i32 = signed.
    """

    async def read_u16(self, index: int, subindex: int = 0) -> int: ...
    async def read_i8(self, index: int, subindex: int = 0) -> int: ...
    async def read_i32(self, index: int, subindex: int = 0) -> int: ...
    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None: ...
    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None: ...
