"""Standard CiA 402 Object Dictionary indices used by the driver.

Note:
- These are the canonical indices defined by CiA 402 (and widely supported).
- Your drive may expose additional vendor-specific objects; add them here as needed.

We keep this file intentionally conservative: only the objects actually used by
the driver layers should be declared to avoid “constant sprawl”.
"""

from __future__ import annotations

from enum import IntEnum

from .register import U16Register
from .register import I8Register, U8Register, I32Register, U32Register

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol.accessor import AsyncODAccessor


class ODIndex(IntEnum):
    # --- CiA 402 core ---
    CONTROLWORD = 0x6040
    STATUSWORD = 0x6041

    MODES_OF_OPERATION = 0x6060
    MODES_OF_OPERATION_DISPLAY = 0x6061

    POSITION_ACTUAL_VALUE = 0x6064
    VELOCITY_ACTUAL_VALUE = 0x606C  # actual velocity (often signed)

    TARGET_POSITION = 0x607A
    TARGET_VELOCITY = 0x60FF

    PROFILE_VELOCITY = 0x6081
    PROFILE_ACCELERATION = 0x6083
    PROFILE_DECELERATION = 0x6084
    QUICK_STOP_DECELERATION = 0x6085

    # --- Homing (if supported) ---
    HOMING_METHOD = 0x6098
    HOMING_SPEEDS = 0x6099          # typically subindex 1/2
    HOMING_ACCELERATION = 0x609A

    # --- Diagnostics / errors ---
    ERROR_CODE = 0x603F             # standard error code
    MANUFACTURER_STATUS_REGISTER = 0x1002  # often present, optional

    # --- Optional: following error / limits ---
    FOLLOWING_ERROR_ACTUAL_VALUE = 0x60F4  # optional

    # --- Position Range Limit (dryve D1 manual §6, p.174) ---
    # A CiA402 ARRAY at 0x607B: sub0=count(Const=2), sub1=min, sub2=max.
    # The dryve D1 has NO 0x607D "Software Position Limit" object — accessing
    # 0x607D, or writing the Const sub0, returns gateway error func=0xAB/0xFF.
    # Use the POSITION_RANGE_LIMIT_*_SUB subindices below.
    POSITION_RANGE_LIMIT = 0x607B  # min=sub1, max=sub2 (INT32, RWW)

    # --- Vendor-specific (dryve D1) ---
    HOMING_STATUS = 0x2014  # dryve D1 specific: homing status (UINT16, RO)
    POSITION_WINDOW = 0x6067 # Position window
    POSITION_WINDOW_TIME = 0x6068 # Position window time.

class ObjectDictionary:
    """Static structure of memory map of CiA 402 dryve D1."""

    def __init__(self, accessor: AsyncODAccessor):
        # --- CiA 402 core ---
        self.CONTROLWORD = U16Register(accessor, 0x6040, "CONTROLWORD")
        self.STATUSWORD = U16Register(accessor, 0x6041, "STATUSWORD")

        self.HOMING_METHOD = U8Register(accessor, 0x6098, "HOMING_METHOD")
        self.HOMING_SPEED_MIN = U32Register(accessor, 0x6099, "HOMING_SPEED MIN", 1)
        self.HOMING_SPEED_MAX = U32Register(accessor, 0x6099, "HOMING_SPEED MAX", 2)
        self.HOMING_STATUS = U16Register(accessor, 0x2014, "HOMING STATUS")
        self.HOMING_ACCELERATION = U32Register(accessor, 0x609A, "HOMING_ACCELERATION")

        self.MODES_OF_OPERATION = I8Register(accessor, 0x6060, "MODES_OF_OPERATION")
        self.MODES_OF_OPERATION_DISPLAY = I8Register(accessor, 0x6061, "MODES_OF_OPERATION_DISPLAY")

        self.ERROR_CODE = U16Register(accessor, 0x603F, "ERROR_CODE")
        self.MANUFACTURER_STATUS_REGISTER = U16Register(accessor, 0x1002, "MANUFACTURER_STATUS_REGISTER")

        self.FOLLOWING_ERROR_ACTUAL_VALUE = U32Register(accessor, 0x60F4, "FOLLOWING_ERROR_ACTUAL_VALUE")

        self.OD_ERROR_REGISTER=U16Register(accessor, 0x1001, "OD_ERROR_REGISTER")
        self.OD_PREDEFINED_ERROR_FIELD=U16Register(accessor, 0x1003, "OD_PREDEFINED_ERROR_FIELD")
        self.VELOCITY_ACTUAL_VALUE = I32Register(accessor, 0x606C, "VELOCITY_ACTUAL_VALUE")
        self.POSITION_ACTUAL_VALUE = I32Register(accessor, 0x6064, "POSITION_ACTUAL_VALUE")
        self.TARGET_POSITION = I32Register(accessor, 0x607A, "TARGET_POSITION")
        self.TARGET_VELOCITY = I32Register(accessor, 0x60FF, "TARGET_VELOCITY")
        self.PROFILE_VELOCITY = I32Register(accessor, 0x6081, "PROFILE_VELOCITY")
        self.PROFILE_ACCELERATION = I32Register(accessor, 0x6083, "PROFILE_ACCELERATION")
        self.PROFILE_DECELERATION = I32Register(accessor, 0x6084, "PROFILE_DECELERATION")
        self.QUICK_STOP_DECELERATION= U32Register(accessor, 0x6085, "QUICK_STOP_DECELERATION")
        self.POSITION_WINDOW = U32Register(accessor, 0x6067, "POSITION WINDOW")
        self.POSITION_WINDOW_TIME = U32Register(accessor, 0x6068, "POSITION_WINDOW_TIME")
        # --- Position Range Limit (0x607B) ---
        self.LIMIT_MIN = I32Register(accessor, 0x607B, "POSITION_RANGE_LIMIT_MIN", subindex=1)
        self.LIMIT_MAX = I32Register(accessor, 0x607B, "POSITION_RANGE_LIMIT_MAX", subindex=2)



# Subindices for ODIndex.POSITION_RANGE_LIMIT (0x607B array). Manual p.174:
# sub1 min ("the value 0 must be entered in sub-index 1"), sub2 max (stroke).
POSITION_RANGE_LIMIT_MIN_SUB = 1
POSITION_RANGE_LIMIT_MAX_SUB = 2

