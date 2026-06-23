"""Standard CiA 402 Object Dictionary indices used by the driver.

Note:
- These are the canonical indices defined by CiA 402 (and widely supported).
- Your drive may expose additional vendor-specific objects; add them here as needed.

We keep this file intentionally conservative: only the objects actually used by
the driver layers should be declared to avoid “constant sprawl”.
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
    POSITION_WINDOW = 0x6067  # symmetric window around target; bit10 "Target Reached" set inside it (manual p.172)
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


# Subindices for ODIndex.POSITION_RANGE_LIMIT (0x607B array). Manual p.174:
# sub1 min ("the value 0 must be entered in sub-index 1"), sub2 max (stroke).
POSITION_RANGE_LIMIT_MIN_SUB = 1
POSITION_RANGE_LIMIT_MAX_SUB = 2