# bytestruct

[![Python application](https://github.com/doronz88/bytestruct/workflows/Python%20application/badge.svg)](https://github.com/doronz88/bytestruct/actions/workflows/python-app.yml "Python application action")
[![Pypi version](https://img.shields.io/pypi/v/bytestruct.svg)](https://pypi.org/project/bytestruct/ "PyPi package")

`bytestruct` parses and builds binary wire formats with the standard library alone. Fixed-size records are
dataclasses whose field annotations give each field's wire type; variable-length data is described by small
codecs that compose like [construct](https://github.com/construct/construct)'s. Everything is fully typed.

The layout of a record is compiled into `struct.Struct` objects once, when the class is defined, so parsing a
record is a single `unpack_from` per byte-order run.

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


header = Header.parse(data)  # short input -> TruncatedDataError, wrong magic -> UnexpectedConstantError
data = header.build()  # an unencodable value or a wrong constant -> BinaryBuildError
```

Wire fields are positional `__init__` parameters in wire order. A field declared
`= field(default=..., init=False)` is left out of `__init__`: parse skips its bytes (padding) and build packs
its default (on a `Const` field, the constant). Fields without a wire type may follow the wire fields and are
ignored by parse/build. Records cannot be subclassed.

Available field types include `u8`..`u64`, `i16`..`i64`, byte-order pinned `u32le`/`u32be`, `Bytes(n)`,
`PaddedString(n)`, `uuid_t`, `ipv4`/`ipv6`, `Array(element, n)`, int-enum fields and `Const` magics.

## Codecs

```python
from bytestruct import CSTRING, U32_LE, U64_LE, Aligned, Prefixed, Switch

STRING = Aligned(4, Prefixed(U32_LE, CSTRING))  # u32 length, NUL-terminated UTF-8, padded to 4 bytes
OBJECT = Switch(U32_LE, {1: STRING, 2: U64_LE})  # parses to (tag, value)

assert OBJECT.parse(OBJECT.build((1, "hi"))) == (1, "hi")
```

A codec is anything with `read(reader)` and `build(value)`: the combinators (`Prefixed`, `Aligned`,
`PrefixedArray`, `Tuple`, `Switch`, `CString`, `GreedyBytes`, `Pass`), the scalars (`U8`, `U16_BE`, `U32_LE`,
...), `Bytes(n)`, and record classes themselves. A record field annotated with a codec
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

## License

GPL-3.0-or-later
