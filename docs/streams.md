# Streams

`parse` and `read` work on bytes that are already in memory. To parse a value straight from a file, a socket or
an `asyncio` stream, without knowing its size up front, use `parse_stream` or `parse_stream_async`. Both parse
one value and leave the stream right after it, so the next read starts at the next value.

```python
import asyncio
import io
from dataclasses import dataclass
from typing import Annotated

from bytestruct import (
    CSTRING,
    GREEDY_BYTES,
    U32_LE,
    BinaryParseError,
    BinaryStruct,
    Prefixed,
    TruncatedDataError,
    parse_stream,
    parse_stream_async,
    u16,
)


@dataclass
class Message(BinaryStruct, byte_order="<"):
    kind: u16
    body: Annotated[bytes, Prefixed(U32_LE, GREEDY_BYTES)]


stream = io.BytesIO(Message(1, b"first").build() + Message(2, b"second").build())
assert parse_stream(Message, stream.read) == Message(1, b"first")
assert parse_stream(Message, stream.read) == Message(2, b"second")
assert stream.read() == b""
```

`parse_stream` takes a `read(size)` function that returns up to `size` bytes, and `b""` at the end of the
stream, such as a binary file's `read`. `parse_stream_async` takes one to await, such as
`asyncio.StreamReader.readexactly`:

```python
async def receive(reader: asyncio.StreamReader) -> list[Message]:
    return [await parse_stream_async(Message, reader.readexactly) for _ in range(2)]


async def main() -> list[Message]:
    reader = asyncio.StreamReader()
    reader.feed_data(Message(1, b"first").build() + Message(2, b"second").build())
    return await receive(reader)


assert asyncio.run(main()) == [Message(1, b"first"), Message(2, b"second")]
```

Errors the `read` function raises (`IncompleteReadError`, a closed connection, ...) propagate unchanged.

## How much is read

The value is parsed from the bytes read so far. The first read is the size every value of the codec has at
least (a record's fixed-size part, the length of a `Prefixed`, the count of a `PrefixedArray`, ...), and while
the value extends past the bytes read, exactly the missing bytes are read and it is parsed again. In `Message`
above, that is the fixed part with the body's length, then the body:

```python
sizes = []
source = io.BytesIO(Message(1, b"first").build() + b"next")


def recording_read(size: int) -> bytes:
    sizes.append(size)
    return source.read(size)


parse_stream(Message, recording_read)
assert sizes == [6, 5]
assert source.read() == b"next"
```

Only bytes the value certainly covers are read, so nothing after it is consumed. Each extra read parses the
value again, from its start, which is cheap for the headers and messages of a protocol.

## What can be parsed

- **Greedy values** (`GREEDY_BYTES`, `GREEDY_STRING`) run to the end of their data, and a stream has no end to
  stop at. Inside a `Prefixed` they work as usual. At the top level, or directly in a record, they raise
  `BinaryParseError`:

```python
try:
    parse_stream(GREEDY_BYTES, io.BytesIO(b"data").read)
except BinaryParseError as e:
    assert str(e) == "a greedy value cannot be parsed from a stream; prefix it with its length"
```

- **C strings** outside a `Prefixed` are read a byte at a time, since their terminator could be anywhere:

```python
stream = io.BytesIO(b"name\0rest")
assert parse_stream(CSTRING, stream.read) == "name"
assert stream.read() == b"rest"
```

- **A stream that ends** before the value is complete raises `TruncatedDataError`, located at the end of the
  data that was read:

```python
try:
    parse_stream(Message, io.BytesIO(Message(1, b"first").build()[:-2]).read)
except TruncatedDataError as e:
    assert str(e) == "at offset 9: stream ended: expected 2 more bytes"
```

- **Malformed values** fail without reading further than the minimum size: a wrong constant as soon as it is
  parsed, and a value cut short inside a `Prefixed` (which more data cannot fix) right away.

## Custom codecs

A [custom codec](codecs.md#writing-your-own-codec) streams when it reads through the `ByteReader` methods
(`read`, `skip`, `unpack`, `sub`, ...), which is how the built-in codecs read. A codec that catches
`TruncatedDataError` to try something else cannot be parsed from a stream correctly: it would make that choice
on the bytes read so far.

While a value is parsed from a stream, a `TruncatedDataError` past the bytes read so far carries `needed`, the
number of bytes missing; it is `None` when more data cannot help.
