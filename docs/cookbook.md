# Cookbook

Complete formats, end to end: declare, build, parse, and handle bad input. Every recipe runs in the test
suite.

```python
import ipaddress
import struct
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import Annotated, Any, Iterator

from bytestruct import (
    CSTRING,
    GREEDY_BYTES,
    GREEDY_STRING,
    U8,
    U16_BE,
    U32_LE,
    BinaryBuildError,
    BinaryDataError,
    BinaryParseError,
    BinaryStruct,
    ByteReader,
    Bytes,
    Const,
    Format,
    PaddedString,
    Prefixed,
    PrefixedArray,
    Switch,
    TruncatedDataError,
    UnexpectedConstantError,
    ipv4,
    u8,
    u16,
    u32,
    u64,
    uuid_t,
)
```

## A file header: magic, version and flags

A container file starts with a fixed header: a magic, a format version, feature flags, the offset and
size of a payload, and a creator name. Unknown flag bits are kept (an `IntFlag` holds any combination);
unknown versions are rejected after parsing.

```python
class Features(IntFlag):
    COMPRESSED = 0x1
    ENCRYPTED = 0x2
    SIGNED = 0x4


@dataclass
class FileHeader(BinaryStruct, byte_order="<"):
    magic: Annotated[bytes, Bytes(4), Const(b"BSF\x1a")] = field(default=b"BSF\x1a", init=False)
    version: u16
    features: Annotated[Features, u16]
    payload_offset: u32
    payload_size: u64
    creator: Annotated[str, PaddedString(16)]
    reserved: Annotated[bytes, Bytes(4)] = field(default=bytes(4), init=False, compare=False)

    SUPPORTED_VERSIONS = (1, 2)


def read_file(data: bytes) -> tuple[FileHeader, bytes]:
    header = FileHeader.parse(data)
    if header.version not in FileHeader.SUPPORTED_VERSIONS:
        raise BinaryParseError(f"unsupported version {header.version}", record="FileHeader", field="version", offset=4)
    payload = ByteReader(data, offset=header.payload_offset).read(header.payload_size)
    return header, payload


header = FileHeader(2, Features.COMPRESSED | Features.SIGNED, FileHeader.SIZE, 5, "bytestruct")
data = header.build() + b"hello"
assert FileHeader.SIZE == 40
assert data[:12].hex(" ") == "42 53 46 1a 02 00 05 00 28 00 00 00"
assert read_file(data) == (header, b"hello")
assert Features.SIGNED in read_file(data)[0].features

try:
    read_file(b"PK\x03\x04" + data[4:])
except UnexpectedConstantError as e:
    assert str(e) == "FileHeader.magic at offset 0: expected b'BSF\\x1a', got b'PK\\x03\\x04'"

try:
    read_file(data[:-1])  # the payload is cut short
except TruncatedDataError as e:
    assert str(e) == "at offset 40: expected 5 bytes, found 4"

try:
    read_file(FileHeader(9, Features(0), 40, 0, "x").build())
except BinaryParseError as e:
    assert str(e) == "FileHeader.version at offset 4: unsupported version 9"
```

`SUPPORTED_VERSIONS` has no annotation, so it is a plain class attribute: neither a dataclass field nor a
wire field.

## A TLV stream

A sequence of type-length-value items until the end of the data: a `u8` type, a big-endian `u16` length,
then the value. Known types are decoded, unknown ones are kept as raw bytes so that a newer peer's items
survive a parse/build round trip.

```python
class ItemType(IntEnum):
    NAME = 1
    PORT = 2


ITEM = Switch(
    U8,
    {
        ItemType.NAME: Prefixed(U16_BE, GREEDY_STRING),
        ItemType.PORT: Prefixed(U16_BE, U16_BE),
    },
    default=Prefixed(U16_BE, GREEDY_BYTES),
)


def parse_items(data: bytes) -> list[tuple[int, Any]]:
    reader = ByteReader(data)
    items = []
    while reader.remaining:
        try:
            items.append(ITEM.read(reader))
        except BinaryDataError as e:
            e.within(f"item {len(items)}")
            raise
    return items


def build_items(items: list[tuple[int, Any]]) -> bytes:
    return b"".join(ITEM.build(item) for item in items)


items = [(ItemType.NAME, "printer"), (ItemType.PORT, 631), (0x42, b"\x01\x02")]
data = build_items(items)
assert data.hex(" ") == "01 00 07 70 72 69 6e 74 65 72 02 00 02 02 77 42 00 02 01 02"
assert parse_items(data) == items
assert parse_items(data)[2] == (0x42, b"\x01\x02")  # unknown type, kept raw

try:
    parse_items(data[:-1])
except TruncatedDataError as e:
    assert str(e) == "item 2 -> 0x42 at offset 18: expected 2 bytes, found 1"
```

