# Extending bytestruct

bytestruct has two extension points, and a fallback:

- a **`Format` subclass** is a new fixed-size field type for records;
- a **codec** (anything with `read(reader)` and `build(value)`) is a new variable-length value, usable on
  its own, inside the combinators and as a record field;
- a **hand-written parser and builder** on `ByteReader` handles what neither can express.

All three raise the same located errors as the built-in types. Every example on this page runs in the
test suite.

```python
import struct
from dataclasses import dataclass, field
from enum import IntFlag
from typing import Annotated, Any, Union

from bytestruct import (
    CSTRING,
    GREEDY_BYTES,
    GREEDY_STRING,
    U8,
    U16_BE,
    U32_BE,
    U32_LE,
    Aligned,
    BinaryBuildError,
    BinaryDataError,
    BinaryParseError,
    BinaryStruct,
    ByteReader,
    Bytes,
    Codec,
    CodecBase,
    Const,
    Format,
    Prefixed,
    PrefixedArray,
    Switch,
    TruncatedDataError,
    Tuple,
    i32,
    u8,
    u16,
    u32,
)
```

## Custom fixed-size field types

A `Format` is a single-value `struct` code, plus an optional byte order that overrides the record's.
`u16` is `Annotated[int, Format("H")]`. To convert values, subclass it and override:

- `decode(value)`: the unpacked value (an `int`, `float` or `bytes`, per the code) to the field value.
  Raise `BinaryParseError` for bad input.
- `encode(value)`: the field value to the value to pack. Raise `BinaryBuildError` for a value that cannot
  be encoded.

Only overridden methods are called, so a format that overrides neither costs nothing beyond the `struct`
unpack. The record fills in the record name, field name and (for parse) the offset of any
`BinaryParseError`/`BinaryBuildError` you raise. Values that `struct` itself rejects after `encode`
(out of range, wrong type) become a `BinaryBuildError` too.

!!! warning "Raise bytestruct errors, not plain exceptions"
    Only `BinaryDataError` subclasses are located and wrapped. A `ValueError` or `UnicodeError` that
    escapes `decode`/`encode` propagates as is, without the record and field, so catch and convert them
    as the examples below do.

### An ASCII-octal number

cpio and tar headers store numbers as zero-padded ASCII octal digits. The wire value is `size` raw bytes
(code `"<size>s"`):

```python
class Octal(Format):
    """An ASCII-octal number in a fixed-size field, zero-padded on the left."""

    def __init__(self, size: int) -> None:
        super().__init__(f"{size}s")
        self.size = size

    def decode(self, value: bytes) -> int:
        try:
            return int(value, 8)
        except ValueError as e:
            raise BinaryParseError(f"invalid octal number {value!r}") from e

    def encode(self, value: int) -> bytes:
        if value < 0:
            raise BinaryBuildError(f"negative octal number {value}")
        digits = b"%o" % value
        if len(digits) > self.size:
            raise BinaryBuildError(f"{value:#o} does not fit {self.size} octal digits")
        return digits.rjust(self.size, b"0")


@dataclass
class CpioHeader(BinaryStruct):
    magic: Annotated[bytes, Bytes(6), Const(b"070707")] = field(default=b"070707", init=False)
    mode: Annotated[int, Octal(6)]
    nlink: Annotated[int, Octal(6)]


header = CpioHeader(0o100644, 1)
assert header.build() == b"070707" + b"100644" + b"000001"
assert CpioHeader.parse(b"070707100644000001") == header

try:
    CpioHeader.parse(b"070707" + b"10z644" + b"000001")
except BinaryParseError as e:
    assert str(e) == "CpioHeader.mode at offset 6: invalid octal number b'10z644'"

try:
    CpioHeader(0o10000000, 1).build()
except BinaryBuildError as e:
    assert str(e) == "CpioHeader.mode: 0o10000000 does not fit 6 octal digits"
```

### A fixed-point number

A signed 16.16 fixed-point number, as in TrueType's `Fixed`: an `i32` holding `value * 65536`. The code is
`"i"` without a byte order, so it follows the record's:

