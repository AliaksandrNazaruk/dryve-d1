# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-06-23

### Fixed

- **Profile Position set-point handshake**: `move_to()` now holds Controlword
  bit 4 (New Set-point) high until the drive acknowledges (Statusword bit 12
  "Set-point Acknowledge" or bit 10 clearing), then releases it — the
  closed-loop handshake mandated by the dryve D1 manual §5.6.10.2. The previous
  open-loop pulse released bit 4 before the drive latched the set-point, and a
  `delta <= 250` position heuristic masked the failure as success. A set-point
  that is never acknowledged now raises `TimeoutError` instead of silently
  reporting a move that never happened.
- **Position Range Limit addressing**: `set_position_limits()` /
  `get_position_limits()` now use object `0x607B` sub-index 1 (min) and 2 (max),
  per the dryve D1 manual (p.174). The drive has no `0x607D` object; the old
  generic-CiA402 mapping (`0x607B`/`0x607D`, sub-index 0) returned gateway error
  `func=0xAB code=0xFF`, so software limits were never applied — and a drive with
  stroke (`0x607B` sub2) `== 0` silently refuses all motion.
- `ProfilePosition.ensure_mode()` now confirms `0x6061` (Modes of Operation
  Display) per manual §5.6.10, warning (not silently skipping) on a stale gateway.

### Added

- `ODIndex.POSITION_RANGE_LIMIT` plus `POSITION_RANGE_LIMIT_MIN_SUB` /
  `POSITION_RANGE_LIMIT_MAX_SUB` sub-index constants.
- `ProfilePositionConfig.setpoint_ack_timeout_s`.
- Simulator now models Statusword bit 12 "Set-point Applied" in Profile Position
  and the `0x607B` Position Range Limit array (sub1/sub2), so the closed-loop
  handshake and limit addressing are exercised without hardware.

### Changed

- `wait_target_reached()` takes `motion_started` (was `_ack_seen`); the
  internal ack helper is `_wait_setpoint_ack()` (was `_wait_start_acknowledgment`).

## [0.2.0] - 2026-03-17

### Added

- Initial PyPI release as standalone package (extracted from igus-dryve-d1 service)
- Async DryveD1 facade for Modbus/TCP communication
- CiA 402 state machine (enable, disable, fault reset, homing)
- Motion control: profile position, profile velocity, jog (hold-to-move)
- Telemetry poller with DriveSnapshot caching
- Pydantic configuration models (DriveConfig, ConnectionConfig, MotionLimits)
- PEP 561 py.typed marker for downstream type checking
- Bundled Modbus TCP simulator for development without hardware
- Comprehensive test suite: unit, integration, property-based (Hypothesis)
