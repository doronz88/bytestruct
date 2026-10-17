# Type reference

Every public field type, scalar and codec, each with a round trip: the bytes it builds (in hex) and the
value it parses back. Every snippet on this page runs in the test suite, which also checks that each name
in `bytestruct.__all__` is used here.

```python
import ipaddress
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Annotated

from bytestruct import (
    CSTRING,
    F32_BE,
    F32_LE,
    F64_BE,
    F64_LE,
    GREEDY_BYTES,
    GREEDY_STRING,
    I16_BE,
    I16_LE,
    I32_BE,
    I32_LE,
    I64_BE,
    I64_LE,
    PASS,
    U16_BE,
    U16_LE,
    U32_BE,
    U32_LE,
    U64_BE,
    U64_LE,
    U8,
    Aligned,
    Array,
    BinaryBuildError,
    BinaryDataError,
    BinaryParseError,
    BinaryStruct,
    ByteReader,
    Bytes,
    BytesLike,
    CString,
    Codec,
    CodecBase,
    Const,
    Format,
    GreedyBytes,
    GreedyString,
    OpenIntEnum,
    PaddedString,
    Pass,
    Prefixed,
    PrefixedArray,
    Scalar,
    Switch,
    TruncatedDataError,
    Tuple,
    UnexpectedConstantError,
    f32,
    f32be,
    f32le,
    f64,
    f64be,
    f64le,
    i16,
    i16be,
    i16le,
    i32,
    i32be,
    i32le,
    i64,
    i64be,
    i64le,
    i8,
    ipv4,
    ipv6,
    u16,
    u16be,
    u16le,
    u32,
    u32be,
    u32le,
    u64,
    u64be,
    u64le,
    u8,
    uuid_t,
)
```

## Field types

Field types go in record annotations. Each example is a one-field record; `byte_order` is the record's.

### `u8`

```python
@dataclass
class U8Field(BinaryStruct):
    value: u8


assert U8Field(0xAB).build().hex(" ") == "ab"
assert U8Field.parse(bytes.fromhex("ab")) == U8Field(0xAB)
```

### `u16`

```python
@dataclass
class U16Field(BinaryStruct, byte_order="<"):
    value: u16


assert U16Field(0x1234).build().hex(" ") == "34 12"
assert U16Field.parse(bytes.fromhex("34 12")) == U16Field(0x1234)
```

### `u32`

```python
@dataclass
class U32Field(BinaryStruct, byte_order=">"):
    value: u32


assert U32Field(0x12345678).build().hex(" ") == "12 34 56 78"
assert U32Field.parse(bytes.fromhex("12 34 56 78")) == U32Field(0x12345678)
```

### `u64`

```python
@dataclass
class U64Field(BinaryStruct, byte_order="<"):
    value: u64


assert U64Field(0x0102030405060708).build().hex(" ") == "08 07 06 05 04 03 02 01"
assert U64Field.parse(bytes.fromhex("08 07 06 05 04 03 02 01")) == U64Field(0x0102030405060708)
```

### `i16`

```python
@dataclass
class I16Field(BinaryStruct, byte_order="<"):
    value: i16


assert I16Field(-2).build().hex(" ") == "fe ff"
assert I16Field.parse(bytes.fromhex("fe ff")) == I16Field(-2)
```

### `i32`

```python
@dataclass
class I32Field(BinaryStruct, byte_order=">"):
    value: i32


assert I32Field(-2).build().hex(" ") == "ff ff ff fe"
assert I32Field.parse(bytes.fromhex("ff ff ff fe")) == I32Field(-2)
```

### `i64`

```python
@dataclass
class I64Field(BinaryStruct, byte_order="<"):
    value: i64


assert I64Field(-1).build().hex(" ") == "ff ff ff ff ff ff ff ff"
assert I64Field.parse(bytes.fromhex("ff ff ff ff ff ff ff ff")) == I64Field(-1)
```

### Pinned aliases: `u16le`/`u16be` ... `f64le`/`f64be`

Every integer and float alias from 16 bits up has a `le` and a `be` variant pinned to that byte order, whatever the
record's (`u16le`, `u16be`, `u32le`, `u32be`, `u64le`, `u64be`, `i16le` ... `i64be`, `f32le` ... `f64be`):

