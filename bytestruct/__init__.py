"""Declarative binary records and composable codecs, on the standard library alone.

Fixed-size records are declared as dataclasses deriving from :class:`BinaryStruct`, with each field's wire
type given by its annotation::

    @dataclass
    class Header(BinaryStruct, byte_order="<"):
        magic: Annotated[u32, Const(0x1F3D5B79)] = field(default=0x1F3D5B79, init=False)
        size: u32
        name: Annotated[str, PaddedString(16)]
        kind: Annotated[SomeIntEnum, u16]  # parsed into the enum type

    header = Header.parse(data)  # short input -> TruncatedDataError, wrong magic -> UnexpectedConstantError
    data = header.build()  # an unencodable value or a wrong constant -> BinaryBuildError

The layout is compiled into ``struct.Struct`` objects once, when the class is defined, so a parse is one
``unpack_from`` per byte-order run. Wire fields are positional ``__init__`` parameters in wire order; a field
declared ``= field(default=..., init=False)`` is left out of ``__init__``: parse skips its bytes (padding) and
build packs its default (on a ``Const`` field, the constant). Fields without a wire type may follow the wire
fields and are ignored by parse/build. Records cannot be subclassed.

Variable-length values are described by codecs, which compose like construct's::

    STRING = Aligned(4, Prefixed(U32_LE, CSTRING))  # u32 length, NUL-terminated UTF-8, padded to 4 bytes
    OBJECT = Switch(U32_LE, {1: STRING, 2: U64_LE})  # parses to (tag, value)

A codec is anything with ``read(reader)`` and ``build(value)``: the :class:`Codec` combinators, the scalars
(``U32_LE``, ...), ``Bytes(n)``, and record classes themselves. A record field annotated with a codec
(``name: Annotated[str, CSTRING]``) makes the record variable-length; such fields follow the fixed-size ones.
Formats with no declarative shape are read by hand with :class:`ByteReader`.
"""

import dataclasses
import enum
import ipaddress
import itertools
import operator
import struct
import sys
import uuid
import weakref
from collections.abc import Awaitable, Mapping, Sequence
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Generic,
    NoReturn,
    Optional,
    Protocol,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

if sys.version_info >= (3, 11):
    from typing import Self
else:  # only installed below 3.11, where pyright still finds its bundled stub
    from typing_extensions import Self  # pyright: ignore[reportMissingModuleSource]

from bytestruct.exceptions import (
    BinaryBuildError,
    BinaryDataError,
    BinaryParseError,
    TruncatedDataError,
    UnexpectedConstantError,
)

# The module's sections, in order:
#   ByteReader      a cursor over a buffer, for formats read by hand
#   Codec protocol  ``Codec``/``CodecBase``, the ``Scalar`` codecs (``U32_LE``, ...) and the combinators
#                   (``CString``, ``Prefixed``, ``Aligned``, ``PrefixedArray``, ``Tuple``, ``Switch``, ...)
#   Field formats   ``Format`` and its subclasses (``Bytes``, ``PaddedString``, ``Uuid``, ``Array``, ...), ``Const``
#                   and the field type aliases (``u8``, ``u32``, ``u32be``, ...)
#   Record compiler ``_layout``/``_runs`` and ``BinaryStruct``, whose ``__init_subclass__`` compiles each record
#                   class (walked through in docs/how-it-works.md)

BytesLike = Union[bytes, bytearray, memoryview]
"""Any buffer the parsers accept: ``bytes``, ``bytearray`` or ``memoryview``."""


class ByteReader:
    """Sequential reader over the window ``data[offset:end]`` of an in-memory buffer; every ``read*``/``unpack*``
    returns exactly what was asked for or raises :class:`TruncatedDataError`.

    Readers made by :meth:`sub` share ``data`` (nothing is copied) and have a narrower ``end``. ``base`` is the
    absolute offset of ``data[0]`` in the outermost buffer, so errors report absolute offsets.
    """

    __slots__ = ("base", "data", "end", "offset")

    def __init__(self, data: BytesLike, offset: int = 0, base: int = 0) -> None:
        self.data = data if type(data) is bytes else bytes(data)
        self.base = base
        self.end = len(self.data)
        if not 0 <= offset <= self.end:  # offsets often come from the wire
            raise BinaryParseError(f"invalid offset {offset} in {self.end} bytes", offset=base)
        self.offset = offset

    @property
    def remaining(self) -> int:
        """The number of bytes left before ``end``."""
        return self.end - self.offset

    @property
    def absolute(self) -> int:
        """The current position as an absolute offset in the outermost buffer."""
        return self.base + self.offset

    def _invalid_size(self, size: int) -> BinaryParseError:
        if size < 0:  # a length computed from wire data
            return BinaryParseError(f"invalid size {size}", offset=self.absolute)
        return TruncatedDataError(f"expected {size} bytes, found {self.remaining}", offset=self.absolute)

    def read(self, size: int) -> bytes:
        """Consume and return the next ``size`` bytes."""
        offset = self.offset
        if not 0 <= size <= self.end - offset:
            raise self._invalid_size(size)
        self.offset = offset + size
        return self.data[offset : offset + size]

    def skip(self, size: int) -> None:
        """Consume the next ``size`` bytes without returning them."""
        if not 0 <= size <= self.end - self.offset:
            raise self._invalid_size(size)
        self.offset += size

    def sub(self, size: int) -> "ByteReader":
        """Consume the next ``size`` bytes and return a reader confined to them."""
        start = self.offset
        self.skip(size)
        reader = ByteReader.__new__(ByteReader)
        reader.data, reader.base, reader.offset, reader.end = self.data, self.base, start, start + size
        return reader

    def read_all(self) -> bytes:
        """Consume and return every remaining byte, up to ``end``."""
        return self.read(self.end - self.offset)

    def unpack(self, fmt: struct.Struct) -> tuple[Any, ...]:
        """Consume ``fmt.size`` bytes and return all the values ``fmt`` unpacks from them."""
        offset = self.offset
        if fmt.size > self.end - offset:
            raise self._invalid_size(fmt.size)
        self.offset = offset + fmt.size
        return fmt.unpack_from(self.data, offset)

    def unpack_one(self, fmt: struct.Struct) -> Any:
        """Consume ``fmt.size`` bytes and return the single value ``fmt`` unpacks from them."""
        offset = self.offset
        if fmt.size > self.end - offset:
            raise self._invalid_size(fmt.size)
        self.offset = offset + fmt.size
        return fmt.unpack_from(self.data, offset)[0]

    def read_cstring(self) -> bytes:
        """Read up to (and consume) a NUL terminator; the terminator is not returned."""
        end = self.data.find(b"\0", self.offset, self.end)
        if end == -1:
            raise TruncatedDataError("unterminated C string", offset=self.absolute)
        result = self.data[self.offset : end]
        self.offset = end + 1
        return result


