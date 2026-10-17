# Errors

## The exception tree

Every failure raises a subclass of `BinaryDataError`, which is a `ValueError`:

```text
ValueError
└── BinaryDataError                 data that could not be parsed, or a value that could not be encoded
    ├── BinaryParseError            wire data could not be parsed
    │   ├── TruncatedDataError      the data ended before the structure was complete
    │   └── UnexpectedConstantError a Const field (magic, version, ...) had the wrong value
    └── BinaryBuildError            a value could not be encoded
```

They live in `bytestruct.exceptions` and are re-exported from `bytestruct`.

```python
from bytestruct import (
    BinaryBuildError,
    BinaryDataError,
    BinaryParseError,
    TruncatedDataError,
    UnexpectedConstantError,
)
from bytestruct import exceptions

assert exceptions.TruncatedDataError is TruncatedDataError
assert issubclass(TruncatedDataError, BinaryParseError)
assert issubclass(UnexpectedConstantError, BinaryParseError)
assert issubclass(BinaryBuildError, BinaryDataError)
assert issubclass(BinaryDataError, ValueError)
```

## Where it went wrong

An error carries its location in four attributes:

- `record`: the record class name, when the failure was in a record;
- `field`: the field name;
- `offset`: the **absolute** offset in the outermost buffer (a [`ByteReader`](byte-reader.md)'s `base`
  plus its position), when known. Build errors have none;
- `path`: the enclosing `Switch` cases, `PrefixedArray` elements and record codec fields the failure
  happened inside, outermost first.

`message` is the bare description, and `str(error)` prefixes it with the location:
`<path> -> <record>.<field> at offset <offset>: <message>`.

```python
from dataclasses import dataclass, field
from typing import Annotated

from bytestruct import CSTRING, U8, U32_LE, Aligned, BinaryStruct, Const, Prefixed, PrefixedArray, Switch, u32


@dataclass
class Header(BinaryStruct):
    magic: Annotated[u32, Const(0x29B00B92)] = field(default=0x29B00B92, init=False)
    size: u32


try:
    Header.parse(bytes(8))
except UnexpectedConstantError as e:
    assert str(e) == "Header.magic at offset 0: expected 0x29b00b92, got 0x0"
    assert (e.record, e.field, e.offset, e.path) == ("Header", "magic", 0, [])
    assert e.message == "expected 0x29b00b92, got 0x0"
```

Errors from nested codecs name each step on the way down. Element indexes appear as `[i]`, and `Switch`
cases by their key's `name` (for enum keys) or value:

```python
STRING = Aligned(4, Prefixed(U32_LE, CSTRING))
VALUES = PrefixedArray(U8, Switch(U8, {1: STRING, 2: U32_LE}))

data = b"\x02" + b"\x01" + STRING.build("a") + b"\x01" + U32_LE.build(200) + b"x"
try:
    VALUES.parse(data)
except TruncatedDataError as e:
    assert str(e) == "[1] -> 0x1 at offset 15: expected 200 bytes, found 1"
    assert e.path == ["[1]", "0x1"]
```

Integers in messages (magics, versions, tags) are shown in hex. Codec fields of records add
`Record.field` steps:

```python
@dataclass
class Message(BinaryStruct):
    kind: u32
    name: Annotated[str, CSTRING]


try:
    Message(1, "a\0b").build()
except BinaryBuildError as e:
    assert str(e) == "Message.name: embedded NUL in 'a\\x00b'"
```

## Catching errors

Catch `BinaryDataError` to handle any malformed input or unencodable value, or `ValueError` if you treat
it like any other bad value:

```python
def safe_parse(data: bytes):
    try:
        return Header.parse(data)
    except BinaryDataError:
        return None


assert safe_parse(Header(4).build()) == Header(4)
assert safe_parse(b"junk") is None
```

`TruncatedDataError` means "not enough bytes yet", which a stream reader can use to wait for more data:
everything else is malformed and will not get better.

```python
def frames(chunks):
    """Yield Header records from chunks of a byte stream."""
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        while True:
            try:
                header = Header.parse(buffer)
            except TruncatedDataError:
                break  # wait for the next chunk; any other error propagates
            yield header
            buffer = buffer[Header.SIZE :]


stream = Header(1).build() + Header(2).build()
assert list(frames([stream[:5], stream[5:11], stream[11:]])) == [Header(1), Header(2)]
```

Since a readable wrong constant wins over truncation, a stream that is not this format fails right
away instead of waiting forever:

```python
try:
    list(frames([b"HTTP/1.1 200 OK"]))
except UnexpectedConstantError as e:
    assert str(e) == "Header.magic at offset 0: expected 0x29b00b92, got 0x50545448"
```

## For codec authors

Two helpers let custom [codecs](codecs.md#writing-your-own-codec) produce errors located like the
built-in ones.

**Raise with an offset.** Use `reader.absolute` (captured before reading) as `offset`:

```python
from bytestruct import ByteReader, CodecBase


class Ascii(CodecBase[str]):
    def read(self, reader: ByteReader) -> str:
        start = reader.absolute
        raw = reader.read_cstring()
        if not raw.isascii():
            raise BinaryParseError("not ASCII", offset=start)
        return raw.decode()

    def build(self, value: str) -> bytes:
        return value.encode("ascii") + b"\0"


try:
    Ascii().read(ByteReader(b"\0\0\xff\0", offset=2, base=10))
except BinaryParseError as e:
    assert str(e) == "at offset 12: not ASCII"
```

**`within(step)`** records, as an error propagates, that it happened inside `step` (a case, an element or
a field). It inserts at the front of `path`, so each enclosing codec adds its own step on the way out:

```python
class Named(CodecBase[tuple[str, int]]):
    """A name, then a u32 value; errors in the value say which name it belonged to."""

    def read(self, reader: ByteReader) -> tuple[str, int]:
        name = CSTRING.read(reader)
        try:
            return name, U32_LE.read(reader)
        except BinaryDataError as e:
            e.within(name)
            raise

    def build(self, value: tuple[str, int]) -> bytes:
        return CSTRING.build(value[0]) + U32_LE.build(value[1])


try:
    PrefixedArray(U8, Named()).parse(b"\x01" + b"size\0" + b"\x01")
except TruncatedDataError as e:
    assert str(e) == "[0] -> size at offset 6: expected 4 bytes, found 1"
```

**`located(record=..., field=..., offset=...)`** returns a copy of the error (same type, same `path`) with
any location it lacks filled in; what it already has is kept. It is how records locate the errors that a
field's `decode` raises without one:

```python
error = BinaryParseError("bad value")
located = error.located(record="Header", field="size", offset=4)
assert type(located) is BinaryParseError
assert str(located) == "Header.size at offset 4: bad value"
assert str(located.located(offset=99)) == "Header.size at offset 4: bad value"  # already located
assert str(error) == "bad value"  # the original is unchanged
```

Raise the copy with `raise error.located(...) from error`, to keep the original as its cause.
