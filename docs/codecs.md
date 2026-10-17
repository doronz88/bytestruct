# Codecs

Records describe fixed layouts. Everything whose size depends on the data (length-prefixed strings,
counted lists, tagged unions) is described by **codecs**: small objects that compose like construct's.

A codec is anything with two methods:

- `read(reader)` parses one value at a [`ByteReader`](byte-reader.md)'s position and advances past it;
- `build(value)` encodes one value and returns `bytes`.

That is the `Codec` protocol. The built-in combinators derive from `CodecBase`, which adds a
`parse(data)` shortcut (parse from the start of `data`; trailing bytes are ignored).

```python
from bytestruct import (
    CSTRING,
    F32_BE,
    F64_LE,
    GREEDY_BYTES,
    GREEDY_STRING,
    I16_BE,
    I32_LE,
    I64_BE,
    PASS,
    U8,
    U16_BE,
    U16_LE,
    U32_BE,
    U32_LE,
    U64_LE,
    Aligned,
    BinaryBuildError,
    BinaryParseError,
    ByteReader,
    Bytes,
    CString,
    GreedyString,
    Prefixed,
    PrefixedArray,
    Scalar,
    Switch,
    Tuple,
)
```

## Scalars

A `Scalar` is a `struct.Struct` holding a single value, usable as a codec. `U8` is an unsigned byte; every
larger size comes in both byte orders, named `<kind><bits>_<LE|BE>`:

| | Little-endian | Big-endian |
| --- | --- | --- |
| unsigned 16/32/64-bit | `U16_LE`, `U32_LE`, `U64_LE` | `U16_BE`, `U32_BE`, `U64_BE` |
| signed 16/32/64-bit | `I16_LE`, `I32_LE`, `I64_LE` | `I16_BE`, `I32_BE`, `I64_BE` |
| IEEE 754 single/double | `F32_LE`, `F64_LE` | `F32_BE`, `F64_BE` |

```python
assert U32_LE.build(1) == b"\x01\x00\x00\x00"
assert U32_BE.build(1) == b"\x00\x00\x00\x01"
assert U16_BE.parse(b"\x12\x34") == U16_LE.parse(b"\x34\x12") == 0x1234
assert I16_BE.parse(I16_BE.build(-2)) == -2
assert I32_LE.parse(I32_LE.build(-1)) == -1
assert I64_BE.parse(I64_BE.build(-(2**40))) == -(2**40)
assert U64_LE.parse(U64_LE.build(2**63)) == 2**63
assert F32_BE.parse(F32_BE.build(1.5)) == 1.5
assert F64_LE.parse(F64_LE.build(0.5)) == 0.5
assert U8.build(255) == b"\xff"

try:
    U8.build(256)
except BinaryBuildError as e:
    assert str(e).startswith("cannot encode 256")
```

Any other single-value `struct` format is one line away:

```python
I8 = Scalar("b")
F16_LE = Scalar("<e")

assert I8.build(-1) == b"\xff"
assert F16_LE.parse(F16_LE.build(0.5)) == 0.5
```

Scalars are also what `Prefixed`, `PrefixedArray` and `Switch` take for their length, count and tag.

## Leaves

### `CSTRING` / `CString(errors="strict")`

A NUL-terminated UTF-8 string. Building a string with an embedded NUL is an error (the peer would stop
there). `CSTRING` is a strict instance; `CString(errors=...)` takes a `bytes.decode` error handler.

```python
assert CSTRING.build("hé") == b"h\xc3\xa9\0"
assert CSTRING.parse(b"h\xc3\xa9\0rest") == "hé"
assert CString(errors="replace").parse(b"\xc3\0") == "�"

try:
    CSTRING.build("a\0b")
except BinaryBuildError as e:
    assert str(e) == "embedded NUL in 'a\\x00b'"
```

### `GREEDY_BYTES` / `GreedyBytes()`

Every remaining byte: of the enclosing `Prefixed`, or of the buffer.

```python
assert GREEDY_BYTES.parse(b"abc") == b"abc"
assert GREEDY_BYTES.build(bytearray(b"abc")) == b"abc"
```

### `Bytes(n)`

The fixed-size record field type doubles as a codec: exactly `n` raw bytes.

```python
assert Bytes(2).read(ByteReader(b"abc")) == b"ab"
assert Bytes(2).build(b"ab") == b"ab"

try:
    Bytes(2).build(b"abc")
except BinaryBuildError as e:
    assert str(e) == "expected 2 bytes, got 3"
```

### `GREEDY_STRING` / `GreedyString(errors="strict")`

