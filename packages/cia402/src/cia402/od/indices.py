"""Standard CiA 402 Object Dictionary indices.

Only canonical CiA 402 indices are declared here. Device-specific
(vendor) indices belong in the respective plugin package.
"""

from __future__ import annotations

from enum import IntEnum


class ODIndex(IntEnum):
    # --- CiA 402 core ---
    CONTROLWORD = 0x6040
    STATUSWORD = 0x6041

    MODES_OF_OPERATION = 0x6060
    MODES_OF_OPERATION_DISPLAY = 0x6061

    POSITION_ACTUAL_VALUE = 0x6064
    VELOCITY_ACTUAL_VALUE = 0x606C

    TARGET_POSITION = 0x607A
    TARGET_VELOCITY = 0x60FF

    PROFILE_VELOCITY = 0x6081
    PROFILE_ACCELERATION = 0x6083
    PROFILE_DECELERATION = 0x6084
    QUICK_STOP_DECELERATION = 0x6085

    # --- Homing ---
    HOMING_METHOD = 0x6098
    HOMING_SPEEDS = 0x6099
    HOMING_ACCELERATION = 0x609A

    # --- Diagnostics ---
    ERROR_CODE = 0x603F
    MANUFACTURER_STATUS_REGISTER = 0x1002

    # --- Optional ---
    FOLLOWING_ERROR_ACTUAL_VALUE = 0x60F4

    # --- Software position limits ---
    MIN_POSITION_LIMIT = 0x607B
    MAX_POSITION_LIMIT = 0x607D