```python
class Fixed16_16(Format):
    """A signed 16.16 fixed-point number, parsed into a float."""

    def __init__(self) -> None:
        super().__init__("i")

    def decode(self, value: int) -> float:
        return value / 65536

    def encode(self, value: float) -> int:
        return round(value * 65536)  # struct rejects what does not fit an i32


fixed = Annotated[float, Fixed16_16()]


@dataclass
class FontHeader(BinaryStruct, byte_order=">"):
    version: fixed
    revision: fixed


font = FontHeader(1.0, 2.5)
assert font.build().hex(" ") == "00 01 00 00 00 02 80 00"
assert FontHeader.parse(bytes.fromhex("00 01 00 00 00 02 80 00")) == font

try:
    FontHeader(1.0, 40000.0).build()
except BinaryBuildError as e:
    assert e.record == "FontHeader"  # the struct.error, converted
```

### A bitfield

Two 4-bit values packed in one byte, like the version and header length of an IPv4 header:

```python
class Nibbles(Format):
    """One byte holding two 4-bit numbers, high nibble first, parsed into a tuple."""

    def __init__(self) -> None:
        super().__init__("B")

    def decode(self, value: int) -> tuple[int, int]:
        return value >> 4, value & 0x0F

    def encode(self, value: tuple[int, int]) -> int:
        high, low = value
        if not (0 <= high <= 0xF and 0 <= low <= 0xF):
            raise BinaryBuildError(f"nibbles out of range: {value}")
        return high << 4 | low


@dataclass
class VersionByte(BinaryStruct):
    version_ihl: Annotated[tuple[int, int], Nibbles()]


assert VersionByte((4, 5)).build().hex() == "45"
assert VersionByte.parse(bytes.fromhex("45")) == VersionByte((4, 5))

try:
    VersionByte((16, 0)).build()
except BinaryBuildError as e:
    assert str(e) == "VersionByte.version_ihl: nibbles out of range: (16, 0)"
```

### Flags

For named bits there is nothing to write: an `enum.IntFlag` is an int enum, so `Annotated[Flags, u8]`
parses into it, combinations included:

```python
class Permissions(IntFlag):
    READ = 1
    WRITE = 2
    EXECUTE = 4


@dataclass
class Entry(BinaryStruct):
    permissions: Annotated[Permissions, u8]


entry = Entry(Permissions.READ | Permissions.WRITE)
assert entry.build().hex() == "03"
parsed = Entry.parse(bytes.fromhex("03"))
assert parsed.permissions == Permissions.READ | Permissions.WRITE
assert Permissions.WRITE in parsed.permissions
```

## Custom codecs

A codec implements the `Codec` protocol: `read(reader)` parses one value at the reader's position and
leaves the reader after it; `build(value)` returns the value's bytes. Subclass `CodecBase[T]` to get a
`parse(data)` shortcut and a typed base, or write any class with the two methods.

Inside `read`, use the reader's methods and other codecs: they raise `TruncatedDataError` (located at the
absolute offset) when the data runs out, so a codec rarely checks lengths itself.

### A varint (LEB128)

7 bits per byte, low groups first, with the high bit set on every byte but the last:

```python
class VarInt(CodecBase[int]):
    """An unsigned LEB128 integer of up to 64 bits."""

    def read(self, reader: ByteReader) -> int:
        start = reader.absolute
        result = shift = 0
        while True:
            byte = U8.read(reader)
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7
            if shift > 63:
                raise BinaryParseError("varint longer than 64 bits", offset=start)

    def build(self, value: int) -> bytes:
        if not 0 <= value < 2**64:
            raise BinaryBuildError(f"cannot encode {value} as a 64-bit varint")
        out = bytearray()
        while True:
            byte, value = value & 0x7F, value >> 7
            out.append(byte | (0x80 if value else 0))
            if not value:
                return bytes(out)


VARINT = VarInt()
assert VARINT.build(1).hex() == "01"
assert VARINT.build(300).hex(" ") == "ac 02"
assert VARINT.parse(bytes.fromhex("ac 02")) == 300
assert VARINT.parse(VARINT.build(2**64 - 1)) == 2**64 - 1

try:
    VARINT.parse(bytes.fromhex("ff ff"))
except TruncatedDataError as e:
    assert str(e) == "at offset 2: expected 1 bytes, found 0"

try:
    VARINT.parse(b"\xff" * 10 + b"\x01")
except BinaryParseError as e:
    assert str(e) == "at offset 0: varint longer than 64 bits"
```