`Prefixed` around each value means a value can never run into the next item: a `PORT` whose length says 3
bytes parses its `u16` and skips the third.

## A length-prefixed message protocol

A request/response protocol over a byte stream: every message is a fixed header (magic, type, request id,
body length) followed by the body. The receiver accumulates bytes, waits until a whole message is there,
then dispatches on the type.

```python
class MessageType(IntEnum):
    PING = 1
    ECHO = 2
    ERROR = 3


@dataclass
class MessageHeader(BinaryStruct, byte_order=">"):
    magic: Annotated[u16, Const(0xB5C7)] = field(default=0xB5C7, init=False)
    type: Annotated[MessageType, u8]
    request_id: u32
    length: u32


@dataclass
class ErrorBody(BinaryStruct, byte_order=">"):
    code: u16
    reason: Annotated[str, CSTRING]


BODIES = {MessageType.PING: None, MessageType.ECHO: GREEDY_BYTES, MessageType.ERROR: ErrorBody}


def encode_message(type: MessageType, request_id: int, body: Any = None) -> bytes:
    codec = BODIES[type]
    payload = b"" if codec is None else codec.build(body)
    return MessageHeader(type, request_id, len(payload)).build() + payload


class Receiver:
    """Splits a byte stream into (header, body) messages."""

    def __init__(self) -> None:
        self.buffer = b""

    def feed(self, chunk: bytes) -> Iterator[tuple[MessageHeader, Any]]:
        self.buffer += chunk
        while True:
            try:
                header = MessageHeader.parse(self.buffer)
            except TruncatedDataError:
                return  # not even a whole header yet
            end = MessageHeader.SIZE + header.length
            if len(self.buffer) < end:
                return  # wait for the rest of the body
            body_reader = ByteReader(self.buffer, offset=MessageHeader.SIZE).sub(header.length)
            codec = BODIES[header.type]
            body = None if codec is None else codec.read(body_reader)
            self.buffer = self.buffer[end:]
            yield header, body


stream = (
    encode_message(MessageType.PING, 1)
    + encode_message(MessageType.ECHO, 2, b"hello")
    + encode_message(MessageType.ERROR, 3, ErrorBody(404, "no such object"))
)
assert stream[:11].hex(" ") == "b5 c7 01 00 00 00 01 00 00 00 00"

receiver = Receiver()
received = []
for start in range(0, len(stream), 7):  # arbitrary chunk boundaries
    received.extend(receiver.feed(stream[start : start + 7]))

assert [(header.type, header.request_id, body) for header, body in received] == [
    (MessageType.PING, 1, None),
    (MessageType.ECHO, 2, b"hello"),
    (MessageType.ERROR, 3, ErrorBody(404, "no such object")),
]
assert receiver.buffer == b""

try:
    list(Receiver().feed(b"GET / HTTP/1.1\r\n"))
except UnexpectedConstantError as e:
    assert str(e) == "MessageHeader.magic at offset 0: expected 0xb5c7, got 0x4745"
```

The body reader is a `sub()` window of exactly `length` bytes, so a body codec cannot read into the next
message, and `GREEDY_BYTES` takes exactly the body.

## A nested record with an array of sub-records

A directory listing: a header, then a count-prefixed array of entries, each with a fixed part and a
name. Records nest as codec fields; `PrefixedArray` repeats them.

```python
class EntryKind(IntEnum):
    FILE = 0
    DIRECTORY = 1
    SYMLINK = 2


@dataclass
class Timestamps(BinaryStruct, byte_order="<"):
    created: u64
    modified: u64


@dataclass
class DirEntry(BinaryStruct, byte_order="<"):
    kind: Annotated[EntryKind, u8]
    size: u64
    times: Annotated[Timestamps, Timestamps]
    name: Annotated[str, Prefixed(U8, GREEDY_STRING)]


@dataclass
class Listing(BinaryStruct, byte_order="<"):
    magic: Annotated[bytes, Bytes(4), Const(b"LIST")] = field(default=b"LIST", init=False)
    volume: uuid_t
    entries: Annotated[list[DirEntry], PrefixedArray(U32_LE, DirEntry)]


listing = Listing(
    uuid.UUID(int=0xABC),
    [
        DirEntry(EntryKind.DIRECTORY, 0, Timestamps(1, 2), "docs"),
        DirEntry(EntryKind.FILE, 1234, Timestamps(3, 4), "README.md"),
    ],
)
data = listing.build()
assert Listing.SIZE == 20  # magic + volume; the entries are a codec field
assert DirEntry.SIZE == 9  # kind + size; times and name are codec fields
assert len(data) == 20 + 4 + (9 + 16 + 1 + 4) + (9 + 16 + 1 + 9)
assert Listing.parse(data) == listing
assert Listing.parse(data).entries[1].times.modified == 4

try:
    Listing.parse(data[:-3])
except TruncatedDataError as e:
    assert str(e) == "Listing.entries -> [1] -> DirEntry.name at offset 80: expected 9 bytes, found 6"
```