class _OpenEnd(int):
    """The ``end`` of a :class:`_StreamBuffer`: the end of the bytes read so far from a stream, which continues
    past it. :class:`Prefixed` narrows a reader by assigning a plain ``int`` end, so a bounded window (which more
    data cannot help) is told apart from the stream's open end without a check on the parsing paths."""

    __slots__ = ()


class _StreamBuffer(ByteReader):
    """The bytes read so far from a stream.

    A read past its open end raises :class:`TruncatedDataError` with ``needed`` set, so that ``parse_stream``
    reads exactly the missing bytes and parses again. Inside a bounded window (a :class:`Prefixed`, or a reader
    made by :meth:`sub`), reads behave as usual.
    """

    __slots__ = ()

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.base = 0
        self.offset = 0
        self.end = _OpenEnd(len(data))

    def _invalid_size(self, size: int) -> BinaryParseError:
        if size < 0 or type(self.end) is not _OpenEnd:
            return super()._invalid_size(size)
        # ``parse_stream`` reads the missing bytes and parses again, so this error is never shown.
        error = TruncatedDataError("more stream data needed")
        error.needed = size - (self.end - self.offset)
        return error

    def read_all(self) -> bytes:
        if type(self.end) is _OpenEnd:
            # The rest of a stream has no known end: reading it would stop wherever the bytes read so far stop.
            raise BinaryParseError("a greedy value cannot be parsed from a stream; prefix it with its length")
        return super().read_all()

    def read_cstring(self) -> bytes:
        try:
            return super().read_cstring()
        except TruncatedDataError as e:
            if type(self.end) is _OpenEnd:
                e.needed = 1  # the terminator's position is unknown, so read on a byte at a time
            raise


T = TypeVar("T")


class Codec(Protocol[T]):
    """Parses one wire value from a :class:`ByteReader` (``read``) and encodes one (``build``).

    Anything with these two methods is a codec: the combinators below, the scalars (``U32_LE``, ...), ``Bytes(n)``,
    and :class:`BinaryStruct` record classes. Errors raised inside a nested value record the enclosing cases,
    elements and fields in :attr:`BinaryDataError.path`.
    """

    # Attributes rather than methods, so that a record class (whose ``build`` is an instance method, called as
    # ``Record.build(record)``) is a codec too.
    @property
    def read(self) -> Callable[[ByteReader], T]: ...

    @property
    def build(self) -> Callable[[T], bytes]: ...


class CodecBase(Generic[T]):
    """Base of the combinators: a codec with the ``parse``, ``parse_stream`` and ``parse_stream_async`` shortcuts."""

    def read(self, reader: ByteReader) -> T:
        """Parse one value at the reader's offset and advance past it."""
        raise NotImplementedError

    def build(self, value: T) -> bytes:
        """Encode ``value`` (raising :class:`BinaryBuildError` when it cannot be encoded)."""
        raise NotImplementedError

    def parse(self, data: BytesLike) -> T:
        """Parse the value from the start of ``data``; trailing bytes are ignored."""
        return self.read(ByteReader(data))

    def parse_stream(self, read: Callable[[int], BytesLike]) -> T:
        """Parse one value from a stream, consuming exactly its bytes.

        ``read(size)`` returns up to ``size`` bytes, and ``b""`` at the end of the stream (e.g. a binary file's
        ``read``). The value is parsed from the bytes read so far; while it extends past them, exactly the missing
        bytes are read and it is parsed again, so the stream is left right after the value. A value that needs more
        than the stream holds raises :class:`TruncatedDataError`. A greedy value (``GREEDY_BYTES``, ...) has no end
        in a stream, so it can only be parsed inside a :class:`Prefixed`. A C string is read on a byte at a time.

        It is slower than reading the bytes by hand and calling :meth:`parse`: a value with a variable part is
        parsed at least twice (see the Streams guide for measurements).
        """
        return _parse_stream(self, read)

    async def parse_stream_async(self, read: Callable[[int], Awaitable[BytesLike]]) -> T:
        """:meth:`parse_stream` for an asynchronous ``read``, such as ``asyncio.StreamReader.readexactly``.

        ``await read(size)`` returns up to ``size`` bytes (``readexactly`` returns all of them, or raises
        ``IncompleteReadError``, which is left to propagate), and ``b""`` at the end of the stream.
        """
        return await _parse_stream_async(self, read)


class Scalar(struct.Struct):
    """A ``struct.Struct`` holding a single value, usable as a codec and with ``ByteReader.unpack_one``."""

    def read(self, reader: ByteReader) -> Any:
        """Parse the value at the reader's offset and advance past it."""
        return reader.unpack_one(self)

    def parse(self, data: BytesLike) -> Any:
        """Parse the value from the start of ``data``; trailing bytes are ignored."""
        return self.read(ByteReader(data))

    def parse_stream(self, read: Callable[[int], BytesLike]) -> Any:
        """Parse the value from a stream and leave the stream right after it, like :meth:`CodecBase.parse_stream`."""
        return _parse_stream(self, read)

    async def parse_stream_async(self, read: Callable[[int], Awaitable[BytesLike]]) -> Any:
        """:meth:`parse_stream` for an asynchronous ``read``, like :meth:`CodecBase.parse_stream_async`."""
        return await _parse_stream_async(self, read)

    def build(self, value: Any) -> bytes:
        """Pack ``value``; out-of-range or wrongly-typed values raise :class:`BinaryBuildError`."""
        try:
            return self.pack(value)
        except (struct.error, OverflowError) as e:
            raise BinaryBuildError(f"cannot encode {value!r}: {e}") from e


U8 = Scalar("B")
"""An unsigned 8-bit integer."""
U16_LE = Scalar("<H")
"""A little-endian unsigned 16-bit integer."""
U16_BE = Scalar(">H")
"""A big-endian unsigned 16-bit integer."""
U32_LE = Scalar("<I")
"""A little-endian unsigned 32-bit integer."""
U32_BE = Scalar(">I")
"""A big-endian unsigned 32-bit integer."""
U64_LE = Scalar("<Q")
"""A little-endian unsigned 64-bit integer."""
U64_BE = Scalar(">Q")
"""A big-endian unsigned 64-bit integer."""
I16_LE = Scalar("<h")
"""A little-endian signed 16-bit integer."""
I16_BE = Scalar(">h")
"""A big-endian signed 16-bit integer."""
I32_LE = Scalar("<i")
"""A little-endian signed 32-bit integer."""
I32_BE = Scalar(">i")
"""A big-endian signed 32-bit integer."""
I64_LE = Scalar("<q")
"""A little-endian signed 64-bit integer."""
I64_BE = Scalar(">q")
"""A big-endian signed 64-bit integer."""
F32_LE = Scalar("<f")
"""A little-endian IEEE 754 single."""
F32_BE = Scalar(">f")
"""A big-endian IEEE 754 single."""
F64_LE = Scalar("<d")
"""A little-endian IEEE 754 double."""
F64_BE = Scalar(">d")
"""A big-endian IEEE 754 double."""