```python
@dataclass
class PinnedFields(BinaryStruct, byte_order=">"):
    little: u32le
    big: u32be
    small: u16le
    wide: u64be
    signed: i16le
    signed_big: i32be
    signed_wide: i64le
    signed_wide_big: i64be
    half: u16be
    long: u64le
    sig16: i16be
    sig32: i32le
    flt: f32le
    flt_big: f32be
    dbl: f64le
    dbl_big: f64be


pinned = PinnedFields(1, 1, 1, 1, -1, -1, -1, -1, 1, 1, -1, -1, 1.5, 1.5, 1.5, 1.5)
data = pinned.build()
assert data[:8].hex(" ") == "01 00 00 00 00 00 00 01"
assert PinnedFields.parse(data) == pinned
```

### `i8`, `f32` and `f64`

A signed byte, and IEEE 754 single- and double-precision floats in the record's byte order:

```python
@dataclass
class NumericFields(BinaryStruct, byte_order=">"):
    tiny: i8
    single: f32
    double: f64


numbers = NumericFields(-2, 1.5, -0.25)
assert numbers.build().hex(" ") == "fe 3f c0 00 00 bf d0 00 00 00 00 00 00"
assert NumericFields.parse(bytes.fromhex("fe 3f c0 00 00 bf d0 00 00 00 00 00 00")) == numbers
```

### `uuid_t`

```python
@dataclass
class UuidField(BinaryStruct):
    value: uuid_t


ID = uuid.UUID("00112233-4455-6677-8899-aabbccddeeff")
assert UuidField(ID).build().hex() == "00112233445566778899aabbccddeeff"
assert UuidField.parse(bytes.fromhex("00112233445566778899aabbccddeeff")) == UuidField(ID)
```

### `ipv4` and `ipv6`

```python
@dataclass
class Addresses(BinaryStruct):
    v4: ipv4
    v6: ipv6


addresses = Addresses(ipaddress.IPv4Address("192.168.1.2"), ipaddress.IPv6Address("fe80::1"))
assert addresses.build().hex(" ") == "c0 a8 01 02 " + "fe 80" + " 00" * 13 + " 01"
assert Addresses.parse(addresses.build()) == addresses
```

### `Bytes(n)`

```python
@dataclass
class BytesField(BinaryStruct):
    value: Annotated[bytes, Bytes(3)]


assert BytesField(b"abc").build().hex(" ") == "61 62 63"
assert BytesField.parse(bytes.fromhex("61 62 63")) == BytesField(b"abc")
```

### `PaddedString(n, errors="strict")`

```python
@dataclass
class StringField(BinaryStruct):
    value: Annotated[str, PaddedString(6)]


assert StringField("héé").build().hex(" ") == "68 c3 a9 c3 a9 00"
assert StringField.parse(bytes.fromhex("68 69 00 ff ff ff")) == StringField("hi")
assert StringField("abcdef").build().hex(" ") == "61 62 63 64 65 66"  # fills the field: no NUL
assert StringField.parse(bytes.fromhex("61 62 63 64 65 66")) == StringField("abcdef")


@dataclass
class LenientStringField(BinaryStruct):
    value: Annotated[str, PaddedString(2, errors="replace")]


assert LenientStringField.parse(bytes.fromhex("c3 00")) == LenientStringField("�")
```

### `Array(element, count)`

```python
@dataclass
class ArrayField(BinaryStruct, byte_order=">"):
    big: Annotated[list[int], Array(u16, 2)]  # the record's byte order
    little: Annotated[list[int], Array(u32le, 2)]  # pinned


arrays = ArrayField([1, 2], [3, 4])
assert arrays.build().hex(" ") == "00 01 00 02 03 00 00 00 04 00 00 00"
assert ArrayField.parse(bytes.fromhex("00 01 00 02 03 00 00 00 04 00 00 00")) == arrays
```

### Int-enum fields

`Annotated[SomeIntEnum, <integer alias>]`:

```python
class Color(IntEnum):
    RED = 1
    GREEN = 2


@dataclass
class EnumField(BinaryStruct, byte_order=">"):
    value: Annotated[Color, u16]


assert EnumField(Color.GREEN).build().hex(" ") == "00 02"
assert EnumField.parse(bytes.fromhex("00 02")).value is Color.GREEN
```

