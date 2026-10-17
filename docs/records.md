# Records

A record is a dataclass deriving from `BinaryStruct`. The class's field annotations are read once, when
the class is defined, and compiled into a fixed wire layout. Every example on this page builds a value and
parses it back.

```python
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Annotated
import ipaddress
import uuid

from bytestruct import (
    CSTRING,
    GREEDY_BYTES,
    U16_BE,
    Array,
    BinaryBuildError,
    BinaryParseError,
    BinaryStruct,
    Bytes,
    Const,
    Format,
    OpenIntEnum,
    PaddedString,
    Prefixed,
    UnexpectedConstantError,
    i16,
    i32,
    i64,
    ipv4,
    ipv6,
    u8,
    u16,
    u32,
    u32be,
    u32le,
    u64,
    uuid_t,
)
```

## Integer fields

| Alias | Python type | Wire type |
| --- | --- | --- |
| `u8` | `int` | unsigned 8-bit |
| `u16`, `u32`, `u64` | `int` | unsigned 16/32/64-bit, in the record's byte order |
| `i16`, `i32`, `i64` | `int` | signed 16/32/64-bit, in the record's byte order |
| `u32le`, `u32be` | `int` | unsigned 32-bit, always little-/big-endian |

```python
@dataclass
class Numbers(BinaryStruct, byte_order="<"):
    a: u8
    b: u16
    c: u32
    d: u64
    e: i16
    f: i32
    g: i64


numbers = Numbers(1, 2, 3, 4, -5, -6, -7)
data = numbers.build()
assert Numbers.SIZE == len(data) == 1 + 2 + 4 + 8 + 2 + 4 + 8
assert data[:3] == b"\x01\x02\x00"
assert Numbers.parse(data) == numbers
```

