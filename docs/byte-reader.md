# ByteReader

Some formats have no declarative shape: offset tables, fields whose presence depends on a version,
lengths computed from two other fields. `ByteReader` is the tool for parsing those by hand. It is also
what records and codecs read from, so hand-written code and declarative pieces mix freely.

## The model

A `ByteReader` is a sequential reader over the window `data[offset:end]` of an in-memory buffer:

- `data` is the buffer (a `bytes`; a `bytearray` or `memoryview` is copied to `bytes` once, when the
  reader is created);
- `offset` is the current position in `data`, and `end` is where the window stops;
- `base` is the absolute offset of `data[0]` in the outermost buffer, used only to report error offsets;
- `remaining` is `end - offset`, and `absolute` is `base + offset`.

Every `read*`/`unpack*` method returns exactly what was asked for, or raises `TruncatedDataError`.

```python
import struct

from bytestruct import U32_LE, BinaryParseError, ByteReader, TruncatedDataError

reader = ByteReader(b"\x01\x02\x03\x04abc\0rest", base=10)
assert reader.unpack(struct.Struct("<BB")) == (1, 2)  # all the values of a struct.Struct
assert reader.unpack_one(struct.Struct("<H")) == 0x0403  # the single value of a struct.Struct
assert (reader.offset, reader.absolute, reader.remaining) == (4, 14, 8)
assert reader.read_cstring() == b"abc"  # up to and past the NUL; the NUL is not returned
reader.skip(1)
assert reader.read(2) == b"es"
assert reader.read_all() == b"t"
assert reader.remaining == 0
```

The [scalars](codecs.md#scalars) are `struct.Struct`s, so `reader.unpack_one(U32_LE)` works too, as does
`U32_LE.read(reader)`.

## Errors and offsets

A read past `end` raises `TruncatedDataError`, located at the absolute offset where the read started. A
negative size (a length computed from wire data) is a `BinaryParseError`, and the position does not
move:

```python
reader = ByteReader(b"abc", offset=1, base=100)
try:
    reader.read(5)
except TruncatedDataError as e:
    assert str(e) == "at offset 101: expected 5 bytes, found 2"

try:
    reader.read(-1)
except BinaryParseError as e:
    assert str(e) == "at offset 101: invalid size -1"
assert reader.offset == 1
```

The starting `offset` is validated too, since it often comes from the wire:

```python
try:
    ByteReader(b"abc", offset=4)
except BinaryParseError as e:
    assert str(e) == "at offset 0: invalid offset 4 in 3 bytes"
```

## Nested ranges without copying

`sub(size)` consumes the next `size` bytes and returns a reader confined to them. The sub-reader shares
`data` with its parent (nothing is copied), so it keeps reporting absolute offsets, and it cannot read
past its window:

```python
reader = ByteReader(b"\x00\x00" + b"abcd" + b"tail", offset=2, base=1000)
body = reader.sub(4)
assert body.data is reader.data  # the same buffer
assert reader.offset == 6  # the parent moved past the window
assert (body.absolute, body.remaining) == (1002, 4)

assert body.read(2) == b"ab"
try:
    body.read(3)  # only 2 bytes are left in the window, though the buffer has more
except TruncatedDataError as e:
    assert str(e) == "at offset 1004: expected 3 bytes, found 2"
assert reader.read_all() == b"tail"
```

## Mixing records and codecs

Records and codecs read from a `ByteReader` and advance it, so a hand-written parser can hand off to them
at any point. Here is a container with a header, a table of `(offset, length)` entries and a blob area
that the entries point into:

```python
from dataclasses import dataclass
from typing import Annotated

from bytestruct import BinaryStruct, Bytes, Const, Prefixed, CSTRING, U8, u16, u32


@dataclass
class TableHeader(BinaryStruct, byte_order="<"):
    magic: Annotated[bytes, Bytes(4), Const(b"TBL1")]
    count: u16


@dataclass
class TableEntry(BinaryStruct, byte_order="<"):
    offset: u32
    length: u16


def parse_table(data: bytes) -> list[bytes]:
    reader = ByteReader(data)
    header = TableHeader.read(reader)
    entries = [TableEntry.read(reader) for _ in range(header.count)]
    blobs = []
    for entry in entries:
        blob = ByteReader(data, offset=entry.offset)  # random access; offsets stay absolute
        blobs.append(blob.read(entry.length))
    return blobs


data = (
    b"TBL1" + b"\x02\x00"
    + TableEntry(18, 3).build() + TableEntry(21, 2).build()
    + b"onetwo"[:3] + b"hi"
)
assert parse_table(data) == [b"one", b"hi"]
```

!!! note
    `magic` above is a `Const` without `field(default=..., init=False)`: it is an ordinary `__init__`
    parameter that must equal the constant on build. Parsing still checks it either way.

A hand-written step can also sit inside a declarative format, by wrapping it in a
[codec](codecs.md#writing-your-own-codec):

```python
from bytestruct import CodecBase


class VersionedName(CodecBase[tuple[int, str]]):
    """A version byte; version 2 and later add a NUL-terminated name."""

    def read(self, reader: ByteReader) -> tuple[int, str]:
        version = U8.read(reader)
        name = CSTRING.read(reader) if version >= 2 else ""
        return version, name

    def build(self, value: tuple[int, str]) -> bytes:
        version, name = value
        return U8.build(version) + (CSTRING.build(name) if version >= 2 else b"")


ITEM = Prefixed(U8, VersionedName())
assert ITEM.parse(ITEM.build((1, ""))) == (1, "")
assert ITEM.parse(ITEM.build((2, "ab"))) == (2, "ab")
assert ITEM.build((2, "ab")) == b"\x04\x02ab\0"
```