### `OpenIntEnum`

An `IntEnum` base that parses unknown values to pseudo-members instead of raising:

```python
class Level(OpenIntEnum):
    INFO = 1


@dataclass
class LevelField(BinaryStruct):
    value: Annotated[Level, u8]


assert LevelField.parse(bytes.fromhex("01")).value is Level.INFO
unknown = LevelField.parse(bytes.fromhex("63")).value
assert unknown == 99 and isinstance(unknown, Level) and unknown.name == "99"
assert LevelField(unknown).build().hex() == "63"
```

### `Const(value)`

```python
@dataclass
class ConstFields(BinaryStruct, byte_order="<"):
    magic: Annotated[u16, Const(0xBEEF)] = field(default=0xBEEF, init=False)
    tag: Annotated[bytes, Bytes(2), Const(b"OK")] = field(default=b"OK", init=False)
    value: u8


assert ConstFields(7).build().hex(" ") == "ef be 4f 4b 07"
assert ConstFields.parse(bytes.fromhex("ef be 4f 4b 07")) == ConstFields(7)

try:
    ConstFields.parse(bytes.fromhex("00 00 4f 4b 07"))
except UnexpectedConstantError as e:
    assert str(e) == "ConstFields.magic at offset 0: expected 0xbeef, got 0x0"
```

### `Format(code, byte_order=None)`

