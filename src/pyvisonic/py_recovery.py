"""Bounded receive-only recovery for sustained panel status floods."""

from collections import deque
from enum import Enum, auto


class RecoveryState(Enum):
    """State of bounded panel flood recovery."""

    IDLE = auto()
    SILENT = auto()
    RESUMING = auto()
    FAILED = auto()


class Recovery:
    """Measure valid traffic using monotonic time, without transport side effects."""

    FLOOD_WINDOW = 30.0
    FLOOD_COUNT = 90
    MIN_SILENCE = 120.0
    MAX_SILENCE = 180.0
    SETTLED_WINDOW = 15.0
    SETTLED_COUNT = 15

    def __init__(self) -> None:
        self.state = RecoveryState.IDLE
        self.started_at: float | None = None
        self.eligible_since: float | None = None
        self.packets: deque[tuple[float, bool]] = deque(maxlen=2048)

    @property
    def silent(self) -> bool:
        """Keep writes blocked until the old protocol has been replaced or stopped."""
        return self.state != RecoveryState.IDLE

    def observe(self, packet: bytes | bytearray, now: float, eligible: bool) -> None:
        """Count validated packets without changing decoding or requiring ACKs."""
        if not eligible and not self.silent:
            self.eligible_since = None
            self.packets.clear()
            return
        if self.eligible_since is None:
            self.eligible_since = now
        target = packet[1] == 0xA5 or (
            packet[1] == 0xB0 and len(packet) > 3 and packet[3] in (0x24, 0x39)
        )
        self.packets.append((now, target))
        while self.packets and self.packets[0][0] < now - self.FLOOD_WINDOW:
            self.packets.popleft()

    def flooding(self, now: float) -> bool:
        """Require elevated repeated status traffic in each third of the window."""
        return (
            self.eligible_since is not None
            and now - self.eligible_since >= self.FLOOD_WINDOW
            and all(
                sum(
                    target
                    for timestamp, target in self.packets
                    if now - (part + 1) * 10 < timestamp <= now - part * 10
                )
                >= self.FLOOD_COUNT // 3
                for part in range(3)
            )
        )

    def start(self, now: float) -> bool:
        """Enter silence only after an observed sustained flood."""
        if self.silent or not self.flooding(now):
            return False
        self.state = RecoveryState.SILENT
        self.started_at = now
        return True

    def tick(self, now: float) -> RecoveryState:
        """Resolve the attempt once; the hard deadline takes precedence."""
        if self.state != RecoveryState.SILENT:
            return self.state
        assert self.started_at is not None
        elapsed = now - self.started_at
        if elapsed >= self.MAX_SILENCE:
            self.state = RecoveryState.FAILED
        elif (
            elapsed >= self.MIN_SILENCE
            and sum(
                timestamp >= now - self.SETTLED_WINDOW for timestamp, _ in self.packets
            )
            <= self.SETTLED_COUNT
        ):
            self.state = RecoveryState.RESUMING
        return self.state