Fields are packed back to back, with no alignment padding (the `struct` module's `<`/`>` modes). There
is no signed 8-bit or floating-point field alias; define one with [`Format`](#custom-field-types-format)
(`Annotated[float, Format("d")]`), or use a scalar such as `F64_LE` as a
[codec field](#variable-length-fields).

Values that do not fit their wire type fail at build time:

```python
try:
    Numbers(256, 0, 0, 0, 0, 0, 0).build()
except BinaryBuildError as e:
    assert e.record == "Numbers"
```

## Byte order

The class argument `byte_order` sets the byte order of every field that does not pin its own. It is a
`struct` byte-order character: `"<"` (little-endian, the default) or `">"` (big-endian). `u32le` and
`u32be` always use their own byte order, so one record can mix both:

```python
@dataclass
class Mixed(BinaryStruct, byte_order=">"):
    length: u16  # big-endian, from the record
    pid: u32le  # always little-endian
    tag: u32be  # always big-endian


mixed = Mixed(1, 2, 3)
assert mixed.build() == b"\x00\x01" + b"\x02\x00\x00\x00" + b"\x00\x00\x00\x03"
assert Mixed.parse(mixed.build()) == mixed
```

## Raw bytes: `Bytes(n)`

`Annotated[bytes, Bytes(n)]` is a field of exactly `n` raw bytes. Building it from a value of any other
length is an error (`struct` would silently pad or truncate it):

```python
@dataclass
class Tagged(BinaryStruct):
    tag: Annotated[bytes, Bytes(4)]
    value: u8


assert Tagged(b"ABCD", 1).build() == b"ABCD\x01"
assert Tagged.parse(b"ABCD\x01") == Tagged(b"ABCD", 1)

try:
    Tagged(b"ABC", 1).build()
except BinaryBuildError as e:
    assert str(e) == "Tagged.tag: expected 4 bytes, got 3"
```

## Strings: `PaddedString(n, errors="strict")`

`Annotated[str, PaddedString(n)]` is a fixed `n`-byte field holding a NUL-padded UTF-8 string of at most
`n` bytes. Parsing decodes up to the first NUL and ignores whatever follows it; a string that fills the
whole field has no terminator. Building NUL-pads the encoded string to `n` bytes.

```python
@dataclass
class Named(BinaryStruct):
    name: Annotated[str, PaddedString(8)]


assert Named("abc").build() == b"abc\0\0\0\0\0"
assert Named.parse(b"abc\0garbage"[:8]) == Named("abc")  # bytes after the NUL are ignored
assert Named("fullname").build() == b"fullname"  # exactly 8 bytes: no terminator
assert Named.parse(b"fullname") == Named("fullname")
```

A string longer than the field, or one containing a NUL (parse would stop there), cannot be built:

```python
for bad in ("much too long", "a\0b"):
    try:
        Named(bad).build()
    except BinaryBuildError as e:
        assert e.field == "name"

try:
    Named("much too long").build()
except BinaryBuildError as e:
    assert str(e) == "Named.name: string of 13 bytes does not fit its 8-byte field"
```

Invalid UTF-8 raises `BinaryParseError`. Pass `errors` (any `bytes.decode` error handler) to accept it
instead, for example names that a peer cut mid-character:

```python
@dataclass
class Lenient(BinaryStruct):
    strict: Annotated[str, PaddedString(4)]
    lenient: Annotated[str, PaddedString(4, errors="replace")]


assert Lenient.parse(b"ok\0\0" + b"\xc3\0\0\0") == Lenient("ok", "�")

try:
    Lenient.parse(b"\xc3\0\0\0" + b"\0\0\0\0")
except BinaryParseError as e:
    assert (e.field, e.offset) == ("strict", 0)
```

## UUIDs and IP addresses

`uuid_t` is a 16-byte UUID parsed into a `uuid.UUID`; `ipv4` and `ipv6` are packed 4- and 16-byte
addresses parsed into `ipaddress.IPv4Address` and `ipaddress.IPv6Address`:

```python
@dataclass
class Peer(BinaryStruct):
    id: uuid_t
    v4: ipv4
    v6: ipv6


peer = Peer(uuid.UUID(int=1), ipaddress.IPv4Address("10.0.0.1"), ipaddress.IPv6Address("fe80::1"))
data = peer.build()
assert Peer.SIZE == len(data) == 16 + 4 + 16
assert data[16:20] == b"\x0a\x00\x00\x01"
assert Peer.parse(data) == peer
```

## Fixed-length arrays: `Array(element, count)`

`Annotated[list[int], Array(element, count)]` is `count` numbers of one of the integer aliases above,
parsed into a list:

```python
@dataclass
class Stats(BinaryStruct, byte_order=">"):
    counts: Annotated[list[int], Array(u16, 3)]
    pinned: Annotated[list[int], Array(u32be, 1)]


stats = Stats([1, 2, 0x102], [5])
data = stats.build()
assert data == b"\x01\x00\x02\x00\x02\x01" + b"\x00\x00\x00\x05"
assert Stats.parse(data) == stats
```

!!! warning "Array elements do not follow the record's byte order"
    Array elements are little-endian unless their alias pins a byte order, even in a big-endian record:
    `counts` above is little-endian. Use a pinned alias (`u32be`) for big-endian elements.

Building from a list of the wrong length is an error:

```python
try:
    Stats([1], [5]).build()
except BinaryBuildError as e:
    assert str(e) == "Stats.counts: expected 3 elements, got 1"
```

## Int-enum fields

`Annotated[SomeEnum, u16]` (any integer alias) parses the integer into the enum type. An integer that is
not a member raises `BinaryParseError`:

```python
class Op(IntEnum):
    READ = 1
    WRITE = 2


@dataclass
class Request(BinaryStruct):
    op: Annotated[Op, u16]


assert Request(Op.WRITE).build() == b"\x02\x00"
assert Request.parse(b"\x02\x00").op is Op.WRITE

try:
    Request.parse(b"\x09\x00")
except BinaryParseError as e:
    assert str(e) == "Request.op at offset 0: 9 is not a valid Op"
```

To keep values from a newer peer (an unknown opcode, status or level), derive the enum from
`OpenIntEnum`, which maps unknown values to cached pseudo-members instead of raising:

```python
class Status(OpenIntEnum):
    OK = 0


@dataclass
class Reply(BinaryStruct):
    status: Annotated[Status, u8]


reply = Reply.parse(b"\x2a")
assert reply.status == 42 and isinstance(reply.status, Status) and reply.status.name == "42"
assert Reply.parse(b"\x2a").status is reply.status
```

## Constants: `Const(value)`

`Const` marks a field (a magic number, a version) that must always hold one value. Declare it with
`field(default=..., init=False)`, so that it is not an `__init__` parameter and build packs the constant:

```python
@dataclass
class Header(BinaryStruct, byte_order="<"):
    magic: Annotated[u32, Const(0x1F3D5B79)] = field(default=0x1F3D5B79, init=False)
    version: Annotated[bytes, Bytes(2), Const(b"v1")] = field(default=b"v1", init=False)
    size: u32


assert Header(16).build() == b"\x79\x5b\x3d\x1f" + b"v1" + b"\x10\x00\x00\x00"
assert Header.parse(Header(16).build()) == Header(16)
```

A wrong constant raises `UnexpectedConstantError` on parse, and `BinaryBuildError` on build:

```python
try:
    Header.parse(b"\0" * Header.SIZE)
except UnexpectedConstantError as e:
    assert str(e) == "Header.magic at offset 0: expected 0x1f3d5b79, got 0x0"

header = Header(16)
header.version = b"v2"
try:
    header.build()
except BinaryBuildError as e:
    assert str(e) == "Header.version: expected b'v1', got b'v2'"
```

Constants are checked before every other conversion, so a buffer that is not this record at all reports
the wrong magic rather than, say, an invalid enum value. When the buffer is too short but the constant is
already readable, the wrong constant is reported too.

`Const` compares the raw unpacked value, so it is only for fields whose value is not converted: integers,
`Bytes` and int enums. `Annotated[u32, Const(1)]` and `Annotated[int, u32, Const(1)]` are equivalent.

## Custom field types: `Format`

Every fixed-size field type is a `Format`: a single-value `struct` code (with an optional pinned byte
order), placed in an `Annotated` alias. `u16` is `Annotated[int, Format("H")]`, and new aliases are
declared the same way:

```python
i8 = Annotated[int, Format("b")]
f64 = Annotated[float, Format("d")]  # in the record's byte order
u16be = Annotated[int, Format("H", ">")]  # always big-endian


@dataclass
class Sample(BinaryStruct, byte_order="<"):
    delta: i8
    value: f64
    port: u16be


sample = Sample(-1, 0.5, 8080)
assert sample.build() == b"\xff" + b"\x00\x00\x00\x00\x00\x00\xe0\x3f" + b"\x1f\x90"
assert Sample.parse(sample.build()) == sample
```

To convert values, subclass `Format` and override `decode` and/or `encode`: see
[Extending bytestruct](extending.md#custom-fixed-size-field-types) for worked examples (an ASCII-octal
number, a fixed-point number, a bitfield).

## Padding and reserved bytes

A field declared with `field(default=..., init=False)` is left out of `__init__`. Parse skips its bytes
(the attribute keeps its default) and build packs the default. That makes it a padding or reserved field;
`compare=False` keeps it out of `==`:

```python
@dataclass
class Padded(BinaryStruct):
    a: u8
    reserved: Annotated[bytes, Bytes(3)] = field(default=bytes(3), init=False, compare=False)
    b: u32


assert Padded(1, 2).build() == b"\x01\x00\x00\x00\x02\x00\x00\x00"
parsed = Padded.parse(b"\x01\xaa\xbb\xcc\x02\x00\x00\x00")
assert parsed == Padded(1, 2)
assert parsed.reserved == bytes(3)  # the default, not the wire bytes
```

## Non-wire fields

A field without a wire type (a plain annotation such as `bytes` or `Optional[str]`, with a default) is an
ordinary dataclass field that parse and build ignore. Declare such fields after the wire fields:

```python
@dataclass
class Frame(BinaryStruct):
    length: u32
    payload: bytes = b""  # filled in later, not part of the wire record


frame = Frame.parse(b"\x03\x00\x00\x00")
frame.payload = b"abc"
assert frame.build() == b"\x03\x00\x00\x00"
assert Frame.SIZE == 4
```

!!! note
    Only `Annotated[...]` fields carry a wire type. A field annotated with a bare record class
    (`inner: Inner`) is a non-wire field; to nest a record, use `Annotated[Inner, Inner]` (see below).

## Variable-length fields

A field annotated with a [codec](codecs.md) (anything with `read` and `build`) instead of a fixed-size
wire type makes the record variable-length. Codec fields are read and written in order after all the
fixed-size fields, so they must come after them:

```python
@dataclass
class Entry(BinaryStruct, byte_order=">"):
    magic: Annotated[u16, Const(0xCAFE)] = field(default=0xCAFE, init=False)
    kind: Annotated[Op, u8]
    name: Annotated[str, CSTRING]
    body: Annotated[bytes, Prefixed(U16_BE, GREEDY_BYTES)]


entry = Entry(Op.READ, "ab", b"xyz")
data = entry.build()
assert data == b"\xca\xfe" + b"\x01" + b"ab\0" + b"\x00\x03xyz"
assert Entry.parse(data) == entry
assert Entry.SIZE == 3  # only the fixed-size fields
```

Records are codecs too, so `Annotated[Inner, Inner]` nests one record in another:

```python
@dataclass
class Inner(BinaryStruct):
    a: u8
    b: u16


@dataclass
class Outer(BinaryStruct):
    count: u8
    inner: Annotated[Inner, Inner]


assert Outer(1, Inner(2, 3)).build() == b"\x01\x02\x03\x00"
assert Outer.parse(b"\x01\x02\x03\x00") == Outer(1, Inner(2, 3))
```

A fixed-size field after a codec field is rejected when the class is defined:

```python
try:

    @dataclass
    class Interleaved(BinaryStruct):
        name: Annotated[str, CSTRING]
        size: u32

except TypeError as e:
    assert str(e) == "Interleaved.size: fixed-size fields must precede the codec fields"
```

## `SIZE`

`Record.SIZE` is the size in bytes of the record's fixed-size fields: the whole record when it has no
codec fields.

```python
assert Header.SIZE == 10
assert Numbers.SIZE == 29
```

## No subclassing

Records cannot be subclassed or combined with other base classes; compose them as fields instead. The
check runs when the class is defined:

```python
try:

    @dataclass
    class Extended(Header):
        extra: u8

except TypeError as e:
    assert str(e) == "Extended: records cannot be subclassed or mixed with other bases"
```