class Pass(CodecBase[None]):
    """No bytes; parses to ``None``."""

    def read(self, reader: ByteReader) -> None:
        """Parse one value at the reader's offset and advance past it."""
        return None

    def build(self, value: None) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        return b""


class GreedyBytes(CodecBase[bytes]):
    """Every remaining byte (of the enclosing :class:`Prefixed`, or of the buffer)."""

    def read(self, reader: ByteReader) -> bytes:
        """Parse one value at the reader's offset and advance past it."""
        return reader.read_all()

    def build(self, value: BytesLike) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        return bytes(value)


class CString(CodecBase[str]):
    """A NUL-terminated UTF-8 string. Building one with an embedded NUL (the peer would stop there) is an error.

    ``errors`` is the UTF-8 decoding error handler (e.g. ``"replace"`` for names cut mid-character).
    """

    def __init__(self, errors: str = "strict") -> None:
        self.errors = errors

    def read(self, reader: ByteReader) -> str:
        """Parse one value at the reader's offset and advance past it."""
        start = reader.absolute
        raw = reader.read_cstring()
        try:
            return raw.decode("utf-8", self.errors)
        except UnicodeDecodeError as e:
            raise BinaryParseError(f"invalid UTF-8 in C string: {e}", offset=start) from e

    def build(self, value: str) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        if "\0" in value:
            raise BinaryBuildError(f"embedded NUL in {value!r}")
        try:
            return value.encode("utf-8") + b"\0"
        except UnicodeEncodeError as e:
            raise BinaryBuildError(f"cannot encode {value!r}: {e}") from e


class GreedyString(CodecBase[str]):
    """Every remaining byte (of the enclosing :class:`Prefixed`, or of the buffer), as a UTF-8 string.

    ``Prefixed(U16_BE, GREEDY_STRING)`` is a length-prefixed string (construct's ``PascalString``). ``errors``
    is the UTF-8 decoding error handler (e.g. ``"replace"``).
    """

    def __init__(self, errors: str = "strict") -> None:
        self.errors = errors

    def read(self, reader: ByteReader) -> str:
        """Parse one value at the reader's offset and advance past it."""
        start = reader.absolute
        try:
            return reader.read_all().decode("utf-8", self.errors)
        except UnicodeDecodeError as e:
            raise BinaryParseError(f"invalid UTF-8 in string: {e}", offset=start) from e

    def build(self, value: str) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        try:
            return value.encode("utf-8")
        except UnicodeEncodeError as e:
            raise BinaryBuildError(f"cannot encode {value!r}: {e}") from e


PASS = Pass()
"""The :class:`Pass` codec."""
GREEDY_BYTES = GreedyBytes()
"""The :class:`GreedyBytes` codec."""
GREEDY_STRING = GreedyString()
"""A strict :class:`GreedyString` codec."""
CSTRING = CString()
"""A strict :class:`CString` codec."""


class Prefixed(CodecBase[T]):
    """``inner``, confined to the byte count that ``length`` precedes it with."""

    def __init__(self, length: Scalar, inner: Codec[T]) -> None:
        self.length = length
        self.inner = inner

    def read(self, reader: ByteReader) -> T:
        """Parse one value at the reader's offset and advance past it."""
        size = reader.unpack_one(self.length)
        end = reader.end
        stop = reader.offset + size
        if not reader.offset <= stop <= end:
            raise reader._invalid_size(size)
        reader.end = stop  # cheaper than a sub-reader
        try:
            return self.inner.read(reader)
        finally:
            reader.end = end
            reader.offset = stop

    def build(self, value: T) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        body = self.inner.build(value)
        try:
            return self.length.pack(len(body)) + body
        except struct.error as e:
            raise BinaryBuildError(f"cannot encode a length of {len(body)}: {e}") from e


class Aligned(CodecBase[T]):
    """``inner``, followed by NUL padding to a multiple of ``modulus`` bytes counted from where it starts."""

    def __init__(self, modulus: int, inner: Codec[T]) -> None:
        self.modulus = modulus
        self.inner = inner
        self._padding = [bytes(size) for size in range(modulus)]

    def read(self, reader: ByteReader) -> T:
        """Parse one value at the reader's offset and advance past it."""
        start = reader.offset
        value = self.inner.read(reader)
        reader.skip(-(reader.offset - start) % self.modulus)
        return value

    def build(self, value: T) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        data = self.inner.build(value)
        return data + self._padding[-len(data) % self.modulus]


class PrefixedArray(CodecBase[list[T]]):
    """An element count in ``count``, followed by that many ``element``s."""

    def __init__(self, count: Scalar, element: Codec[T]) -> None:
        self.count = count
        self.element = element

    def read(self, reader: ByteReader) -> list[T]:
        """Parse one value at the reader's offset and advance past it."""
        count = reader.unpack_one(self.count)
        read = self.element.read
        result: list[T] = []
        try:
            for _ in range(count):
                result.append(read(reader))
        except BinaryDataError as e:
            e.within(f"[{len(result)}]")
            raise
        return result

    def build(self, value: list[T]) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        build = self.element.build
        parts = [self.count.build(len(value))]
        for index, element in enumerate(value):
            try:
                parts.append(build(element))
            except BinaryDataError as e:
                e.within(f"[{index}]")
                raise
        return b"".join(parts)


class Tuple(CodecBase[tuple[Any, ...]]):
    """Consecutive values of different codecs, parsed into a tuple."""

    def __init__(self, *codecs: Codec[Any]) -> None:
        self.codecs = codecs

    def read(self, reader: ByteReader) -> tuple[Any, ...]:
        """Parse one value at the reader's offset and advance past it."""
        return tuple([codec.read(reader) for codec in self.codecs])

    def build(self, value: tuple[Any, ...]) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        codecs = self.codecs
        if len(value) != len(codecs):
            raise BinaryBuildError(f"expected {len(codecs)} values, got {len(value)}")
        if len(codecs) == 2:  # the common (key, value) pair, without a join
            return codecs[0].build(value[0]) + codecs[1].build(value[1])
        return b"".join([codec.build(item) for codec, item in zip(codecs, value)])


