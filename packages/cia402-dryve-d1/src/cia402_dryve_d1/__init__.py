"""cia402-dryve-d1 — igus dryve D1 plugin for the CiA 402 driver framework.

Provides:
- DryveD1Hooks: Device-specific hooks for the generic CiA 402 state machine
- igus proprietary Modbus gateway protocol (FC 0x2B / MEI 0x0D)
- Bundled Modbus TCP simulator for testing without hardware
"""

from .hooks import DryveD1Hooks
from .version import __version__

__all__ = [
    "DryveD1Hooks",
    "__version__",
]
