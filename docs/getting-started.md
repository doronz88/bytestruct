# Getting started

## A first record

A record is a dataclass that derives from `BinaryStruct`. Each field's annotation names its wire type,
and fields appear on the wire in declaration order:

```python
from dataclasses import dataclass

from bytestruct import BinaryStruct, u8, u16, u32


@dataclass
class Point(BinaryStruct, byte_order="<"):
    flags: u8
    x: u16
    y: u32


assert Point.SIZE == 7  # 1 + 2 + 4, no alignment padding
```

`u8`, `u16` and `u32` are `Annotated[int, ...]` aliases: to a type checker, `Point.x` is an `int`. The
`byte_order` class argument (`"<"` little-endian, the default, or `">"` big-endian) applies to every field
that does not pin its own.

## Build and parse

`build()` encodes an instance; `parse()` decodes one from the start of a buffer:

```python
point = Point(flags=1, x=2, y=3)
data = point.build()
assert data == b"\x01" + b"\x02\x00" + b"\x03\x00\x00\x00"

assert Point.parse(data) == point
assert Point.parse(data + b"more bytes") == point  # trailing bytes are ignored
assert Point.parse(bytearray(data)) == point  # bytes, bytearray and memoryview are all accepted
```

To parse several records out of one buffer, read them from a `ByteReader`, which keeps track of the
position:

```python
from bytestruct import ByteReader

reader = ByteReader(Point(1, 2, 3).build() + Point(4, 5, 6).build())
assert Point.read(reader) == Point(1, 2, 3)
assert Point.read(reader) == Point(4, 5, 6)
assert reader.remaining == 0
```

## Errors

Every failure is a `BinaryDataError`, which is a `ValueError`. Parsing raises a `BinaryParseError` (a
`TruncatedDataError` when the data is too short) and building raises a `BinaryBuildError`. The message
says which record, which field and which absolute offset:

```python
from bytestruct import BinaryBuildError, BinaryDataError, TruncatedDataError

try:
    Point.parse(b"\x01\x02\x00\x03")
except TruncatedDataError as e:
    assert str(e) == "Point.y at offset 3: expected 4 bytes, found 1"
    assert (e.record, e.field, e.offset) == ("Point", "y", 3)

try:
    Point(1, 2, 2**32).build()
except BinaryBuildError as e:
    assert e.record == "Point"

assert issubclass(BinaryDataError, ValueError)
```

## Next steps

- [Records](records.md) covers every field type, constants, padding and variable-length fields.
- [Codecs](codecs.md) covers the combinators for variable-length data.
- [ByteReader](byte-reader.md) covers parsing formats by hand.
- [Errors](errors.md) covers the exception tree and error locations.
- [Extending bytestruct](extending.md) shows how to write your own field types and codecs.
- The [Cookbook](cookbook.md) has complete formats, end to end.
- The [Type reference](reference.md) has a round-trip example for every public type.
