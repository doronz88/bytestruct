# Migrating from construct

bytestruct covers the part of [construct](https://github.com/construct/construct) that most binary
protocols use: fixed layouts, constants, strings, length prefixes, counted lists, tagged unions and
alignment. It does so without construct's runtime context, so the dynamic features (`this` expressions,
`IfThenElse`, `Pointer`, ...) have no declarative equivalent: they become a small codec or a few lines of
[`ByteReader`](byte-reader.md) code.

The main differences in shape:

- A `Struct` becomes a dataclass. Parsing returns an instance with typed attributes instead of a
  `Container`, and building takes an instance instead of a dict.
- Byte order is set once per record (`byte_order="<"`), not per field.
- Fixed-size fields come first; variable-length (codec) fields follow them.
- Parsing works on in-memory buffers; [`parse_stream`](streams.md) parses one value from a file or a stream.

## Mapping table

| construct | bytestruct |
| --- | --- |
| `Struct("a" / Int32ul, ...)` | `@dataclass class R(BinaryStruct, byte_order="<"): a: u32` |
| `Int8ul` / `Int8ub` | `u8` field, `U8` codec |
| `Int16ul`, `Int32ul`, `Int64ul` | `u16`, `u32`, `u64` in a `byte_order="<"` record; `U16_LE`, `U32_LE`, `U64_LE` codecs |
| `Int16ub`, `Int32ub`, `Int64ub` | the same fields in a `byte_order=">"` record; `U16_BE`, `U32_BE`, `U64_BE` codecs |
| `Int32ul` in a big-endian struct | `u32le` (and `u32be` the other way round) |
| `Int16sl`, `Int32sl`, `Int64sl` | `i16`, `i32`, `i64`; `I16_LE`, `I32_LE`, `I64_LE` codecs (and `_BE`) |
| `Int8sl`, `Float32l`, `Float64b`, ... | `Annotated[int, Format("b")]`, `Annotated[float, Format("f")]`, ... fields; `F32_LE`, `F64_BE`, ... codecs |
| `Const(0x1234, Int32ul)` | `Annotated[u32, Const(0x1234)] = field(default=0x1234, init=False)` |
| `Const(b"MAGC")` | `Annotated[bytes, Bytes(4), Const(b"MAGC")] = field(default=b"MAGC", init=False)` |
| `Bytes(16)` | `Annotated[bytes, Bytes(16)]` field, `Bytes(16)` codec |
| `Padding(4)` | `Annotated[bytes, Bytes(4)] = field(default=bytes(4), init=False, compare=False)` |
| `PaddedString(16, "utf8")` | `Annotated[str, PaddedString(16)]` |
| `CString("utf8")` | `Annotated[str, CSTRING]` field, `CSTRING` codec |
| `GreedyString("utf8")` | `GREEDY_STRING` |
| `PascalString(Int16ub, "utf8")` | `Prefixed(U16_BE, GREEDY_STRING)` |
| `Prefixed(Int32ul, X)` | `Prefixed(U32_LE, X)` |
| `PrefixedArray(Int32ul, X)` | `PrefixedArray(U32_LE, X)` |
| `Array(4, Int16ul)` | `Annotated[list[int], Array(u16, 4)]` (number elements only) |
| `Switch(this.kind, {...})` right after `"kind" / Int8ul` | `Switch(U8, {...})`, parsing to `(kind, value)` |
| `Aligned(4, X)` | `Aligned(4, X)` |
| `GreedyBytes` | `GREEDY_BYTES` |
| `Sequence(A, B)` | `Tuple(A, B)` |
| `Pass` | `PASS` |
| `Enum(Int16ul, Kind)` (with an `IntEnum`) | `Annotated[Kind, u16]`; `OpenIntEnum` to keep unknown values |
| `Adapter` / `ExprAdapter` | a [custom codec](codecs.md#writing-your-own-codec), or a [`Format` subclass](records.md#custom-field-types-format) for fixed-size fields |
| `StreamError`, `ConstError`, `ConstructError` | `TruncatedDataError`, `UnexpectedConstantError`, `BinaryDataError` |

### Behavior differences to watch for

- **`PaddedString`** decodes up to the first NUL (or the whole field when it has none) and ignores what
  follows, like construct's. Building a string longer than the field, or one containing a NUL, raises
  `BinaryBuildError`; construct raises too for an overlong string.
- **`Switch` parses to `(tag, value)`.** The tag is read by the `Switch` itself, from the bytes just before
  the value, instead of from an earlier field through `this`.
- **Trailing bytes are ignored** by `parse`, as in construct. There is no `Terminated`; check
  `reader.remaining` yourself when you need to.
- **Unknown enum values** raise `BinaryParseError` with a plain `IntEnum`; construct's `Enum` returns an
  `EnumInteger`. Use `OpenIntEnum` to keep them.

## Side by side

A construct header:

```python
import construct as c

CHeader = c.Struct(
    "magic" / c.Const(b"PKG1"),
    "version" / c.Int16ub,
    "flags" / c.Int16ub,
    "name" / c.PaddedString(16, "utf8"),
    "length" / c.Int32ub,
)

parsed = CHeader.parse(b"PKG1" + b"\x00\x02\x00\x01" + b"demo".ljust(16, b"\0") + b"\x00\x00\x00\x10")
assert (parsed.version, parsed.name, parsed.length) == (2, "demo", 16)
```

and its bytestruct equivalent:

```python
from dataclasses import dataclass, field
from typing import Annotated

from bytestruct import BinaryStruct, Bytes, Const, PaddedString, u16, u32


@dataclass
class Header(BinaryStruct, byte_order=">"):
    magic: Annotated[bytes, Bytes(4), Const(b"PKG1")] = field(default=b"PKG1", init=False)
    version: u16
    flags: u16
    name: Annotated[str, PaddedString(16)]
    length: u32


header = Header.parse(b"PKG1" + b"\x00\x02\x00\x01" + b"demo".ljust(16, b"\0") + b"\x00\x00\x00\x10")
assert (header.version, header.name, header.length) == (2, "demo", 16)
assert header.build() == CHeader.build(
    {"version": 2, "flags": 1, "name": "demo", "length": 16}
)
```

Variable-length parts translate one-to-one:

```python
from bytestruct import GREEDY_STRING, U16_BE, U16_LE, U32_LE, U64_LE, Aligned, Prefixed, PrefixedArray

c_strings = c.PrefixedArray(c.Int32ul, c.Aligned(4, c.PascalString(c.Int16ub, "utf8")))
strings = PrefixedArray(U32_LE, Aligned(4, Prefixed(U16_BE, GREEDY_STRING)))

values = ["a", "bcd", "héllo"]
assert strings.build(values) == c_strings.build(values)
assert strings.parse(c_strings.build(values)) == values
```

## `this`-dependent fields

construct lets any field refer to an earlier one through `this`. bytestruct has no context: a value that
depends on an earlier field is read by a codec or by hand.

**A count or length right before the data** is the common case, and is what `PrefixedArray` and
`Prefixed` express:

```python
c_items = c.Struct("count" / c.Int32ul, "items" / c.Array(c.this.count, c.Int16ul))
items = PrefixedArray(U32_LE, U16_LE)

assert items.build([1, 2, 3]) == c_items.build({"count": 3, "items": [1, 2, 3]})
assert items.parse(items.build([1, 2, 3])) == [1, 2, 3]
```

**A count elsewhere in the record**, for example in a header followed by other fields, is a few lines of
`ByteReader`:

```python
from bytestruct import ByteReader, u8


@dataclass
class Directory(BinaryStruct, byte_order="<"):
    count: u16
    flags: u8


def parse_directory(data: bytes) -> tuple[Directory, list[int]]:
    reader = ByteReader(data)
    directory = Directory.read(reader)
    entries = [U32_LE.read(reader) for _ in range(directory.count)]
    return directory, entries


assert parse_directory(b"\x02\x00\x07" + U32_LE.build(10) + U32_LE.build(20)) == (Directory(2, 7), [10, 20])
```

**`IfThenElse` / `If`** on an earlier field becomes a codec that reads the field and branches:

```python
from bytestruct import U8, BinaryParseError, CodecBase

c_record = c.Struct(
    "version" / c.Int8ul,
    "size" / c.IfThenElse(c.this.version >= 2, c.Int64ul, c.Int32ul),
)


class VersionedSize(CodecBase[tuple[int, int]]):
    """A version byte, then the size: a u64 from version 2 on, a u32 before."""

    def read(self, reader: ByteReader) -> tuple[int, int]:
        version = U8.read(reader)
        size = (U64_LE if version >= 2 else U32_LE).read(reader)
        return version, size

    def build(self, value: tuple[int, int]) -> bytes:
        version, size = value
        return U8.build(version) + (U64_LE if version >= 2 else U32_LE).build(size)


RECORD = VersionedSize()
for version in (1, 2):
    data = c_record.build({"version": version, "size": 1000})
    assert RECORD.build((version, 1000)) == data
    assert RECORD.parse(data) == (version, 1000)
```

The codec composes like any other: `PrefixedArray(U8, VersionedSize())`, or a record field annotated
`Annotated[tuple[int, int], VersionedSize()]`.

## No equivalent

These construct features have no bytestruct counterpart. Most become a few lines of `ByteReader` code or a
custom codec; some are out of scope by design.

| construct | Instead |
| --- | --- |
| `this` expressions, `Computed`, `Rebuild` | compute in Python, before `build` or after `parse` |
| `IfThenElse`, `If`, `Optional`, `Select` | a custom codec that branches (above) |
| `Pointer`, `Seek`, `Tell`, `Peek` | `ByteReader(data, offset=...)` for random access, `reader.offset` |
| `RepeatUntil`, `GreedyRange` | a loop over a `ByteReader` |
| `Array(this.count, ...)` with the count elsewhere | read the count, then loop (above) |
| `BitStruct`, `BitsInteger`, `Flag` | integer fields plus Python bit operations (or `enum.IntFlag`) |
| `Checksum`, `RawCopy` | compute over the bytes returned by `build` or the slice that was parsed |
| `Lazy`, `LazyBound` (recursive structures) | a custom codec whose `read` calls itself |
| `Struct.compile()` | not needed: records are compiled when the class is defined |
| `parse_stream`, `parse_file` | [`codec.parse_stream(stream.read)`](streams.md), `codec.parse_stream_async(reader.readexactly)` for `asyncio` streams |
| `Container` / `ListContainer` | dataclass instances and lists |
| Nested anonymous `Struct`s | separate record classes, nested with `Annotated[Inner, Inner]` (a codec field, so after the fixed-size fields) |