class Switch(CodecBase[tuple[Any, Any]]):
    """A ``tag``, followed by a value in the codec it selects from ``cases``; parses to ``(tag, value)``.

    A tag without a case uses ``default``, or is an error when there is none.
    """

    def __init__(self, tag: Scalar, cases: Mapping[Any, Codec[Any]], *, default: Optional[Codec[Any]] = None) -> None:
        self.tag = tag
        self.cases = dict(cases)
        self.default = default
        self._names = {key: getattr(key, "name", None) or _show(key) for key in self.cases}

    def read(self, reader: ByteReader) -> tuple[Any, Any]:
        """Parse one value at the reader's offset and advance past it."""
        start = reader.absolute
        tag = reader.unpack_one(self.tag)
        case = self.cases.get(tag, self.default)
        if case is None:
            raise BinaryParseError(f"unknown tag {_show(tag)}", offset=start)
        try:
            return tag, case.read(reader)
        except BinaryDataError as e:
            e.within(self._names.get(tag) or _show(tag))
            raise

    def build(self, value: tuple[Any, Any]) -> bytes:
        """Encode ``value``, raising :class:`BinaryBuildError` when it cannot be encoded."""
        tag, item = value
        case = self.cases.get(tag, self.default)
        if case is None:
            raise BinaryBuildError(f"unknown tag {_show(tag)}")
        head = self.tag.build(tag)
        try:
            return head + case.build(item)
        except BinaryDataError as e:
            e.within(self._names.get(tag) or _show(tag))
            raise


class OpenIntEnum(enum.IntEnum):
    """``IntEnum`` that maps unknown values to cached pseudo-members instead of raising, so a value from a newer
    peer (an unknown opcode, status, level, ...) still parses. Declare at least one member."""

    @classmethod
    def _missing_(cls, value: object) -> Optional["OpenIntEnum"]:
        if not isinstance(value, int):
            return None
        member = int.__new__(cls, value)
        member._name_ = str(value)
        member._value_ = value
        # Registered so the next ``cls(value)`` returns the same object.
        return cast(OpenIntEnum, cls._value2member_map_.setdefault(value, member))


class Format:
    """Field annotation metadata: a single-value ``struct`` code, optionally pinned to a byte order.

    ``Annotated[int, Format("H")]`` is how ``u16`` is defined. Subclasses define custom fixed-size field types by
    overriding ``decode`` (unpacked value -> field value, raising :class:`BinaryParseError`) and/or ``encode``
    (the reverse, raising :class:`BinaryBuildError`); only overridden ones are called.
    """

    def __init__(self, code: str, byte_order: Optional[str] = None) -> None:
        self.code = code
        self.byte_order = byte_order

    def decode(self, value: Any) -> Any:
        """Convert the unpacked wire value to the field value."""
        return value

    def encode(self, value: Any) -> Any:
        """Convert the field value to the value to pack."""
        return value


class Bytes(Format):
    """A fixed-size raw byte field; building it from a value of any other length is an error."""

    def __init__(self, size: int) -> None:
        super().__init__(f"{size}s")
        self.size = size

    def encode(self, value: Any) -> Any:
        """Convert the field value to the wire bytes, raising :class:`BinaryBuildError` when it does not fit."""
        if len(value) != self.size:
            raise BinaryBuildError(f"expected {self.size} bytes, got {len(value)}")
        return value

    # As a codec, outside of fixed-size records.
    def read(self, reader: ByteReader) -> bytes:
        """Read the next ``size`` bytes (as a codec, outside of fixed-size records)."""
        return reader.read(self.size)

    def build(self, value: BytesLike) -> bytes:
        """Encode ``value``, which must be exactly ``size`` bytes long (as a codec)."""
        return bytes(self.encode(value))


class PaddedString(Format):
    """A fixed-size field holding a NUL-padded UTF-8 string of at most ``size`` bytes.

    A string that fills the field has no terminator; a shorter one ends at its first NUL, and the bytes after it
    are ignored. ``errors`` is the UTF-8 decoding error handler (e.g. ``"replace"`` for names cut mid-character).
    Building NUL-pads the encoded string to the field size.
    """

    def __init__(self, size: int, errors: str = "strict") -> None:
        super().__init__(f"{size}s")
        self.size = size
        self.errors = errors

    def decode(self, value: Any) -> str:
        """Decode the bytes before the first NUL (the whole field when there is none)."""
        end = value.find(b"\0")
        try:
            return (value if end == -1 else value[:end]).decode("utf-8", self.errors)
        except UnicodeDecodeError as e:
            raise BinaryParseError(f"invalid UTF-8 in string field: {e}") from e

    def encode(self, value: Any) -> bytes:
        """Encode the string, raising :class:`BinaryBuildError` when it cannot be encoded, does not fit or contains a
        NUL byte."""
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as e:
            raise BinaryBuildError(f"cannot encode {value!r}: {e}") from e
        if b"\0" in encoded:  # parse would stop there
            raise BinaryBuildError("string contains a NUL byte")
        if len(encoded) > self.size:
            raise BinaryBuildError(f"string of {len(encoded)} bytes does not fit its {self.size}-byte field")
        return encoded


class Uuid(Format):
    """A 16-byte UUID field, parsed into a :class:`uuid.UUID`."""

    def __init__(self) -> None:
        super().__init__("16s")

    def decode(self, value: Any) -> uuid.UUID:
        """Parse the 16 wire bytes into a :class:`uuid.UUID`."""
        return uuid.UUID(bytes=value)

    def encode(self, value: Any) -> bytes:
        """Return the UUID's 16 bytes."""
        return value.bytes


class IPAddress(Format):
    """A packed IPv4 (4-byte) or IPv6 (16-byte) address field, parsed into an :mod:`ipaddress` object."""

    def __init__(self, version: int) -> None:
        self.address_type = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
        super().__init__("4s" if version == 4 else "16s")

    def decode(self, value: Any) -> Union[ipaddress.IPv4Address, ipaddress.IPv6Address]:
        """Parse the packed wire bytes into an :mod:`ipaddress` address."""
        return self.address_type(value)

    def encode(self, value: Any) -> bytes:
        """Return the address's packed bytes."""
        return value.packed


class Array(Format):
    """A fixed-length array of a plain number type, parsed into a list:
    ``Annotated[list[int], Array(u64, 6)]``. Elements use the record's byte order unless their alias pins one."""

    def __init__(self, element: Any, count: int, byte_order: str = "<") -> None:
        fmt: Format = get_args(element)[1]
        self.element = element
        self.count = count
        self.pinned = fmt.byte_order is not None
        self.struct = struct.Struct(f"{fmt.byte_order or byte_order}{count}{fmt.code}")
        super().__init__(f"{self.struct.size}s")

    def in_record(self, byte_order: str) -> "Array":
        """This array with unpinned elements in the enclosing record's ``byte_order``."""
        return self if self.pinned else Array(self.element, self.count, byte_order)

    def decode(self, value: Any) -> list[Any]:
        """Unpack the wire bytes into a list of ``count`` numbers."""
        return list(self.struct.unpack(value))

    def encode(self, value: Any) -> bytes:
        """Convert the field value to the wire bytes, raising :class:`BinaryBuildError` when it does not fit."""
        if len(value) != self.count:
            raise BinaryBuildError(f"expected {self.count} elements, got {len(value)}")
        try:
            return self.struct.pack(*value)
        except (struct.error, OverflowError) as e:
            raise BinaryBuildError(f"cannot encode: {e}") from e


