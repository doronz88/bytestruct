# bytestruct

`bytestruct` parses and builds binary wire formats with nothing but the standard library. Fixed-size
records are dataclasses whose field annotations name each field's wire type; variable-length data is
described by small codecs that compose the way [construct](https://github.com/construct/construct)'s do.

It was extracted from [pymobiledevice3](https://github.com/doronz88/pymobiledevice3), where it replaced
construct for every Apple wire protocol the project speaks.

## Why

- **Typed.** A record is a plain dataclass, so `header.size` is an `int` to your type checker and your
  editor, not a key in a dict. The package itself is checked by pyright and ships `py.typed`.
- **Fast.** A record's layout is compiled into `struct.Struct` objects once, when the class is defined, so
  parsing a record is one `unpack_from` per byte-order run. See [How it works](how-it-works.md) for the
  design and a benchmark against construct.
- **No dependencies.** Only the standard library (plus `typing_extensions` on Python < 3.11, for
  `typing.Self`).
- **Python 3.9+.** CI runs the suite on CPython 3.9 through 3.14 (and the 3.15 prerelease) on Linux,
  macOS and Windows.
- **Construct-like composition, without the runtime magic.** `Prefixed`, `Switch`, `Aligned`,
  `PrefixedArray` and friends nest like construct's, but there are no `this` expressions, no context
  dictionaries and no stream seeking. Each codec is a small object with a `read(reader)` and a
  `build(value)` method that you can read in one sitting, and errors say exactly where the bytes went
  wrong.

## Install

```shell
python3 -m pip install -U bytestruct
```

## A 30-second example

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


header = Header(size=1024, name="notes.txt", kind=Kind.FILE)
data = header.build()
assert len(data) == Header.SIZE == 26

parsed = Header.parse(data)
assert parsed == header
assert parsed.kind is Kind.FILE
```

Variable-length data is described with codecs:

```python
from bytestruct import CSTRING, U32_LE, U64_LE, Aligned, Prefixed, Switch

STRING = Aligned(4, Prefixed(U32_LE, CSTRING))  # u32 length, NUL-terminated UTF-8, padded to 4 bytes
OBJECT = Switch(U32_LE, {1: STRING, 2: U64_LE})  # parses to (tag, value)

assert OBJECT.build((1, "hi")) == b"\x01\x00\x00\x00" + b"\x03\x00\x00\x00" + b"hi\x00" + b"\x00"
assert OBJECT.parse(OBJECT.build((1, "hi"))) == (1, "hi")
assert OBJECT.parse(OBJECT.build((2, 7))) == (2, 7)
```

Head to [Getting started](getting-started.md) next.