### A length-prefixed list of records

A count followed by records is `PrefixedArray(count, Record)`. A list prefixed by its **byte length**
instead is `Prefixed` around a codec that reads records until the window ends:

```python
@dataclass
class Point(BinaryStruct, byte_order=">"):
    x: i32
    y: i32


class Until(CodecBase[list[Any]]):
    """Elements of ``element`` until the data ends (in a Prefixed, until its byte count is used up)."""

    def __init__(self, element: Codec[Any]) -> None:
        self.element = element

    def read(self, reader: ByteReader) -> list[Any]:
        items: list[Any] = []
        try:
            while reader.remaining:
                items.append(self.element.read(reader))
        except BinaryDataError as e:
            e.within(f"[{len(items)}]")
            raise
        return items

    def build(self, value: list[Any]) -> bytes:
        return b"".join(self.element.build(item) for item in value)


POINTS = Prefixed(U16_BE, Until(Point))
points = [Point(1, -1), Point(2, 3)]
assert POINTS.build(points).hex(" ") == "00 10 00 00 00 01 ff ff ff ff 00 00 00 02 00 00 00 03"
assert POINTS.parse(POINTS.build(points)) == points

try:
    POINTS.parse(bytes.fromhex("00 0c 00 00 00 01 ff ff ff ff 00 00 00 02"))
except TruncatedDataError as e:
    assert str(e) == "[1] -> Point.y at offset 14: expected 4 bytes, found 0"
```

### A TLV codec

Type-length-value: a type byte, a `u16` length, then that many bytes interpreted by the type. `Switch`
picks the codec from the type; `Prefixed` confines it to the length. Wrapping both in a class gives the
format a name and a place for its own rules:

```python
class Tlv(CodecBase[tuple[int, Any]]):
    """A u8 type, a big-endian u16 length and a value; unknown types parse to raw bytes."""

    def __init__(self, types: dict[int, Codec[Any]]) -> None:
        self.switch = Switch(U8, {tag: Prefixed(U16_BE, codec) for tag, codec in types.items()},
                             default=Prefixed(U16_BE, GREEDY_BYTES))

    def read(self, reader: ByteReader) -> tuple[int, Any]:
        return self.switch.read(reader)

    def build(self, value: tuple[int, Any]) -> bytes:
        return self.switch.build(value)


TLV = Tlv({1: GREEDY_STRING, 2: U32_BE})
assert TLV.build((1, "hi")).hex(" ") == "01 00 02 68 69"
assert TLV.build((2, 7)).hex(" ") == "02 00 04 00 00 00 07"
assert TLV.parse(bytes.fromhex("02 00 04 00 00 00 07")) == (2, 7)
assert TLV.parse(bytes.fromhex("09 00 01 ff")) == (9, b"\xff")  # unknown type
```

## Composing custom codecs

Custom codecs go anywhere a built-in one does: inside combinators, and as record fields (after the
fixed-size fields). A record whose body is a varint-counted list of TLVs:

```python
@dataclass
class Message(BinaryStruct, byte_order=">"):
    magic: Annotated[u16, Const(0x4D53)] = field(default=0x4D53, init=False)
    flags: u8
    sequence: Annotated[int, VARINT]
    options: Annotated[list[tuple[int, Any]], Until(TLV)]


message = Message(0, 300, [(1, "hi"), (2, 7)])
data = message.build()
assert data.hex(" ") == "4d 53 00 ac 02 01 00 02 68 69 02 00 04 00 00 00 07"
assert Message.parse(data) == message

FRAMES = PrefixedArray(U8, Prefixed(U32_LE, Message))  # and records nest in combinators again
assert FRAMES.parse(FRAMES.build([message, message])) == [message, message]
```

!!! note "Lengths, counts and tags are scalars"
    `Prefixed`, `PrefixedArray` and `Switch` read their length, count or tag with a `Scalar`
    (`reader.unpack_one`), so a custom codec such as `VarInt` cannot take that role. A varint-prefixed
    list is a few lines of its own codec, as in `read_value` below.