class Const:
    """Field annotation metadata: the field must always hold ``value``.

    A wrong value raises :class:`UnexpectedConstantError` on parse and :class:`BinaryBuildError` on build. Only for
    fields whose value is not converted (numbers, ``Bytes``, int enums): parse compares the raw unpacked value.
    """

    def __init__(self, value: Any) -> None:
        self.value = value


# Field types: ``Annotated[<what type checkers see>, <wire format>]``. The byte order comes from the record unless
# the alias pins one.
u8 = Annotated[int, Format("B")]
"""An unsigned 8-bit integer field."""
u16 = Annotated[int, Format("H")]
"""An unsigned 16-bit integer field, in the record's byte order."""
u32 = Annotated[int, Format("I")]
"""An unsigned 32-bit integer field, in the record's byte order."""
u64 = Annotated[int, Format("Q")]
"""An unsigned 64-bit integer field, in the record's byte order."""
i16 = Annotated[int, Format("h")]
"""A signed 16-bit integer field, in the record's byte order."""
i32 = Annotated[int, Format("i")]
"""A signed 32-bit integer field, in the record's byte order."""
i64 = Annotated[int, Format("q")]
"""A signed 64-bit integer field, in the record's byte order."""
u32le = Annotated[int, Format("I", "<")]
"""An unsigned 32-bit integer field, always little-endian."""
u32be = Annotated[int, Format("I", ">")]
"""An unsigned 32-bit integer field, always big-endian."""
i8 = Annotated[int, Format("b")]
"""A signed 8-bit integer field."""
f32 = Annotated[float, Format("f")]
"""An IEEE 754 single-precision float field, in the record's byte order."""
f64 = Annotated[float, Format("d")]
"""An IEEE 754 double-precision float field, in the record's byte order."""
u16le = Annotated[int, Format("H", "<")]
"""An unsigned 16-bit integer field, always little-endian."""
u16be = Annotated[int, Format("H", ">")]
"""An unsigned 16-bit integer field, always big-endian."""
u64le = Annotated[int, Format("Q", "<")]
"""An unsigned 64-bit integer field, always little-endian."""
u64be = Annotated[int, Format("Q", ">")]
"""An unsigned 64-bit integer field, always big-endian."""
i16le = Annotated[int, Format("h", "<")]
"""A signed 16-bit integer field, always little-endian."""
i16be = Annotated[int, Format("h", ">")]
"""A signed 16-bit integer field, always big-endian."""
i32le = Annotated[int, Format("i", "<")]
"""A signed 32-bit integer field, always little-endian."""
i32be = Annotated[int, Format("i", ">")]
"""A signed 32-bit integer field, always big-endian."""
i64le = Annotated[int, Format("q", "<")]
"""A signed 64-bit integer field, always little-endian."""
i64be = Annotated[int, Format("q", ">")]
"""A signed 64-bit integer field, always big-endian."""
f32le = Annotated[float, Format("f", "<")]
"""An IEEE 754 single-precision float field, always little-endian."""
f32be = Annotated[float, Format("f", ">")]
"""An IEEE 754 single-precision float field, always big-endian."""
f64le = Annotated[float, Format("d", "<")]
"""An IEEE 754 double-precision float field, always little-endian."""
f64be = Annotated[float, Format("d", ">")]
"""An IEEE 754 double-precision float field, always big-endian."""
uuid_t = Annotated[uuid.UUID, Uuid()]
"""A 16-byte UUID field, parsed into a :class:`uuid.UUID`."""
ipv4 = Annotated[ipaddress.IPv4Address, IPAddress(4)]
"""A 4-byte IPv4 address field, parsed into an :class:`ipaddress.IPv4Address`."""
ipv6 = Annotated[ipaddress.IPv6Address, IPAddress(6)]
"""A 16-byte IPv6 address field, parsed into an :class:`ipaddress.IPv6Address`."""


class _EnumFormat(Format):
    """Converts a parsed integer to the field's int-valued enum (``Annotated[SomeEnum, u32]``)."""

    def __init__(self, base: Format, enum_type: type[enum.Enum]) -> None:
        super().__init__(base.code, base.byte_order)
        self.enum_type = enum_type
        # The enum's own value -> member map: a lookup costs a fraction of ``enum_type(value)``. Values that are
        # not in it (unknown ones, an ``OpenIntEnum``'s first sighting of one, combined flags) take the call.
        self._members = enum_type._value2member_map_

    def decode(self, value: Any) -> Any:
        """Convert the unpacked integer to the enum, raising :class:`BinaryParseError` for an unknown value."""
        member = self._members.get(value)
        if member is not None:
            return member
        try:
            return self.enum_type(value)
        except ValueError as e:
            raise BinaryParseError(str(e)) from e


_NO_CONST = object()  # sentinel: None (or 0) can be a legitimate constant


@dataclasses.dataclass(frozen=True)
class _Field:
    """A fixed-size field, as laid out by :func:`_layout`."""

    index: int  # position among the fixed-size fields
    name: str
    fmt: Format
    struct: struct.Struct  # this field alone: its size, and for locating errors
    offset: int
    const: Any
    init: bool  # an ``__init__`` parameter


@dataclasses.dataclass(frozen=True)
class _CodecField:
    """A variable-length field, read and written by its codec after the fixed-size fields."""

    name: str
    codec: Codec[Any]


@dataclasses.dataclass(frozen=True)
class _Run:
    """Consecutive fields sharing one byte order, (un)packed by a single ``struct.Struct``."""

    struct: struct.Struct
    offset: int
    first: int  # index of the run's first field
    count: int


def _is_codec(item: Any) -> bool:
    """Whether annotation metadata is a codec (see :class:`Codec`)."""
    return hasattr(item, "read") and hasattr(item, "build")


def _wire_format(hint: Any) -> tuple[Optional[Format], Any, Optional[Codec[Any]]]:
    """The fixed-size format, constant and codec of a field annotation (all absent for a field without a wire type).

    ``Annotated[u32, Const(1)]`` is flattened by Python; ``Annotated[SomeEnum, u16]`` nests the alias.
    """
    if get_origin(hint) is not Annotated:
        return None, _NO_CONST, None
    base, *metadata = get_args(hint)
    for item in list(metadata):
        if get_origin(item) is Annotated:
            metadata.extend(get_args(item)[1:])
    fmt = next((m for m in reversed(metadata) if isinstance(m, Format)), None)
    if fmt is not None and isinstance(base, type) and issubclass(base, enum.Enum):
        fmt = _EnumFormat(fmt, base)
    const = next((m.value for m in metadata if isinstance(m, Const)), _NO_CONST)
    codec = None if fmt is not None else next((m for m in metadata if _is_codec(m)), None)
    return fmt, const, codec


