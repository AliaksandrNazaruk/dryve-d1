import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..od.indices import ObjectDictionary
    from ..od.register import ConnectedRegister


class Debug:
    def __init__(self, od: ObjectDictionary, name: str):
        self._od = od
        self._logger = logging.getLogger(name)

    async def _print_register(self, tag: str, reg: ConnectedRegister):
        try:
            value = await reg.read()
            self._logger.info(
                f"[{tag}] {reg.name} at {hex(reg.index)}[{reg.subindex}]={value}"
            )
        except Exception as e:
            self._logger.error(
                f"[{tag}] Cannot read {reg.name} at {hex(reg.index)} : {e}"
            )

    async def dump_profiles_position_registers(self, tag: str):
        registers = [
            self._od.CONTROLWORD,
            self._od.STATUSWORD,

            self._od.MODES_OF_OPERATION,
            self._od.MODES_OF_OPERATION_DISPLAY,

            self._od.TARGET_POSITION,
            self._od.POSITION_ACTUAL_VALUE,
            self._od.ERROR_CODE,

            self._od.PROFILE_VELOCITY,
            self._od.PROFILE_ACCELERATION,
            self._od.PROFILE_DECELERATION,

            self._od.LIMIT_MIN,
            self._od.LIMIT_MAX,

            self._od.POSITION_WINDOW,
            self._od.POSITION_WINDOW_TIME,
        ]

        for reg in registers:
            await self._print_register(tag, reg)