The base of every fixed-size field type: a single-value `struct` code, optionally pinned to a byte order.
See [Extending bytestruct](extending.md#custom-fixed-size-field-types) for subclasses.

```python
i8 = Annotated[int, Format("b")]
f32 = Annotated[float, Format("f")]
u16be = Annotated[int, Format("H", ">")]


@dataclass
class FormatFields(BinaryStruct, byte_order="<"):
    small: i8
    ratio: f32
    port: u16be


fields = FormatFields(-1, 1.5, 443)
assert fields.build().hex(" ") == "ff 00 00 c0 3f 01 bb"
assert FormatFields.parse(bytes.fromhex("ff 00 00 c0 3f 01 bb")) == fields
```

### `BinaryStruct`

The record base class, and a codec itself (see below). `SIZE` is the size of its fixed-size fields.

```python
@dataclass
class Pair(BinaryStruct, byte_order=">"):
    a: u8
    b: u16


assert Pair.SIZE == 3
assert Pair(1, 2).build().hex(" ") == "01 00 02"
assert Pair.parse(bytes.fromhex("01 00 02")) == Pair(1, 2)
assert Pair.read(ByteReader(bytes.fromhex("01 00 02"))) == Pair(1, 2)
```

## Scalars

`Scalar` is a `struct.Struct` of a single value, used as a codec and as the length, count and tag of
`Prefixed`, `PrefixedArray` and `Switch`.

### `U8`

```python
assert U8.build(0xAB).hex() == "ab"
assert U8.parse(bytes.fromhex("ab")) == 0xAB
```

### `U16_LE`, `U16_BE`

```python
assert U16_LE.build(0x1234).hex(" ") == "34 12"
assert U16_LE.parse(bytes.fromhex("34 12")) == 0x1234
assert U16_BE.build(0x1234).hex(" ") == "12 34"
assert U16_BE.parse(bytes.fromhex("12 34")) == 0x1234
```

### `U32_LE`, `U32_BE`

```python
assert U32_LE.build(0x12345678).hex(" ") == "78 56 34 12"
assert U32_LE.parse(bytes.fromhex("78 56 34 12")) == 0x12345678
assert U32_BE.build(0x12345678).hex(" ") == "12 34 56 78"
assert U32_BE.parse(bytes.fromhex("12 34 56 78")) == 0x12345678
```

### `U64_LE`, `U64_BE`

```python
assert U64_LE.build(0x0102030405060708).hex(" ") == "08 07 06 05 04 03 02 01"
assert U64_LE.parse(bytes.fromhex("08 07 06 05 04 03 02 01")) == 0x0102030405060708
assert U64_BE.build(0x0102030405060708).hex(" ") == "01 02 03 04 05 06 07 08"
assert U64_BE.parse(bytes.fromhex("01 02 03 04 05 06 07 08")) == 0x0102030405060708
```

### `I16_LE`, `I16_BE`

```python
assert I16_LE.build(-2).hex(" ") == "fe ff"
assert I16_LE.parse(bytes.fromhex("fe ff")) == -2
assert I16_BE.build(-2).hex(" ") == "ff fe"
assert I16_BE.parse(bytes.fromhex("ff fe")) == -2
```

### `I32_LE`, `I32_BE`

```python
assert I32_LE.build(-2).hex(" ") == "fe ff ff ff"
assert I32_LE.parse(bytes.fromhex("fe ff ff ff")) == -2
assert I32_BE.build(-2).hex(" ") == "ff ff ff fe"
assert I32_BE.parse(bytes.fromhex("ff ff ff fe")) == -2
```

### `I64_LE`, `I64_BE`

```python
assert I64_LE.build(-2).hex(" ") == "fe ff ff ff ff ff ff ff"
assert I64_LE.parse(bytes.fromhex("fe ff ff ff ff ff ff ff")) == -2
assert I64_BE.build(-2).hex(" ") == "ff ff ff ff ff ff ff fe"
assert I64_BE.parse(bytes.fromhex("ff ff ff ff ff ff ff fe")) == -2
```

### `F32_LE`, `F32_BE`

```python
assert F32_LE.build(1.5).hex(" ") == "00 00 c0 3f"
assert F32_LE.parse(bytes.fromhex("00 00 c0 3f")) == 1.5
assert F32_BE.build(1.5).hex(" ") == "3f c0 00 00"
assert F32_BE.parse(bytes.fromhex("3f c0 00 00")) == 1.5
```

### `F64_LE`, `F64_BE`

```python
assert F64_LE.build(1.5).hex(" ") == "00 00 00 00 00 00 f8 3f"
assert F64_LE.parse(bytes.fromhex("00 00 00 00 00 00 f8 3f")) == 1.5
assert F64_BE.build(1.5).hex(" ") == "3f f8 00 00 00 00 00 00"
assert F64_BE.parse(bytes.fromhex("3f f8 00 00 00 00 00 00")) == 1.5
```

### `Scalar(format)`

Any other single-value `struct` format:

```python
I8 = Scalar("b")
assert I8.build(-1).hex() == "ff"
assert I8.parse(bytes.fromhex("ff")) == -1
assert I8.read(ByteReader(bytes.fromhex("ff"))) == -1
```

## Leaf codecs

### `CSTRING`, `CString(errors="strict")`

```python
assert CSTRING.build("hé").hex(" ") == "68 c3 a9 00"
assert CSTRING.parse(bytes.fromhex("68 c3 a9 00 ff")) == "hé"
assert CString(errors="replace").parse(bytes.fromhex("c3 00")) == "�"
assert CString().build("a").hex(" ") == "61 00"
```

### `GREEDY_BYTES`, `GreedyBytes()`

```python
assert GREEDY_BYTES.build(b"ab").hex(" ") == "61 62"
assert GREEDY_BYTES.parse(bytes.fromhex("61 62")) == b"ab"
assert GreedyBytes().parse(b"") == b""
```

### `GREEDY_STRING`, `GreedyString(errors="strict")`

```python
assert GREEDY_STRING.build("hé").hex(" ") == "68 c3 a9"
assert GREEDY_STRING.parse(bytes.fromhex("68 c3 a9")) == "hé"
assert GreedyString(errors="replace").parse(bytes.fromhex("ff")) == "�"
```

### `PASS`, `Pass()`

```python
assert PASS.build(None).hex() == ""
assert PASS.parse(bytes.fromhex("ff")) is None
assert Pass().build(None) == b""
```

### `Bytes(n)` as a codec

```python
assert Bytes(2).build(b"ab").hex(" ") == "61 62"
assert Bytes(2).read(ByteReader(bytes.fromhex("61 62 63"))) == b"ab"
```

## Combinators

### `Prefixed(length, inner)`

```python
NAME = Prefixed(U16_BE, GREEDY_STRING)
assert NAME.build("abc").hex(" ") == "00 03 61 62 63"
assert NAME.parse(bytes.fromhex("00 03 61 62 63 ff")) == "abc"
```

### `Aligned(modulus, inner)`

```python
WORD_ALIGNED = Aligned(4, CSTRING)
assert WORD_ALIGNED.build("ab").hex(" ") == "61 62 00 00"
assert WORD_ALIGNED.build("abcd").hex(" ") == "61 62 63 64 00 00 00 00"
assert WORD_ALIGNED.parse(bytes.fromhex("61 62 00 00")) == "ab"
```

### `PrefixedArray(count, element)`

```python
NUMBERS = PrefixedArray(U8, U16_LE)
assert NUMBERS.build([1, 2]).hex(" ") == "02 01 00 02 00"
assert NUMBERS.parse(bytes.fromhex("02 01 00 02 00")) == [1, 2]
```

### `Tuple(*codecs)`

```python
ENTRY = Tuple(CSTRING, U8, U16_BE)
assert ENTRY.build(("a", 1, 2)).hex(" ") == "61 00 01 00 02"
assert ENTRY.parse(bytes.fromhex("61 00 01 00 02")) == ("a", 1, 2)
```

### `Switch(tag, cases, *, default=None)`

```python
VALUE = Switch(U8, {1: U32_BE, 2: CSTRING}, default=PASS)
assert VALUE.build((1, 7)).hex(" ") == "01 00 00 00 07"
assert VALUE.parse(bytes.fromhex("01 00 00 00 07")) == (1, 7)
assert VALUE.build((2, "a")).hex(" ") == "02 61 00"
assert VALUE.parse(bytes.fromhex("02 61 00")) == (2, "a")
assert VALUE.parse(bytes.fromhex("09")) == (9, None)  # the default
```

### Records as codecs

```python
PAIRS = PrefixedArray(U8, Pair)
assert PAIRS.build([Pair(1, 2)]).hex(" ") == "01 01 00 02"
assert PAIRS.parse(bytes.fromhex("01 01 00 02")) == [Pair(1, 2)]
```

## Protocols and helpers

### `Codec`, `CodecBase`

`Codec[T]` is the protocol of anything with `read(reader) -> T` and `build(value: T) -> bytes`; use it to
annotate parameters that take any codec. `CodecBase[T]` is the base class of the built-in combinators and
adds `parse(data)`.

```python
class Nibbles(CodecBase[tuple[int, int]]):
    """One byte, split into its high and low nibbles."""

    def read(self, reader: ByteReader) -> tuple[int, int]:
        byte = U8.read(reader)
        return byte >> 4, byte & 0xF

    def build(self, value: tuple[int, int]) -> bytes:
        return U8.build(value[0] << 4 | value[1])


def round_trip(codec: Codec[object], value: object) -> bytes:
    data = codec.build(value)
    assert codec.read(ByteReader(data)) == value
    return data


assert round_trip(Nibbles(), (4, 5)).hex() == "45"
assert Nibbles().parse(bytes.fromhex("45")) == (4, 5)
assert round_trip(U16_BE, 0x1234).hex() == "1234"
assert round_trip(Pair, Pair(1, 2)).hex() == "010002"
```

### `ByteReader`

```python
reader = ByteReader(bytes.fromhex("01 02 03 61 00"), base=100)
assert reader.read(1).hex() == "01"
assert reader.unpack_one(U16_BE) == 0x0203
assert reader.read_cstring() == b"a"
assert (reader.offset, reader.absolute, reader.remaining) == (5, 105, 0)
```

See [ByteReader](byte-reader.md) for the rest.

### `BytesLike`

The type of the buffers that `parse` and `ByteReader` accept: `bytes`, `bytearray` or `memoryview`.

```python
def first_byte(data: BytesLike) -> int:
    return U8.parse(data)


assert first_byte(b"\x01") == first_byte(bytearray(b"\x01")) == first_byte(memoryview(b"\x01")) == 1
```

### Exceptions

```python
try:
    U32_BE.parse(bytes.fromhex("00 01"))
except TruncatedDataError as e:
    assert isinstance(e, BinaryParseError) and isinstance(e, BinaryDataError)
    assert str(e) == "at offset 0: expected 4 bytes, found 2"

try:
    U8.build(256)
except BinaryBuildError as e:
    assert isinstance(e, BinaryDataError)
```

See [Errors](errors.md) for the tree and error locations.