def _layout(cls: type, byte_order: str) -> tuple[list[_Field], list[_CodecField]]:
    """The record's fixed-size fields (with their offsets) and codec fields, in declaration order.

    Fields without a wire type are skipped; ``Array`` fields take the record's byte order unless pinned.
    """
    # 3.10+ ``__annotations__`` is per class (lazily computed on 3.14); 3.9's falls back to a base class's.
    names = cls.__annotations__ if sys.version_info >= (3, 10) else cls.__dict__.get("__annotations__", {})
    hints = get_type_hints(cls, include_extras=True)  # keeps the Annotated metadata
    fields: list[_Field] = []
    codec_fields: list[_CodecField] = []
    offset = 0
    for name in names:
        fmt, const, codec = _wire_format(hints[name])
        if codec is not None:
            codec_fields.append(_CodecField(name, codec))
            continue
        if fmt is None:  # a trailing field without a wire type
            continue
        if codec_fields:
            raise TypeError(f"{cls.__name__}.{name}: fixed-size fields must precede the codec fields")
        if isinstance(fmt, Array):
            fmt = fmt.in_record(byte_order)
        field_struct = struct.Struct((fmt.byte_order or byte_order) + fmt.code)
        # ``@dataclass(slots=True)`` recreates the class without the ``field()`` specs in its namespace (the new class
        # is compiled again); its ``__dataclass_fields__`` still has them.
        spec = cls.__dict__.get("__dataclass_fields__", {}).get(name) or cls.__dict__.get(name)
        init = not isinstance(spec, dataclasses.Field) or spec.init
        fields.append(_Field(len(fields), name, fmt, field_struct, offset, const, init))
        offset += field_struct.size
    return fields, codec_fields


def _runs(fields: list[_Field]) -> list[_Run]:
    """Group consecutive same-byte-order fields into one ``struct.Struct`` each (usually one per record)."""
    runs: list[_Run] = []
    for order, group in itertools.groupby(fields, key=lambda f: f.struct.format[0]):
        members = list(group)
        fmt = struct.Struct(order + "".join(f.struct.format[1:] for f in members))
        runs.append(_Run(fmt, members[0].offset, members[0].index, len(members)))
    return runs


def _overrides(fmt: Format, method: str) -> bool:
    """Whether the format's class overrides ``Format.decode``/``encode``; only overriding fields are converted."""
    return getattr(type(fmt), method) is not getattr(Format, method)


def _tuple_getter(names: list[str]) -> Callable[[Any], tuple[Any, ...]]:
    """``operator.attrgetter`` that always returns a tuple (with one name it returns the bare value)."""
    if not names:
        return lambda obj: ()
    if len(names) == 1:
        single = operator.attrgetter(names[0])
        return lambda obj: (single(obj),)
    return operator.attrgetter(*names)


def _tuple_itemgetter(indices: list[int]) -> Callable[[Sequence[Any]], tuple[Any, ...]]:
    """``operator.itemgetter`` that always returns a tuple (with one index it returns the bare value)."""
    if not indices:
        return lambda values: ()
    if len(indices) == 1:
        single = operator.itemgetter(indices[0])
        return lambda values: (single(values),)
    return operator.itemgetter(*indices)


def _show(value: Any) -> str:
    """Integers (magics, versions, tags) read best in hex."""
    return hex(value) if isinstance(value, int) and not isinstance(value, bool) else repr(value)