`Until` reads to the end of the data here, because `Message.parse` gives it the rest of the buffer; inside
`FRAMES`, each message's `Prefixed` window stops it.

## Located errors from your own code

Errors read like `path -> Record.field at offset N: message`. Three tools put the location there:

**`offset=`** when you raise. Capture `reader.absolute` before reading, so the offset is absolute in the
outermost buffer even inside a `sub()` reader or a `Prefixed` window (see `VarInt` above).

**`within(step)`** as an error propagates through a codec that contains other values, to add a step to
its `path` (outermost first: each enclosing codec inserts its step at the front). `Until` above does it
with the element index; records do it for codec fields (`Message.options`), `Switch` with the case name
and `PrefixedArray` with `[i]`:

```python
bad = bytes.fromhex("4d 53 00 01" + "01 00 05 68 69")  # the TLV says 5 bytes, 2 follow
try:
    Message.parse(bad)
except TruncatedDataError as e:
    assert e.path == ["Message.options", "[0]", "0x1"]
    assert str(e) == "Message.options -> [0] -> 0x1 at offset 7: expected 5 bytes, found 2"
```

**`located(record=..., field=..., offset=...)`** returns a copy with missing location parts filled in.
Records call it on what `Format.decode`/`encode` raise; use it when your code catches an error from a
lower layer that lacks context, such as a hand-written parser that knows which field it was reading:

```python
def read_named_u32(reader: ByteReader, record: str, name: str) -> int:
    start = reader.absolute
    try:
        return U32_BE.read(reader)
    except BinaryDataError as e:
        raise e.located(record=record, field=name, offset=start) from e


try:
    read_named_u32(ByteReader(b"\0\0\0" + b"\x01\x02", offset=3), "Footer", "crc")
except TruncatedDataError as e:
    assert str(e) == "Footer.crc at offset 3: expected 4 bytes, found 2"
```

`located` keeps any location the error already had, and the copy has the same type and `path`.

## Hand-written parsers and builders

Some formats have no declarative shape at all, such as recursive ones. Write a pair of functions: a
parser on `ByteReader` and a builder that returns `bytes`, and wrap them in a codec so they compose.

Here is a small self-describing value format (like a tiny MessagePack): a type byte, then

- `0x00`: none; `0x01`: false; `0x02`: true;
- `0x10`: a big-endian `i32`;
- `0x20`: a UTF-8 string with a varint length;
- `0x30`: a list: a varint count, then that many values;
- `0x40`: a map: a varint count, then that many string-key/value pairs.

