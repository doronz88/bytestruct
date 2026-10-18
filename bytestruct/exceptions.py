"""Errors raised while parsing or building binary data."""

from typing import Optional, cast

__all__ = [
    "BinaryBuildError",
    "BinaryDataError",
    "BinaryParseError",
    "TruncatedDataError",
    "UnexpectedConstantError",
]


class BinaryDataError(ValueError):
    """Base for binary wire-format errors: data that could not be parsed, or a value that could not be encoded.

    ``record``, ``field`` and ``offset`` (absolute, in the outermost buffer) locate the failure when known, and
    ``path`` lists the enclosing cases, elements and fields, outermost first. They prefix the message, e.g.
    ``Header.magic at offset 0: expected 0x29b00b92, got 0x0`` or
    ``DICTIONARY -> [2] -> STRING at offset 104: expected 200 bytes, found 16``.
    """

    def __init__(
        self,
        message: str,
        *,
        record: Optional[str] = None,
        field: Optional[str] = None,
        offset: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.record = record
        self.field = field
        self.offset = offset
        self.path: list[str] = []

    def __str__(self) -> str:
        name = ".".join(part for part in (self.record, self.field) if part)
        location = " -> ".join([*self.path, name] if name else self.path)
        if self.offset is not None:
            location = f"{location} at offset {self.offset}" if location else f"at offset {self.offset}"
        return f"{location}: {self.message}" if location else self.message

    def located(
        self, *, record: Optional[str] = None, field: Optional[str] = None, offset: Optional[int] = None
    ) -> "BinaryDataError":
        """Return a copy of this error (same type) with any missing location filled in."""
        error = type(self)(
            self.message,
            record=self.record or record,
            field=self.field or field,
            offset=self.offset if self.offset is not None else offset,
        )
        error.path = list(self.path)
        return error

    def within(self, step: str) -> None:
        """Record that the failure happened inside ``step`` (a case, element or field), as the error propagates."""
        self.path.insert(0, step)


class BinaryParseError(BinaryDataError):
    """Binary wire data could not be parsed."""


class BinaryBuildError(BinaryDataError):
    """A value could not be encoded into its binary wire format."""


class TruncatedDataError(BinaryParseError):
    """Binary wire data ended before the structure being parsed was complete.

    ``needed`` is set while parsing from a stream (``parse_stream``/``parse_stream_async``): the number of bytes
    missing after the end of the data read so far, which the value certainly extends over. It is ``None``
    otherwise, and when more data cannot help (the value is cut short inside a length-prefixed window).
    """

    needed: Optional[int] = None

    def located(
        self, *, record: Optional[str] = None, field: Optional[str] = None, offset: Optional[int] = None
    ) -> "TruncatedDataError":
        """Return a copy of this error (same type) with any missing location filled in."""
        error = cast(TruncatedDataError, super().located(record=record, field=field, offset=offset))
        error.needed = self.needed
        return error


class UnexpectedConstantError(BinaryParseError):
    """A constant field (magic, version, ...) in binary wire data did not have its expected value."""