class BinaryStruct:
    """Base for dataclasses describing a binary record; see the module docstring.

    Record classes are codecs themselves (``read``/``build``), so they nest inside other records and combinators.
    """

    # Computed once per record class by ``__init_subclass__``.
    SIZE: ClassVar[int]  # of the fixed-size fields (all of a record without codec fields)
    _fields: ClassVar[tuple[_Field, ...]]  # in wire order
    _codec_fields: ClassVar[tuple[_CodecField, ...]]
    _runs: ClassVar[tuple[_Run, ...]]
    _consts: ClassVar[tuple[_Field, ...]]
    _const_getter: ClassVar[Callable[[Any], Any]]  # instance -> its constant fields' values
    _const_values: ClassVar[Any]  # what ``_const_getter`` must return
    _decoders: ClassVar[tuple[_Field, ...]]
    _encoders: ClassVar[tuple[_Field, ...]]  # non-constant fields whose format converts values before packing
    _getter: ClassVar[Callable[[Any], tuple[Any, ...]]]  # instance -> its fixed-size field values
    _init_indices: ClassVar[Optional[list[int]]]  # the fixed-size fields passed to ``__init__``, if not all of them

    def __init_subclass__(cls, byte_order: str = "<", **kwargs: Any) -> None:
        """Compile the record: runs once per class, when the ``class`` statement executes (before ``@dataclass``).

        It lays out the fields, groups them into one ``struct.Struct`` per byte-order run, precomputes the
        constant, decoder and encoder lists, and gives the record dedicated ``parse``/``read``/``build`` when its
        shape allows (:func:`_specialize`). See docs/how-it-works.md for the walkthrough.
        """
        super().__init_subclass__(**kwargs)
        if cls.__bases__ != (BinaryStruct,):
            raise TypeError(f"{cls.__name__}: records cannot be subclassed or mixed with other bases")
        fields, codec_fields = _layout(cls, byte_order)
        cls._fields = tuple(fields)
        cls._codec_fields = tuple(codec_fields)
        cls._runs = tuple(_runs(fields))
        cls._consts = tuple(f for f in fields if f.const is not _NO_CONST)
        cls._decoders = tuple(f for f in fields if _overrides(f.fmt, "decode"))
        # A constant was checked before packing, so it never needs encoding.
        cls._encoders = tuple(f for f in fields if _overrides(f.fmt, "encode") and f.const is _NO_CONST)
        cls.SIZE = sum(f.struct.size for f in fields)
        cls._getter = _tuple_getter([f.name for f in fields])  # pyright: ignore[reportAttributeAccessIssue]
        cls._init_indices = None if all(f.init for f in fields) else [f.index for f in fields if f.init]
        if cls._consts:
            # ``attrgetter`` returns the bare value for one name and a tuple for several; compare likewise.
            cls._const_getter = operator.attrgetter(*(f.name for f in cls._consts))
            consts = [f.const for f in cls._consts]
            cls._const_values = consts[0] if len(consts) == 1 else tuple(consts)
        _specialize(cls)

    @classmethod
    def parse(cls, data: BytesLike) -> Self:
        """Parse the record from the start of ``data``; trailing bytes are ignored."""
        if cls._codec_fields:
            return cls.read(ByteReader(data))
        return cls._parse(data, 0, len(data), 0)

    @classmethod
    def parse_stream(cls, read: Callable[[int], BytesLike]) -> Self:
        """Parse the record from a stream and leave the stream right after it, like :meth:`CodecBase.parse_stream`."""
        return _parse_stream(cls, read)

    @classmethod
    async def parse_stream_async(cls, read: Callable[[int], Awaitable[BytesLike]]) -> Self:
        """:meth:`parse_stream` for an asynchronous ``read``, like :meth:`CodecBase.parse_stream_async`."""
        return await _parse_stream_async(cls, read)

    @classmethod
    def read(cls, reader: ByteReader) -> Self:
        """Parse the record at the reader's offset and advance past it."""
        start = reader.offset
        if not cls._codec_fields:
            record = cls._parse(reader.data, start, reader.end, reader.base)
            reader.offset = start + cls.SIZE
            return record
        values = cls._unpack(reader.data, start, reader.end, reader.base)
        reader.offset = start + cls.SIZE
        for field in cls._codec_fields:
            try:
                values.append(field.codec.read(reader))
            except BinaryDataError as e:
                e.within(f"{cls.__name__}.{field.name}")
                raise
        return cls(*values)

    @classmethod
    def _parse(cls, data: BytesLike, start: int, end: int, base: int) -> Self:
        """Parse at ``data[start:end]``; ``base + start`` is that position's absolute offset (for errors)."""
        return cls(*cls._unpack(data, start, end, base))

    @classmethod
    def _unpack(cls, data: BytesLike, start: int, end: int, base: int) -> list[Any]:
        """The ``__init__`` arguments of the fixed-size fields at ``data[start:end]``."""
        if end - start < cls.SIZE:
            cls._raise_short(data, start, end, base)
        values: list[Any] = []
        for run in cls._runs:
            values.extend(run.struct.unpack_from(data, start + run.offset))
        # Constants first, so a wrong magic is reported as such.
        for field in cls._consts:
            if values[field.index] != field.const:
                raise cls._const_error(field, values[field.index], base + start)
        for field in cls._decoders:
            try:
                values[field.index] = field.fmt.decode(values[field.index])
            except BinaryParseError as e:
                raise e.located(record=cls.__name__, field=field.name, offset=base + start + field.offset) from e
        if cls._init_indices is not None:
            return [values[i] for i in cls._init_indices]
        return values

    @classmethod
    def _raise_short(cls, data: BytesLike, start: int, end: int, base: int) -> NoReturn:
        """A wrong constant that is already readable wins (``b"HTTP/1.1..."`` is not a short frame, it is not
        a frame of this record at all); otherwise the error names the first field that does not fit."""
        available = end - start
        for field in cls._consts:
            if field.offset + field.struct.size <= available:
                actual = field.struct.unpack_from(data, start + field.offset)[0]
                if actual != field.const:
                    raise cls._const_error(field, actual, base + start)
        field = next(f for f in cls._fields if f.offset + f.struct.size > available)
        error = TruncatedDataError(
            f"expected {field.struct.size} bytes, found {max(available - field.offset, 0)}",
            record=cls.__name__,
            field=field.name,
            offset=base + start + field.offset,
        )
        if type(end) is _OpenEnd:
            error.needed = cls.SIZE - available
        raise error

    @classmethod
    def _const_error(cls, field: _Field, actual: Any, record_offset: int) -> UnexpectedConstantError:
        return UnexpectedConstantError(
            f"expected {_show(field.const)}, got {_show(actual)}",
            record=cls.__name__,
            field=field.name,
            offset=record_offset + field.offset,
        )

    def build(self) -> bytes:
        """Encode the record (as a codec, ``Record.build(record)``)."""
        if not self._codec_fields:
            return self._build_fixed()
        parts = [self._build_fixed()]
        for field in self._codec_fields:
            try:
                parts.append(field.codec.build(getattr(self, field.name)))
            except BinaryDataError as e:
                e.within(f"{type(self).__name__}.{field.name}")
                raise
        return b"".join(parts)

    def _build_fixed(self) -> bytes:
        """Encode the fixed-size fields: check the constants, run the encoders, then pack each run."""
        cls = type(self)
        if cls._consts and cls._const_getter(self) != cls._const_values:
            field = next(f for f in cls._consts if getattr(self, f.name) != f.const)
            raise BinaryBuildError(
                f"expected {_show(field.const)}, got {_show(getattr(self, field.name))}",
                record=cls.__name__,
                field=field.name,
            )
        values = cls._getter(self)
        if cls._encoders:
            values = list(values)
            for field in cls._encoders:
                try:
                    values[field.index] = field.fmt.encode(values[field.index])
                except BinaryDataError as e:
                    raise e.located(record=cls.__name__, field=field.name) from e
        try:
            if len(cls._runs) == 1:
                return cls._runs[0].struct.pack(*values)
            return b"".join([run.struct.pack(*values[run.first : run.first + run.count]) for run in cls._runs])
        except (struct.error, OverflowError) as e:
            raise BinaryBuildError(f"cannot encode: {e}", record=cls.__name__) from e


# The functions ``_specialize`` installed. When ``@dataclass(slots=True)`` recreates a record class, the new class
# inherits them in its namespace and gets its own; a ``parse``/``read``/``build`` the record defines is kept.
_specialized: "weakref.WeakSet[Callable[..., Any]]" = weakref.WeakSet()


def _install(cls: type[BinaryStruct], name: str, function: Callable[..., Any], wrap: Callable[[Any], Any]) -> None:
    current = cls.__dict__.get(name)
    if current is not None and getattr(current, "__func__", current) not in _specialized:
        return
    _specialized.add(function)
    setattr(cls, name, wrap(function))


