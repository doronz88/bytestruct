# How it works

bytestruct is a single module of about a thousand lines. This page walks through what happens when a
record class is defined, parsed and built, and why it is shaped that way. The names below (`_layout`,
`_runs`, `_plain`, ...) are private implementation details: read them in
[`bytestruct/__init__.py`](https://github.com/doronz88/bytestruct/blob/master/bytestruct/__init__.py), but
do not depend on them.

## Compilation, at class-definition time

`BinaryStruct.__init_subclass__` runs once, when the `class` statement executes (before `@dataclass`
decorates the class). It:

1. **Resolves the annotations** with `typing.get_type_hints(include_extras=True)`, which keeps the
   `Annotated` metadata. For each field it finds the last `Format` (the fixed-size wire type), any `Const`
   and, if there is no `Format`, a codec (anything with `read` and `build`). Python flattens nested
   `Annotated`, so `Annotated[u32, Const(1)]` is `Annotated[int, Format("I"), Const(1)]`. An int-enum base
   type (`Annotated[Kind, u16]`) wraps the format in one that converts to the enum.
2. **Lays out the fixed-size fields**: each gets a one-field `struct.Struct` (its byte order is the
   format's pinned one, or the record's) and an offset, the running sum of the sizes before it. A
   `dataclasses.field(init=False)` is noted as not being an `__init__` parameter. A fixed-size field after
   a codec field is a `TypeError`.
3. **Groups consecutive fields of the same byte order into runs**, each compiled into a single
   `struct.Struct`. A record without pinned fields is one run; `u16, u32be, u8` in a `"<"` record is three
   (`<H`, `>I`, `<B`).
4. **Precomputes the work lists**: the `Const` fields; the fields whose format overrides `decode` (the
   *decoders*); the non-constant fields whose format overrides `encode` (the *encoders*); an
   `operator.attrgetter` over the fixed-size fields; the indexes of the `__init__` parameters, when not all
   fields are; and `SIZE`.
5. **Picks the fast paths**:
    - `_plain` parse: one run, no constants, no decoders, every field an `__init__` parameter, no codec
      fields.
    - `_plain_build`: one run, no constants, no encoders.

Records cannot be subclassed (the class's only base must be `BinaryStruct`), so this layout is final:
there are no base-class fields to merge, and no subclass that could change it.

## Parsing

A `_plain` record parses in one line: check the length, then

```text
cls(*run.struct.unpack_from(data, start))
```

That is one C-level `unpack_from` straight into the dataclass `__init__`: no intermediate dictionary, no
per-field Python code.

Otherwise, `_unpack` builds the `__init__` argument list:

1. check `SIZE` against the available bytes once (not per field);
2. `unpack_from` each run at its offset, extending one list of values;
3. compare each `Const` field's raw value, before any conversion, so a buffer that is not this record at
   all reports its magic rather than a confusing later error;
4. run only the *decoders*: a plain `u32` costs nothing here, since `Format.decode` is not overridden and
   so is never called;
5. drop the values of `init=False` fields (their defaults stand).

Error locations cost nothing on success. The length check is a single comparison; only when it fails does
`_raise_short` work out which field did not fit, and whether a constant that *is* readable is wrong, which
then wins: `b"HTTP/1.1 ..."` is not a short frame, it is not a frame at all.

## Building

A `_plain_build` record builds with one `pack` of the attrgetter's tuple:

```text
run.struct.pack(*getter(self))
```

Otherwise, `_build_fixed` compares all the constants at once (one attrgetter call, one tuple comparison;
only on a mismatch does it look for the field to blame), runs only the *encoders*, and packs each run.
`struct.error` and `OverflowError` become `BinaryBuildError`. Constant fields are never encoded: they were
just checked to equal the constant.

## Codec fields

A record with codec fields always takes the general path. `read` unpacks the fixed-size fields as above,
moves the reader past `SIZE` bytes, then calls each codec field's `codec.read(reader)` in order and passes
everything to `__init__`. `build` joins the fixed-size bytes with each `codec.build(value)`. An error
inside a codec field gets a `Record.field` step in its path.

A record class has `read` (a classmethod) and `build` (called as `Record.build(record)`), so it satisfies
the `Codec` protocol itself. That is why records nest in combinators and in each other.

## Codecs

The combinators are deliberately small. `Prefixed` does not allocate a sub-reader: it narrows the
reader's `end` for the inner codec, restores it afterwards (in a `finally`), and moves to the end of the
prefixed bytes. `Aligned` keeps a precomputed table of padding byte strings. `Tuple` special-cases the
common 2-tuple (a `(key, value)` pair) to avoid a `join`. `Switch` precomputes each case's display name for
error paths. The scalars *are* `struct.Struct` instances, so `reader.unpack_one(U32_LE)` needs no wrapper.

`ByteReader` converts its input to `bytes` once and never copies again: `sub()` returns a reader over the
same `data` with a narrower `end`, and every read checks against that `end`.

## Why it is fast

The guiding rule is to move work from parse time to class-definition time, and to make the common case
the cheapest:

- Most of the work happens inside `struct`'s C code: one `unpack_from` per byte-order run, not one call
  per field.
- Conversions are opt-in. A field costs a Python call only if its format overrides `decode`/`encode`.
- There is no dynamic context. construct evaluates `this` expressions against a context and builds a
  `Container` for every struct it parses; bytestruct has neither, and its records are ordinary
  dataclasses.
- Error reporting is lazy: locations are computed when something fails, not tracked while parsing.

## Benchmark

A fixed-size header (a `u32` magic, two `u16`, a `u32` and a `u64`, little-endian) and a list of 100
length-prefixed NUL-terminated strings, parsed and built with bytestruct and with construct (both
interpreted and `compile()`d). For the header parse, a hand-written `struct.unpack_from` into a plain
dataclass is the floor.

Measured on an Apple M5 Pro (macOS 27.0.1), CPython 3.12.13, construct 2.10.70, best of 7 repeats; the
ratios are relative to bytestruct:

| Case | bytestruct | construct | construct, compiled | struct + dataclass |
| --- | ---: | ---: | ---: | ---: |
| header parse | 0.30 µs | 2.14 µs (7.2x) | 0.85 µs (2.9x) | 0.15 µs (0.5x) |
| header build | 0.16 µs | 2.04 µs (12.8x) | 1.15 µs (7.2x) | |
| 100 strings parse | 26.5 µs | 133.7 µs (5.0x) | 84.3 µs (3.2x) | |
| 100 strings build | 11.5 µs | 47.2 µs (4.1x) | 41.5 µs (3.6x) | |

In this benchmark bytestruct is 4-13x faster than interpreted construct and 3-7x faster than compiled
construct. Hand-written `struct` code is still twice as fast for one small header: the header's `Const`
takes it off the `_plain` path, and a record parse makes a few Python-level calls that a bare
`unpack_from` does not. That gap is the price of the declarative layer, paid for typed fields, constant
checks and located errors.

The script (needs `pip install bytestruct construct`):

<!-- docs-test: skip -->
```python
"""bytestruct vs construct: parse and build a fixed-size header and a list of strings."""

import platform
import struct
import timeit
from dataclasses import dataclass, field
from typing import Annotated

import construct as c

from bytestruct import CSTRING, U32_LE, BinaryStruct, Const, Prefixed, PrefixedArray, u16, u32, u64


@dataclass
class Header(BinaryStruct, byte_order="<"):
    magic: Annotated[u32, Const(0xFEEDFACE)] = field(default=0xFEEDFACE, init=False)
    major: u16
    minor: u16
    flags: u32
    size: u64


CHeader = c.Struct(
    "magic" / c.Const(0xFEEDFACE, c.Int32ul),
    "major" / c.Int16ul,
    "minor" / c.Int16ul,
    "flags" / c.Int32ul,
    "size" / c.Int64ul,
)

NAMES = PrefixedArray(U32_LE, Prefixed(U32_LE, CSTRING))
CNames = c.PrefixedArray(c.Int32ul, c.Prefixed(c.Int32ul, c.CString("utf8")))

# The floor: a hand-written struct.unpack_from into a plain dataclass.
RAW = struct.Struct("<IHHIQ")


@dataclass
class RawHeader:
    major: int
    minor: int
    flags: int
    size: int


def raw_parse(data):
    magic, *values = RAW.unpack_from(data)
    if magic != 0xFEEDFACE:
        raise ValueError(magic)
    return RawHeader(*values)


header = Header(1, 2, 3, 4)
data = header.build()
assert CHeader.build({"major": 1, "minor": 2, "flags": 3, "size": 4}) == data
cheader = CHeader.parse(data)
names = [f"name-{i}" for i in range(100)]
names_data = NAMES.build(names)
assert CNames.build(names) == names_data

cases = {
    "header parse": {
        "struct + dataclass": lambda: raw_parse(data),
        "bytestruct": lambda: Header.parse(data),
        "construct": lambda: CHeader.parse(data),
        "construct (compiled)": (lambda p: lambda: p(data))(CHeader.compile().parse),
    },
    "header build": {
        "bytestruct": lambda: header.build(),
        "construct": lambda: CHeader.build(cheader),
        "construct (compiled)": (lambda b: lambda: b(cheader))(CHeader.compile().build),
    },
    "100 strings parse": {
        "bytestruct": lambda: NAMES.parse(names_data),
        "construct": lambda: CNames.parse(names_data),
        "construct (compiled)": (lambda p: lambda: p(names_data))(CNames.compile().parse),
    },
    "100 strings build": {
        "bytestruct": lambda: NAMES.build(names),
        "construct": lambda: CNames.build(names),
        "construct (compiled)": (lambda b: lambda: b(names))(CNames.compile().build),
    },
}

print(f"Python {platform.python_version()}, construct {c.version_string}, {platform.machine()}")
for case, runners in cases.items():
    number = 20_000 if case.startswith("header") else 1_000
    timings = {name: min(timeit.repeat(fn, number=number, repeat=7)) / number * 1e6 for name, fn in runners.items()}
    for name, usec in timings.items():
        print(f"{case:18} {name:21} {usec:8.2f} us  {usec / timings['bytestruct']:5.1f}x")
```
