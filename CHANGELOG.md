# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