def _specialize(cls: type[BinaryStruct]) -> None:
    """Give a record without codec fields its own ``parse``, ``read`` and ``build``.

    The generic methods decide on every call what the record needs (constants? decoders? ``init=False``
    fields?); these closures have the answers baked in. The commonest shape, one byte-order run without
    decoders, parses with a single ``unpack_from`` into ``__init__``; records with decoders or several runs get
    a closure doing the generic steps without the per-call dispatch. Building is specialized for one run
    without encoders; records with codec fields keep the generic methods.
    """
    if cls._codec_fields:
        return
    size = cls.SIZE
    consts = cls._consts
    # ``itemgetter`` returns the bare value for one index and a tuple for several, like ``_const_values``.
    const_getter = operator.itemgetter(*(f.index for f in consts)) if consts else None
    const_values = cls._const_values if consts else None
    init_indices = cls._init_indices
    select = None if init_indices is None else _tuple_itemgetter(init_indices)

    def check_consts(values: Sequence[Any], record_offset: int) -> None:
        field = next(f for f in consts if values[f.index] != f.const)
        raise cls._const_error(field, values[field.index], record_offset)

    raise_short = cls._raise_short
    if not cls._decoders and len(cls._runs) == 1:
        unpack_from = cls._runs[0].struct.unpack_from

        def parse(data: BytesLike) -> Any:
            try:
                values = unpack_from(data)
            except struct.error:
                raise_short(data, 0, len(data), 0)
            if const_getter is not None and const_getter(values) != const_values:
                check_consts(values, 0)
            return cls(*values) if select is None else cls(*select(values))

        def read(reader: ByteReader) -> Any:
            start = reader.offset
            data = reader.data
            if reader.end - start < size:
                raise_short(data, start, reader.end, reader.base)
            values = unpack_from(data, start)
            if const_getter is not None and const_getter(values) != const_values:
                check_consts(values, reader.base + start)
            record = cls(*values) if select is None else cls(*select(values))
            reader.offset = start + size
            return record

        _install(cls, "parse", parse, staticmethod)
        _install(cls, "read", read, staticmethod)
    else:
        runs = [(r.struct.unpack_from, r.offset) for r in cls._runs]
        decoders = [(f.index, f.fmt.decode, f) for f in cls._decoders]

        def arguments(data: BytesLike, start: int, record_offset: int) -> Any:
            if len(runs) == 1:
                values = list(runs[0][0](data, start))
            else:
                values: list[Any] = []
                for unpack_run, offset in runs:
                    values.extend(unpack_run(data, start + offset))
            if const_getter is not None and const_getter(values) != const_values:
                check_consts(values, record_offset)
            for index, decode, field in decoders:
                try:
                    values[index] = decode(values[index])
                except BinaryParseError as e:
                    raise e.located(record=cls.__name__, field=field.name, offset=record_offset + field.offset) from e
            return values if select is None else select(values)

        def parse_general(data: BytesLike) -> Any:
            if len(data) < size:
                raise_short(data, 0, len(data), 0)
            return cls(*arguments(data, 0, 0))

        def read_general(reader: ByteReader) -> Any:
            start = reader.offset
            if reader.end - start < size:
                raise_short(reader.data, start, reader.end, reader.base)
            record = cls(*arguments(reader.data, start, reader.base + start))
            reader.offset = start + size
            return record

        _install(cls, "parse", parse_general, staticmethod)
        _install(cls, "read", read_general, staticmethod)

    if not cls._encoders and len(cls._runs) == 1:
        pack = cls._runs[0].struct.pack
        getter = cls._getter
        name = cls.__name__

        def build(self: Any) -> bytes:
            values = getter(self)
            if const_getter is not None and const_getter(values) != const_values:
                field = next(f for f in consts if values[f.index] != f.const)
                raise BinaryBuildError(
                    f"expected {_show(field.const)}, got {_show(values[field.index])}", record=name, field=field.name
                )
            try:
                return pack(*values)
            except (struct.error, OverflowError) as e:
                raise BinaryBuildError(f"cannot encode: {e}", record=name) from e

        _install(cls, "build", build, lambda function: function)


# Records' minimum sizes in a stream (see ``_min_size``), computed on first use.
_record_min_sizes: "weakref.WeakKeyDictionary[type, int]" = weakref.WeakKeyDictionary()


def _min_size(codec: Any) -> int:
    """Bytes every value of ``codec`` starts with, whatever their content: what a stream can read up front."""
    if isinstance(codec, type) and issubclass(codec, BinaryStruct):
        size = _record_min_sizes.get(codec)
        if size is None:
            size = _record_min_sizes[codec] = codec.SIZE + sum(_min_size(f.codec) for f in codec._codec_fields)
        return size
    if isinstance(codec, (Scalar, Bytes)):
        return codec.size
    if isinstance(codec, Prefixed):
        return codec.length.size
    if isinstance(codec, PrefixedArray):
        return codec.count.size
    if isinstance(codec, Switch):
        return codec.tag.size
    if isinstance(codec, Aligned):
        return _min_size(cast(Aligned[Any], codec).inner)
    if isinstance(codec, Tuple):
        return sum(_min_size(item) for item in codec.codecs)
    if isinstance(codec, CString):
        return 1  # the terminator
    return 0


def _stream_ended(size: int, offset: int) -> TruncatedDataError:
    return TruncatedDataError(f"stream ended: expected {size} more bytes", offset=offset)


def _parse_stream(codec: Codec[T], read: Callable[[int], BytesLike]) -> T:
    """The implementation of :meth:`CodecBase.parse_stream` (any codec, records included)."""
    data = b""
    size = _min_size(codec)
    while True:
        while size:
            chunk = read(size)
            if not chunk:
                raise _stream_ended(size, len(data))
            data += chunk
            size -= len(chunk)
        try:
            return codec.read(_StreamBuffer(data))
        except TruncatedDataError as e:
            if not e.needed:
                raise
            size = e.needed


async def _parse_stream_async(codec: Codec[T], read: Callable[[int], Awaitable[BytesLike]]) -> T:
    """The implementation of :meth:`CodecBase.parse_stream_async`."""
    data = b""
    size = _min_size(codec)
    while True:
        while size:
            chunk = await read(size)
            if not chunk:
                raise _stream_ended(size, len(data))
            data += chunk
            size -= len(chunk)
        try:
            return codec.read(_StreamBuffer(data))
        except TruncatedDataError as e:
            if not e.needed:
                raise
            size = e.needed


__all__ = [
    "CSTRING",
    "F32_BE",
    "F32_LE",
    "F64_BE",
    "F64_LE",
    "GREEDY_BYTES",
    "GREEDY_STRING",
    "I16_BE",
    "I16_LE",
    "I32_BE",
    "I32_LE",
    "I64_BE",
    "I64_LE",
    "PASS",
    "U8",
    "U16_BE",
    "U16_LE",
    "U32_BE",
    "U32_LE",
    "U64_BE",
    "U64_LE",
    "Aligned",
    "Array",
    "BinaryBuildError",
    "BinaryDataError",
    "BinaryParseError",
    "BinaryStruct",
    "ByteReader",
    "Bytes",
    "BytesLike",
    "CString",
    "Codec",
    "CodecBase",
    "Const",
    "Format",
    "GreedyBytes",
    "GreedyString",
    "OpenIntEnum",
    "PaddedString",
    "Pass",
    "Prefixed",
    "PrefixedArray",
    "Scalar",
    "Switch",
    "TruncatedDataError",
    "Tuple",
    "UnexpectedConstantError",
    "f32",
    "f32be",
    "f32le",
    "f64",
    "f64be",
    "f64le",
    "i8",
    "i16",
    "i16be",
    "i16le",
    "i32",
    "i32be",
    "i32le",
    "i64",
    "i64be",
    "i64le",
    "ipv4",
    "ipv6",
    "u8",
    "u16",
    "u16be",
    "u16le",
    "u32",
    "u32be",
    "u32le",
    "u64",
    "u64be",
    "u64le",
    "uuid_t",
]
