from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol.accessor import AsyncODAccessor

class ConnectedRegister(ABC):
    """Interface de base pour un registre lié de manière statique à son protocole."""

    def __init__(self, accessor: AsyncODAccessor, index: int, name: str, subindex: int = 0):
        self._accessor = accessor
        self.index = index
        self.name = name
        self.subindex = subindex

    @abstractmethod
    async def read(self) -> int: ...

    @abstractmethod
    async def write(self, value: int) -> None: ...

    async def __str__(self)->str:
        try:
            value = await self.read()
            reg_string = f"{self.name} at {hex(self.index)}[{self.subindex}]={value}"

        except Exception as e:
            reg_string = f"Cannot read {self.name} at {hex(self.index)} : {e}"
        return reg_string


class I16Register(ConnectedRegister):
    async def read(self) -> int:
        return await self._accessor.read_u16(self.index, self.subindex)

    async def write(self, value: int) -> None:
        await self._accessor.write_i16(self.index, value, self.subindex)

class U16Register(ConnectedRegister):
    async def read(self, index=0) -> int:
        if index:
            #TODO: fixme. Should use here a different function
            return await self._accessor.read_u16(self.index, index)
        else:
            return await self._accessor.read_u16(self.index, self.subindex)

    async def write(self, value: int) -> None:
        await self._accessor.write_u16(self.index, value, self.subindex)


class I32Register(ConnectedRegister):
    async def read(self) -> int:
        return await self._accessor.read_i32(self.index, self.subindex)

    async def write(self, value: int) -> None:
        await self._accessor.write_i32(self.index, value, self.subindex)


class U32Register(ConnectedRegister):
    async def read(self) -> int:
        return await self._accessor.read_u32(self.index, self.subindex)

    async def write(self, value: int) -> None:
        await self._accessor.write_u32(self.index, value, self.subindex)

class I8Register(ConnectedRegister):
    async def read(self) -> int:
        return await self._accessor.read_i8(self.index, self.subindex)

    async def write(self, value: int) -> None:
        await self._accessor.write_i8(self.index, value, self.subindex)

class U8Register(ConnectedRegister):
    async def read(self) -> int:
        return await self._accessor.read_u8(self.index, self.subindex)

    async def write(self, value: int) -> None:
        await self._accessor.write_u8(self.index, value, self.subindex)