```python
Value = Union[None, bool, int, str, list[Any], dict[str, Any]]
I32 = struct.Struct(">i")
STRING = Prefixed(U8, GREEDY_STRING)  # map keys: short strings


def read_value(reader: ByteReader, depth: int = 0) -> Value:
    if depth > 32:  # untrusted input must not exhaust the stack
        raise BinaryParseError("nested too deeply", offset=reader.absolute)
    start = reader.absolute
    kind = U8.read(reader)
    if kind in (0x00, 0x01, 0x02):
        return (None, False, True)[kind]
    if kind == 0x10:
        return reader.unpack_one(I32)
    if kind == 0x20:
        raw = reader.read(VARINT.read(reader))
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise BinaryParseError(f"invalid UTF-8: {e}", offset=start) from e
    if kind == 0x30:
        items = []
        for index in range(VARINT.read(reader)):
            try:
                items.append(read_value(reader, depth + 1))
            except BinaryDataError as e:
                e.within(f"[{index}]")
                raise
        return items
    if kind == 0x40:
        result = {}
        for _ in range(VARINT.read(reader)):
            key = STRING.read(reader)
            try:
                result[key] = read_value(reader, depth + 1)
            except BinaryDataError as e:
                e.within(repr(key))
                raise
        return result
    raise BinaryParseError(f"unknown value type {kind:#x}", offset=start)


def build_value(value: Value) -> bytes:
    if value is None or isinstance(value, bool):
        return bytes([{None: 0x00, False: 0x01, True: 0x02}[value]])
    if isinstance(value, int):
        try:
            return b"\x10" + I32.pack(value)
        except struct.error as e:
            raise BinaryBuildError(f"cannot encode {value}: {e}") from e
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"\x20" + VARINT.build(len(encoded)) + encoded
    if isinstance(value, list):
        return b"\x30" + VARINT.build(len(value)) + b"".join(build_value(item) for item in value)
    if isinstance(value, dict):
        parts = [STRING.build(key) + build_value(item) for key, item in value.items()]
        return b"\x40" + VARINT.build(len(value)) + b"".join(parts)
    raise BinaryBuildError(f"cannot encode a {type(value).__name__}")


class AnyValue(CodecBase[Value]):
    """The value format as a codec, so it composes with the rest of bytestruct."""

    def read(self, reader: ByteReader) -> Value:
        return read_value(reader)

    def build(self, value: Value) -> bytes:
        return build_value(value)


VALUE = AnyValue()
document = {"name": "bytestruct", "tags": ["fast", "typed"], "stars": 42, "fork": False, "parent": None}
data = VALUE.build(document)
assert VALUE.parse(data) == document
assert VALUE.build([1, "a"]).hex(" ") == "30 02 10 00 00 00 01 20 01 61"
assert VALUE.parse(bytes.fromhex("30 02 10 00 00 00 01 20 01 61")) == [1, "a"]

try:
    VALUE.parse(bytes.fromhex("40 01 04 74 61 67 73 30 02 02 77"))
except BinaryParseError as e:
    assert str(e) == "'tags' -> [1] at offset 10: unknown value type 0x77"

@dataclass
class Document(BinaryStruct, byte_order=">"):
    version: u8
    body: Annotated[Value, VALUE]


assert Document.parse(Document(1, {"a": [True]}).build()) == Document(1, {"a": [True]})
```

## Testing your extensions

Two kinds of tests catch almost every codec bug: round trips over representative values (including the
edges: empty, zero, maximum, one past a length boundary), and error tests that pin the exception type and
message. With pytest:

```python
import pytest


@pytest.mark.parametrize("value", [0, 1, 127, 128, 300, 2**32, 2**64 - 1])
def test_varint_round_trip(value: int) -> None:
    data = VARINT.build(value)
    reader = ByteReader(data + b"next")
    assert VARINT.read(reader) == value
    assert reader.read_all() == b"next"  # consumed exactly its own bytes


@pytest.mark.parametrize(("value", "hex_"), [(0, "00"), (127, "7f"), (128, "80 01"), (300, "ac 02")])
def test_varint_wire_format(value: int, hex_: str) -> None:
    assert VARINT.build(value).hex(" ") == hex_  # pin the bytes, not just the round trip


def test_varint_errors() -> None:
    with pytest.raises(TruncatedDataError, match="at offset 1: expected 1 bytes, found 0"):
        VARINT.parse(b"\x80")
    with pytest.raises(BinaryBuildError):
        VARINT.build(-1)


def test_octal_field() -> None:
    assert CpioHeader.parse(CpioHeader(0o755, 2).build()) == CpioHeader(0o755, 2)
    with pytest.raises(BinaryParseError) as error:
        CpioHeader.parse(b"070707" + b"zzzzzz" + b"000001")
    assert (error.value.record, error.value.field, error.value.offset) == ("CpioHeader", "mode", 6)


def test_value_round_trip() -> None:
    for value in [None, True, -(2**31), "", "é", [], {}, {"a": [1, {"b": None}]}]:
        assert VALUE.parse(VALUE.build(value)) == value
    with pytest.raises(BinaryParseError, match="nested too deeply"):
        VALUE.parse(b"\x30\x01" * 40 + b"\x00")
```

The functions above are ordinary pytest tests; this page's test runner calls them directly too:

```python
for value in [0, 1, 127, 128, 300, 2**32, 2**64 - 1]:
    test_varint_round_trip(value)
for value, hex_ in [(0, "00"), (127, "7f"), (128, "80 01"), (300, "ac 02")]:
    test_varint_wire_format(value, hex_)
test_varint_errors()
test_octal_field()
test_value_round_trip()
```

Testing through `read` on a `ByteReader` with trailing bytes, as `test_varint_round_trip` does, catches a
codec that reads too much or too little, which a `parse` round trip alone would miss.
