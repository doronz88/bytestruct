# bytestruct

[![Python application](https://github.com/doronz88/bytestruct/workflows/Python%20application/badge.svg)](https://github.com/doronz88/bytestruct/actions/workflows/python-app.yml "Python application action")
[![Pypi version](https://img.shields.io/pypi/v/bytestruct.svg)](https://pypi.org/project/bytestruct/ "PyPi package")
[![Docs](https://github.com/doronz88/bytestruct/actions/workflows/docs.yml/badge.svg)](https://doronz88.github.io/bytestruct/ "Documentation")

`bytestruct` parses and builds binary wire formats with the standard library alone. Fixed-size records are
dataclasses whose field annotations give each field's wire type; variable-length data is described by small
codecs that compose like [construct](https://github.com/construct/construct)'s. Everything is fully typed.

The layout of a record is compiled into `struct.Struct` objects once, when the class is defined, so parsing a
record is a single `unpack_from` per byte-order run.

**Documentation: <https://doronz88.github.io/bytestruct/>**, including a
[migration guide from construct](https://doronz88.github.io/bytestruct/migrating-from-construct/).

## Install

```shell
python3 -m pip install -U bytestruct
```

## Fixed-size records

```python
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Annotated

from bytestruct import BinaryStruct, Const, PaddedString, u16, u32


class Kind(IntEnum):
    FILE = 1
    DIRECTORY = 2


@dataclass
class Header(BinaryStruct, byte_order="<"):
    magic: Annotated[u32, Const(0x1F3D5B79)] = field(default=0x1F3D5B79, init=False)
    size: u32
    name: Annotated[str, PaddedString(16)]
    kind: Annotated[Kind, u16]  # parsed into the enum type


data = Header(size=1024, name="notes.txt", kind=Kind.FILE).build()  # a bad value -> BinaryBuildError
header = Header.parse(data)  # short input -> TruncatedDataError, wrong magic -> UnexpectedConstantError
assert header.kind is Kind.FILE
```

Wire fields are positional `__init__` parameters in wire order. A field declared
`= field(default=..., init=False)` is left out of `__init__`: parse skips its bytes (padding) and build packs
its default (on a `Const` field, the constant). Fields without a wire type may follow the wire fields and are
ignored by parse/build. Records cannot be subclassed.

Available field types include `u8`..`u64`, `i16`..`i64`, byte-order pinned `u32le`/`u32be`, `Bytes(n)`,
`PaddedString(n)`, `uuid_t`, `ipv4`/`ipv6`, `Array(element, n)`, int-enum fields and `Const` magics; custom
ones subclass `Format`.

## Codecs

```python
from bytestruct import CSTRING, U32_LE, U64_LE, Aligned, Prefixed, Switch

STRING = Aligned(4, Prefixed(U32_LE, CSTRING))  # u32 length, NUL-terminated UTF-8, padded to 4 bytes
OBJECT = Switch(U32_LE, {1: STRING, 2: U64_LE})  # parses to (tag, value)

assert OBJECT.parse(OBJECT.build((1, "hi"))) == (1, "hi")
```

A codec is anything with `read(reader)` and `build(value)`: the combinators (`Prefixed`, `Aligned`,
`PrefixedArray`, `Tuple`, `Switch`), the leaves (`CSTRING`, `GREEDY_STRING`, `GREEDY_BYTES`, `PASS`), the
scalars (`U8`, `U16_LE`/`U16_BE`, ..., `F64_BE`), `Bytes(n)`, and record classes themselves. A record field annotated with a codec
(`name: Annotated[str, CSTRING]`) makes the record variable-length; such fields follow the fixed-size ones.
Formats with no declarative shape are read by hand with `ByteReader`, whose sub-readers are windows on the same
buffer (nothing is copied).

## Errors

Every failure raises a `BinaryDataError` (a `ValueError`): `BinaryParseError` (with `TruncatedDataError` and
`UnexpectedConstantError`) or `BinaryBuildError`. Errors carry the record, field, absolute offset and the path
through nested codecs, and prefix their message with them:

```text
Header.magic at offset 0: expected 0x1f3d5b79, got 0x0
DICTIONARY -> [2] -> STRING at offset 104: expected 200 bytes, found 16
```

## Performance

Parsing and building run mostly inside `struct`'s C code, and conversions cost a Python call only on the
fields that need one. In the [benchmark](https://doronz88.github.io/bytestruct/how-it-works/#benchmark)
(Apple M5 Pro, CPython 3.12, construct 2.10.70):

| Case | bytestruct | construct | construct, compiled |
| --- | ---: | ---: | ---: |
| header parse | 0.30 µs | 2.14 µs (7.2x) | 0.85 µs (2.9x) |
| header build | 0.16 µs | 2.04 µs (12.8x) | 1.15 µs (7.2x) |
| 100 strings parse | 26.5 µs | 133.7 µs (5.0x) | 84.3 µs (3.2x) |
| 100 strings build | 11.5 µs | 47.2 µs (4.1x) | 41.5 µs (3.6x) |

That is 4-13x faster than construct, and 3-7x faster than construct's `compile()`. A hand-written
`struct.unpack_from` into a plain dataclass is still about twice as fast for one small header; that gap pays
for typed fields, constant checks and located errors.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the checks CI runs and how releases are cut.

## License

MIT
