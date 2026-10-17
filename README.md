<p align="center">
  <img src="https://doronz88.github.io/bytestruct/assets/logo.svg" alt="bytestruct logo" width="128">
</p>

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

Each record class gets its own `parse`/`read`/`build`, compiled when the class is defined, and the work
happens mostly inside `struct`'s C code. In the
[benchmark](https://doronz88.github.io/bytestruct/how-it-works/#benchmark) (Apple M5 Pro, CPython 3.12,
construct 2.10.70):

| Case | bytestruct | construct | construct, compiled | ctypes |
| --- | ---: | ---: | ---: | ---: |
| header parse | 0.14 µs | 2.20 µs (15.7x) | 0.87 µs (6.2x) | 0.09 µs (0.6x) |
| header build | 0.10 µs | 2.05 µs (20.5x) | 1.17 µs (11.7x) | 0.03 µs (0.3x) |
| 100 strings parse | 26.8 µs | 131.8 µs (4.9x) | 83.7 µs (3.1x) | |
| 100 strings build | 11.3 µs | 46.6 µs (4.1x) | 41.6 µs (3.7x) | |

A record parse costs the same as a hand-written `struct.unpack_from` into a plain dataclass. ctypes is
faster for a fixed C layout, but its fields convert from C on every read, overflow silently, and cannot be
variable-length.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the checks CI runs and how releases are cut.

## License

MIT
