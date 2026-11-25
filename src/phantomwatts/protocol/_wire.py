"""Little-endian field packing shared by the characteristic codecs."""


class Reader:
    """Cursor over a characteristic value that fails on short or over-long input."""

    def __init__(self, data: bytes) -> None:
        self._data = bytes(data)
        self._position = 0

    @property
    def remaining(self) -> int:
        return len(self._data) - self._position

    def uint(self, size: int) -> int:
        return int.from_bytes(self._take(size), "little")

    def sint(self, size: int) -> int:
        return int.from_bytes(self._take(size), "little", signed=True)

    def rest(self) -> bytes:
        return self._take(self.remaining)

    def finish(self) -> None:
        if self.remaining:
            raise ValueError(f"{self.remaining} unexpected trailing byte(s)")

    def _take(self, size: int) -> bytes:
        if size > self.remaining:
            raise ValueError(f"value is truncated: need {size} byte(s), have {self.remaining}")
        chunk = self._data[self._position : self._position + size]
        self._position += size
        return chunk


def uint(value: int, size: int, field: str) -> bytes:
    return _pack(value, size, signed=False, field=field)


def sint(value: int, size: int, field: str) -> bytes:
    return _pack(value, size, signed=True, field=field)


def to_wire(value: float, per_unit: int) -> int:
    """Convert a value in natural units to the integer the wire carries."""
    return round(value * per_unit)


def _pack(value: int, size: int, *, signed: bool, field: str) -> bytes:
    try:
        return value.to_bytes(size, "little", signed=signed)
    except OverflowError:
        kind = "signed" if signed else "unsigned"
        raise ValueError(f"{field} {value} does not fit in {size} {kind} byte(s)") from None
