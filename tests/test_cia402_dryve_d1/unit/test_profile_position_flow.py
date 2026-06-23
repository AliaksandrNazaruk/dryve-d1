from __future__ import annotations

import pytest

from dryve_d1.motion.profile_position import ProfilePosition, ProfilePositionConfig
from dryve_d1.od.controlword import CWBit
from dryve_d1.od.indices import ODIndex
from dryve_d1.od.statusword import SWBit

SW_TARGET_REACHED = 1 << int(SWBit.TARGET_REACHED)   # bit10
SW_SETPOINT_ACK = 1 << int(SWBit.OP_MODE_SPECIFIC)   # bit12 ("Setpoint applied" in PP)


def _fast_cfg(**overrides) -> ProfilePositionConfig:
    base = dict(
        verify_mode=False,
        mode_settle_s=0.0,
        system_cycle_delay_s=0.001,
        poll_interval_s=0.0,
        move_timeout_s=0.5,
        setpoint_ack_timeout_s=0.05,
    )
    base.update(overrides)
    return ProfilePositionConfig(**base)


def _bit(word: int, bit: int) -> bool:
    return bool((int(word) >> int(bit)) & 1)


class _FakeOD:
    def __init__(
        self,
        *,
        statuswords: list[int] | None = None,
        const_statusword: int | None = None,
    ) -> None:
        # If statuswords is given it is consumed in order; once exhausted (or if
        # only const_statusword is provided) every read returns const_statusword.
        self.statuswords = list(statuswords or [])
        self.const_statusword = const_statusword
        self.writes_u8: list[tuple[int, int, int]] = []
        self.writes_u16: list[tuple[int, int, int]] = []
        self.writes_u32: list[tuple[int, int, int]] = []
        self.writes_i32: list[tuple[int, int, int]] = []
        self.read_i32_value = 0
        # Ordered event log: ("read_sw",) and ("write_cw", value) for handshake
        # ordering assertions.
        self.events: list[tuple] = []

    async def read_u16(self, index: int, subindex: int = 0) -> int:
        if index == int(ODIndex.STATUSWORD):
            self.events.append(("read_sw",))
        if self.statuswords:
            return self.statuswords.pop(0)
        if self.const_statusword is not None:
            return self.const_statusword
        return 0

    async def read_i8(self, index: int, subindex: int = 0) -> int:
        return 1

    async def read_i32(self, index: int, subindex: int = 0) -> int:
        return int(self.read_i32_value)

    async def write_u16(self, index: int, value: int, subindex: int = 0) -> None:
        if index == int(ODIndex.CONTROLWORD):
            self.events.append(("write_cw", value))
        self.writes_u16.append((index, value, subindex))

    async def write_u8(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes_u8.append((index, value, subindex))

    async def write_u32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes_u32.append((index, value, subindex))

    async def write_i32(self, index: int, value: int, subindex: int = 0) -> None:
        self.writes_i32.append((index, value, subindex))


async def test_ensure_mode_always_writes_mode_register() -> None:
    od = _FakeOD()
    pp = ProfilePosition(
        od,
        config=ProfilePositionConfig(
            verify_mode=False,
            mode_settle_s=0.0,
        ),
    )

    await pp.ensure_mode()

    assert (int(ODIndex.MODES_OF_OPERATION), 1, 0) in od.writes_u8


async def test_ensure_mode_tolerates_6061_read_failure_when_not_verifying() -> None:
    """verify_mode=False: a failing 0x6061 read must NOT abort (delay-only fallback)."""
    od = _FakeOD()

    async def _boom(index: int, subindex: int = 0) -> int:
        raise TimeoutError("gateway read timeout")

    od.read_i8 = _boom  # type: ignore[assignment]
    pp = ProfilePosition(od, config=_fast_cfg(verify_mode=False))

    await pp.ensure_mode()  # must not raise
    assert (int(ODIndex.MODES_OF_OPERATION), 1, 0) in od.writes_u8


async def test_ensure_mode_raises_on_6061_read_failure_when_verifying() -> None:
    """verify_mode=True: a failing 0x6061 read propagates (strict)."""
    od = _FakeOD()

    async def _boom(index: int, subindex: int = 0) -> int:
        raise TimeoutError("gateway read timeout")

    od.read_i8 = _boom  # type: ignore[assignment]
    pp = ProfilePosition(od, config=_fast_cfg(verify_mode=True))

    with pytest.raises(TimeoutError):
        await pp.ensure_mode()


async def test_move_to_pulses_new_setpoint_set_then_clear() -> None:
    od = _FakeOD(
        statuswords=[
            0,                   # barrier read before start
            0,                   # ack read: bit10 == 0 -> set-point acknowledged (moving)
            SW_TARGET_REACHED,   # wait_target_reached: bit10 set
        ]
    )
    od.read_i32_value = 12345    # actual position == target -> completion confirmed
    pp = ProfilePosition(od, config=_fast_cfg())

    await pp.move_to(target_position=12345, timeout_s=0.5)

    cw_writes = [w for w in od.writes_u16 if w[0] == int(ODIndex.CONTROLWORD)]
    assert len(cw_writes) >= 2

    set_word = cw_writes[-2][1]
    clear_word = cw_writes[-1][1]

    assert _bit(set_word, int(CWBit.NEW_SET_POINT))
    assert not _bit(clear_word, int(CWBit.NEW_SET_POINT))


async def test_move_to_holds_bit4_until_setpoint_acknowledged() -> None:
    """Closed-loop handshake: bit4 must stay HIGH until the ack is read.

    Manual §5.6.10.2 requires resetting bit4 only AFTER the drive acknowledges
    the set-point. So the acknowledge statusword read must occur between the
    bit4-set write and the bit4-clear write.
    """
    od = _FakeOD(
        statuswords=[
            0,                   # barrier read
            SW_TARGET_REACHED,   # ack read #1: still bit10=1, no bit12 -> not yet acked
            SW_SETPOINT_ACK,     # ack read #2: bit12 set -> acknowledged
            SW_TARGET_REACHED,   # wait_target_reached: bit10 set
        ]
    )
    od.read_i32_value = 999      # actual position == target -> completion confirmed
    pp = ProfilePosition(od, config=_fast_cfg())

    await pp.move_to(target_position=999)

    # Find the bit4-set and the following bit4-clear in the ordered event log.
    set_idx = next(
        i for i, e in enumerate(od.events)
        if e[0] == "write_cw" and _bit(e[1], int(CWBit.NEW_SET_POINT))
    )
    clear_idx = next(
        i for i, e in enumerate(od.events)
        if i > set_idx and e[0] == "write_cw" and not _bit(e[1], int(CWBit.NEW_SET_POINT))
    )
    reads_between = [
        i for i in range(set_idx + 1, clear_idx) if od.events[i][0] == "read_sw"
    ]
    assert reads_between, "ack must be polled while bit4 is held high (between set and clear)"


async def test_move_to_raises_when_setpoint_never_acknowledged() -> None:
    """Regression: stale bit10=1 with no bit12 must NOT report a false success.

    This is the exact failure mode from the field logs (target_reached latched
    from homing, set-point never applied). The old delta<=250 heuristic returned
    success here; the closed-loop handshake must raise instead.
    """
    od = _FakeOD(const_statusword=SW_TARGET_REACHED)  # bit10 stuck high, bit12 never set
    pp = ProfilePosition(od, config=_fast_cfg())

    with pytest.raises(TimeoutError):
        await pp.move_to(target_position=10)

    # bit4 must still have been released despite the failure.
    cw_writes = [w[1] for w in od.writes_u16 if w[0] == int(ODIndex.CONTROLWORD)]
    assert cw_writes, "controlword should have been written"
    assert not _bit(cw_writes[-1], int(CWBit.NEW_SET_POINT)), "bit4 must be released on ack timeout"


async def test_move_to_completes_when_position_at_target() -> None:
    """Ack via bit12 while bit10 stays set AND the axis is already at target.

    The drive acknowledges (bit12) with Target Reached still set; since the
    actual position already equals the target, the move completes with no wait
    and no false timeout.
    """
    od = _FakeOD(const_statusword=SW_SETPOINT_ACK | SW_TARGET_REACHED)
    od.read_i32_value = 42  # actual position already == target
    pp = ProfilePosition(od, config=_fast_cfg())

    await pp.move_to(target_position=42)

    cw_writes = [w[1] for w in od.writes_u16 if w[0] == int(ODIndex.CONTROLWORD)]
    assert not _bit(cw_writes[-1], int(CWBit.NEW_SET_POINT)), "bit4 must be released after ack"


async def test_move_to_does_not_falsely_complete_on_stale_bit10() -> None:
    """REGRESSION (field): bit12 ack with a STALE bit10 must not fake completion.

    Real dryve hardware sets bit12 (Set-point Acknowledge) while bit10 "Target
    Reached" is still 1 from the previous motion — even for a genuine move
    (statusword 0x1627). Completion must be confirmed against the actual
    position, not the bits. Here the fake position never reaches the target, so
    move_to must TIME OUT rather than report "reached" instantly.
    """
    od = _FakeOD(const_statusword=SW_SETPOINT_ACK | SW_TARGET_REACHED)  # 0x1627-like
    od.read_i32_value = 100003  # far from target 5000 (Vincent's X axis)
    pp = ProfilePosition(od, config=_fast_cfg(move_timeout_s=0.05))

    with pytest.raises(TimeoutError):
        await pp.move_to(target_position=5000)
