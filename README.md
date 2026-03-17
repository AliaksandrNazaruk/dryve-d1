# dryve-d1

[![CI](https://github.com/AliaksandrNazaruk/dryve-d1/actions/workflows/ci.yml/badge.svg)](https://github.com/AliaksandrNazaruk/dryve-d1/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dryve-d1.svg)](https://pypi.org/project/dryve-d1/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typed-PEP%20561-blue.svg)](https://peps.python.org/pep-0561/)

Async Python driver for [igus dryve D1](https://www.igus.com/info/drive-technology-dryve-d1) stepper/servo motor controllers over **Modbus/TCP**. Implements the **CiA 402** (CANopen Drive Profile) state machine in pure Python — no `pymodbus` dependency.

## Installation

```bash
pip install dryve-d1
```

## Quick Start

```python
import asyncio
from dryve_d1 import DryveD1
from dryve_d1.config.defaults import default_driver_config

async def main():
    cfg = default_driver_config(host="192.168.1.100", unit_id=1)
    drive = DryveD1(config=cfg)
    await drive.connect()
    try:
        await drive.fault_reset()
        await drive.enable_operation()
        await drive.home()
        await drive.move_to_position(target_position=10000)
    finally:
        await drive.close()

asyncio.run(main())
```

## Features

- **CiA 402 state machine** — full lifecycle: enable, disable, quick stop, fault reset
- **Profile position** — move to absolute/relative position with velocity and acceleration
- **Profile velocity** — continuous velocity control
- **Jog (hold-to-move)** — with configurable watchdog TTL and keepalive
- **Homing** — configurable homing methods with timeout
- **Telemetry poller** — periodic status polling with `DriveSnapshot` caching
- **Pydantic config** — `DriveConfig`, `ConnectionConfig`, `MotionLimits`, `RetryPolicy`
- **Pure async** — built on `asyncio`, no blocking calls
- **PEP 561 typed** — full type annotations, `py.typed` marker included
- **Zero heavy deps** — only `pydantic` required (no `pymodbus`)

## Bundled Simulator

Develop and test without hardware using the included Modbus TCP simulator:

```bash
python simulator.py          # starts on port 501
```

Then connect with `host="127.0.0.1"`, `port=501`, `unit_id=0`.

## Architecture

```
┌──────────────────────────────────────────┐
│  dryve_d1.api.drive.DryveD1             │  ← async facade
├──────────────────────────────────────────┤
│  dryve_d1.cia402      │  dryve_d1.motion │  ← state machine + motion profiles
├──────────────────────────────────────────┤
│  dryve_d1.protocol    │  dryve_d1.od     │  ← Modbus/CANopen codec + OD registers
├──────────────────────────────────────────┤
│  dryve_d1.transport                      │  ← TCP client, session, retry
└──────────────────────────────────────────┘
```

## Development

```bash
git clone https://github.com/AliaksandrNazaruk/dryve-d1.git
cd dryve-d1
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest tests/unit -m "not simulator"

# Lint & type check
ruff check src/ tests/
mypy src/dryve_d1
```

## License

[MIT](LICENSE) &copy; 2026 Aliaksandr Nazaruk