Every remaining byte, decoded as UTF-8. Wrapped in `Prefixed`, it is a length-prefixed string
(construct's `PascalString`). Invalid UTF-8 raises `BinaryParseError`, unless `GreedyString` is given
another `errors` handler.

```python
assert GREEDY_STRING.build("hé") == b"h\xc3\xa9"
assert GREEDY_STRING.parse(b"h\xc3\xa9") == "hé"

PASCAL_STRING = Prefixed(U16_BE, GREEDY_STRING)
assert PASCAL_STRING.build("hé") == b"\x00\x03h\xc3\xa9"
assert PASCAL_STRING.parse(b"\x00\x03h\xc3\xa9") == "hé"

try:
    PASCAL_STRING.parse(b"\x00\x01\xff")
except BinaryParseError as e:
    assert str(e).startswith("at offset 2: invalid UTF-8")

assert GreedyString(errors="replace").parse(b"\xff") == "\ufffd"
```

### `PASS` / `Pass()`

No bytes at all; parses to `None`. Useful as a `Switch` case for tags that carry no value.

```python
assert PASS.parse(b"anything") is None
assert PASS.build(None) == b""
```

## Combinators

### `Prefixed(length, inner)`

`inner`, confined to the byte count that the `length` scalar precedes it with. The inner codec cannot read
past the prefixed bytes, and any it leaves unread are skipped:

```python
BLOB = Prefixed(U32_LE, GREEDY_BYTES)
assert BLOB.build(b"abc") == b"\x03\x00\x00\x00abc"
assert BLOB.parse(b"\x03\x00\x00\x00abcdef") == b"abc"

NAME = Prefixed(U8, CSTRING)
reader = ByteReader(b"\x04a\0bc" + b"next")
assert NAME.read(reader) == "a"  # "bc" is skipped
assert reader.read_all() == b"next"

try:
    NAME.parse(b"\x02ab\0")  # the terminator is outside the prefixed bytes
except BinaryParseError as e:
    assert str(e) == "at offset 1: unterminated C string"

try:
    Prefixed(U8, GREEDY_BYTES).build(b"x" * 256)
except BinaryBuildError as e:
    assert str(e).startswith("cannot encode a length of 256")
```

### `Aligned(modulus, inner)`

`inner`, followed by NUL padding to a multiple of `modulus` bytes, counted from where the value starts:

```python
PADDED = Aligned(4, CSTRING)
assert PADDED.build("ab") == b"ab\0\0"
assert PADDED.build("abc") == b"abc\0"
assert PADDED.build("abcd") == b"abcd\0\0\0\0"

reader = ByteReader(b"a\0\0\0" + b"next")
assert PADDED.read(reader) == "a"
assert reader.read_all() == b"next"
```

Parse skips the padding without checking that it is zero.

### `PrefixedArray(count, element)`

An element count in the `count` scalar, followed by that many `element`s, parsed into a list:

```python
NAMES = PrefixedArray(U8, CSTRING)
assert NAMES.build(["a", "bc"]) == b"\x02a\0bc\0"
assert NAMES.parse(b"\x02a\0bc\0") == ["a", "bc"]
assert NAMES.parse(b"\x00") == []
```

### `Tuple(*codecs)`

Consecutive values of different codecs, parsed into a tuple:

```python
PAIR = Tuple(CSTRING, U8)
assert PAIR.build(("a", 1)) == b"a\0\x01"
assert PAIR.parse(b"a\0\x01") == ("a", 1)

try:
    PAIR.build(("a",))
except BinaryBuildError as e:
    assert str(e) == "expected 2 values, got 1"
```

### `Switch(tag, cases, *, default=None)`

A `tag` scalar, followed by a value in the codec that `cases` selects for it. It parses to `(tag, value)`
and builds from the same pair. A tag without a case uses `default`, or is an error when there is none:

```python
from enum import IntEnum


class Kind(IntEnum):
    TEXT = 1
    NUMBER = 2
    EMPTY = 3


VALUE = Switch(U8, {Kind.TEXT: CSTRING, Kind.NUMBER: U16_BE, Kind.EMPTY: PASS})
assert VALUE.build((Kind.TEXT, "a")) == b"\x01a\0"
assert VALUE.parse(b"\x02\x00\x05") == (Kind.NUMBER, 5)
assert VALUE.parse(b"\x03") == (Kind.EMPTY, None)

try:
    VALUE.parse(b"\x09")
except BinaryParseError as e:
    assert str(e) == "at offset 0: unknown tag 0x9"

LENIENT = Switch(U8, {1: U8}, default=GREEDY_BYTES)
assert LENIENT.parse(b"\x07rest") == (7, b"rest")
```

The parsed tag is the raw integer (equal to, but not an instance of, an `IntEnum` key). When the keys have
a `name` (enum members), error paths use it, as in `NUMBER at offset 1: ...`.

## Composing codecs

Codecs nest freely. Here is a small type-length-value dictionary:

```python
STRING = Aligned(4, Prefixed(U32_LE, CSTRING))  # u32 length, NUL-terminated UTF-8, padded to 4 bytes
OBJECT = Switch(U32_LE, {1: STRING, 2: U64_LE, 3: PASS})
DICTIONARY = PrefixedArray(U32_LE, Tuple(STRING, OBJECT))

entries = [("name", (1, "bytestruct")), ("size", (2, 1024)), ("flag", (3, None))]
data = DICTIONARY.build(entries)
assert DICTIONARY.parse(data) == entries
assert len(STRING.build("name")) == 12  # 4 (length) + 5 ("name\0") + 3 (padding)
```

## Records as codecs

A `BinaryStruct` class is a codec: `Record.read(reader)` parses one record and `Record.build(record)`
encodes one. So records go anywhere a codec does:

```python
from dataclasses import dataclass

from bytestruct import BinaryStruct, u16, u32


@dataclass
class Point(BinaryStruct, byte_order="<"):
    x: u16
    y: u32


POINTS = PrefixedArray(U8, Point)
assert POINTS.build([Point(1, 2)]) == b"\x01" + b"\x01\x00\x02\x00\x00\x00"
assert POINTS.parse(b"\x01" + b"\x01\x00\x02\x00\x00\x00") == [Point(1, 2)]

TAGGED = Switch(U8, {1: Point, 2: CSTRING})
assert TAGGED.parse(TAGGED.build((1, Point(3, 4)))) == (1, Point(3, 4))
```

The reverse also holds: a record field annotated with a codec (`name: Annotated[str, CSTRING]`) makes
the record variable-length. See [Records](records.md#variable-length-fields).

## Writing your own codec

Subclass `CodecBase[T]` (for the `parse` shortcut) or write any class with `read` and `build`. Read with
the [`ByteReader`](byte-reader.md) methods, which raise `TruncatedDataError` for you; raise
`BinaryParseError` / `BinaryBuildError` for anything else, with `offset=reader.absolute` so the error is
located.

An unsigned LEB128 varint (7 bits per byte, low groups first, the high bit set on all but the last byte),
which no fixed-size scalar can express:

```python
from bytestruct import CodecBase


class VarInt(CodecBase[int]):
    """An unsigned LEB128 integer."""

    def read(self, reader: ByteReader) -> int:
        start = reader.absolute
        result = shift = 0
        while True:
            byte = U8.read(reader)  # raises TruncatedDataError at the end of the data
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7
            if shift > 63:
                raise BinaryParseError("varint longer than 64 bits", offset=start)

    def build(self, value: int) -> bytes:
        if value < 0:
            raise BinaryBuildError(f"cannot encode {value}: negative")
        out = bytearray()
        while True:
            byte, value = value & 0x7F, value >> 7
            out.append(byte | (0x80 if value else 0))
            if not value:
                return bytes(out)


VARINT = VarInt()
assert VARINT.build(300) == b"\xac\x02"
assert VARINT.parse(b"\xac\x02") == 300
assert VARINT.parse(VARINT.build(2**63)) == 2**63

STRINGS = PrefixedArray(U8, Tuple(VARINT, CSTRING))  # custom codecs compose like the built-in ones
assert STRINGS.parse(STRINGS.build([(1, "a"), (300, "b")])) == [(1, "a"), (300, "b")]

try:
    VARINT.parse(b"\x80")
except BinaryParseError as e:
    assert str(e) == "at offset 1: expected 1 bytes, found 0"
```

A codec that contains other values should record which one failed, by calling `within(step)` on a
`BinaryDataError` as it propagates (see [Errors](errors.md#for-codec-authors)). An adapter that converts
another codec's value:

```python
from datetime import datetime, timezone

from bytestruct import BinaryDataError


class Timestamp(CodecBase[datetime]):
    """Seconds since the epoch, as a little-endian u64."""

    def read(self, reader: ByteReader) -> datetime:
        try:
            return datetime.fromtimestamp(U64_LE.read(reader), timezone.utc)
        except BinaryDataError as e:
            e.within("Timestamp")
            raise

    def build(self, value: datetime) -> bytes:
        return U64_LE.build(int(value.timestamp()))


WHEN = Timestamp()
moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
assert WHEN.parse(WHEN.build(moment)) == moment

try:
    WHEN.parse(b"\x00")
except BinaryParseError as e:
    assert str(e) == "Timestamp at offset 0: expected 8 bytes, found 1"
```