`times` is a nested record: as a codec field it comes after `size`, the last fixed-size field, even
though it has a fixed size itself.

## Big-endian network packets

An IPv4 header followed by a UDP header and payload, all in network (big-endian) byte order. The 4-bit
version and header-length fields share a byte (a small `Format` subclass), the checksum is computed in
Python over the built bytes, and the UDP length comes from the payload.

```python
class VersionIhl(Format):
    """IPv4's first byte: the version (high nibble) and the header length in 32-bit words (low nibble)."""

    def __init__(self) -> None:
        super().__init__("B")

    def decode(self, value: int) -> tuple[int, int]:
        return value >> 4, value & 0xF

    def encode(self, value: tuple[int, int]) -> int:
        version, ihl = value
        if not (0 <= version <= 0xF and 0 <= ihl <= 0xF):
            raise BinaryBuildError(f"version/IHL out of range: {value}")
        return version << 4 | ihl


def checksum(data: bytes) -> int:
    """The Internet checksum (RFC 1071)."""
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f">{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


@dataclass
class IPv4Header(BinaryStruct, byte_order=">"):
    version_ihl: Annotated[tuple[int, int], VersionIhl()]
    tos: u8
    total_length: u16
    identification: u16
    flags_fragment: u16
    ttl: u8
    protocol: u8
    checksum: u16
    source: ipv4
    destination: ipv4


@dataclass
class UdpHeader(BinaryStruct, byte_order=">"):
    source_port: u16
    destination_port: u16
    length: u16
    checksum: u16


def build_udp_packet(source: str, destination: str, sport: int, dport: int, payload: bytes) -> bytes:
    udp = UdpHeader(sport, dport, UdpHeader.SIZE + len(payload), 0).build() + payload
    ip = IPv4Header(
        (4, IPv4Header.SIZE // 4), 0, IPv4Header.SIZE + len(udp), 0x1234, 0x4000, 64, 17, 0,
        ipaddress.IPv4Address(source), ipaddress.IPv4Address(destination),
    )
    ip.checksum = checksum(ip.build())
    return ip.build() + udp


def parse_udp_packet(data: bytes) -> tuple[IPv4Header, UdpHeader, bytes]:
    reader = ByteReader(data)
    ip = IPv4Header.read(reader)
    version, ihl = ip.version_ihl
    if version != 4 or ihl < 5:
        raise BinaryParseError(f"not an IPv4 header: version {version}, IHL {ihl}", offset=0)
    if checksum(data[: ihl * 4]) != 0:
        raise BinaryParseError("bad IPv4 header checksum", record="IPv4Header", field="checksum", offset=10)
    reader.skip(ihl * 4 - IPv4Header.SIZE)  # IP options
    packet = reader.sub(ip.total_length - ihl * 4)  # ignore link-layer padding after the packet
    udp = UdpHeader.read(packet)
    return ip, udp, packet.read(udp.length - UdpHeader.SIZE)


packet = build_udp_packet("192.168.1.10", "10.0.0.1", 5353, 53, b"query")
assert IPv4Header.SIZE == 20 and len(packet) == 20 + 8 + 5
assert packet.hex(" ") == (
    "45 00 00 21 12 34 40 00 40 11 5c e5 c0 a8 01 0a 0a 00 00 01"  # IPv4: checksum 0x5ce5
    " 14 e9 00 35 00 0d 00 00"  # UDP: 5353 -> 53, length 13, no checksum
    " 71 75 65 72 79"  # "query"
)
assert checksum(packet[:20]) == 0  # a valid header sums to zero

ip, udp, payload = parse_udp_packet(packet + b"\0\0")  # trailing padding is ignored
assert (str(ip.source), str(ip.destination), ip.ttl) == ("192.168.1.10", "10.0.0.1", 64)
assert (udp.source_port, udp.destination_port, udp.length, payload) == (5353, 53, 13, b"query")

corrupted = bytearray(packet)
corrupted[8] = 1  # TTL
try:
    parse_udp_packet(bytes(corrupted))
except BinaryParseError as e:
    assert str(e) == "IPv4Header.checksum at offset 10: bad IPv4 header checksum"
```
