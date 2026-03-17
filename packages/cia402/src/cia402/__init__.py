"""cia402 — Generic async CiA 402 (CANopen Drive Profile) driver framework.

Public API:
- AsyncODAccessor: Protocol for object dictionary access
- DeviceHooks / NullDeviceHooks: Plugin system for device-specific behavior
- CiA402StateMachine: State machine runner
- FaultManager: Fault diagnostics and reset
"""

from .od.accessor import AsyncODAccessor
from .plugin import DeviceHooks, NullDeviceHooks
from .cia402.state_machine import CiA402StateMachine, StateMachineConfig
from .cia402.fault import FaultManager, FaultInfo
from .cia402.preconditions import PreconditionFailed
from .version import __version__

__all__ = [
    "AsyncODAccessor",
    "CiA402StateMachine",
    "DeviceHooks",
    "FaultInfo",
    "FaultManager",
    "NullDeviceHooks",
    "PreconditionFailed",
    "StateMachineConfig",
    "__version__",
]
