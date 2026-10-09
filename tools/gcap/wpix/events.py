"""wpix ストリーム内の <EVT レコード層。

    EVT ヘッダ (0x40 バイト):
      +0x00 char[4] "<EVT"
      +0x04 u32     type        API/イベント種別 ID
      +0x08 u64     seq         ストリーム横断の通し番号 (記録順)
      +0x10 u64     payloadSize ヘッダ以降のバイト数
      +0x18 u8[0x28] reserved (常に 0 を観測)
    payload: "META" で始まる
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

EVT_HEADER_SIZE = 0x40


@dataclass
class EvtRecord:
    stream_id: int
    offset: int          # ストリーム内オフセット
    type: int
    seq: int
    payload: bytes
    header_reserved: bytes

    @property
    def size(self) -> int:
        return EVT_HEADER_SIZE + len(self.payload)


def iter_records(stream_id: int, data: bytes):
    pos = 0
    n = len(data)
    while pos < n:
        if data[pos:pos + 4] != b"<EVT":
            raise ValueError(f"stream {stream_id}: expected <EVT at {pos:#x}, got {data[pos:pos + 4]!r}")
        typ, seq, psize = struct.unpack_from("<IQQ", data, pos + 4)
        reserved = data[pos + 0x18:pos + EVT_HEADER_SIZE]
        payload = data[pos + EVT_HEADER_SIZE:pos + EVT_HEADER_SIZE + psize]
        yield EvtRecord(stream_id, pos, typ, seq, payload, reserved)
        pos += EVT_HEADER_SIZE + psize


def all_records(wpix) -> list[EvtRecord]:
    recs = []
    for sid, st in wpix.streams.items():
        recs.extend(iter_records(sid, st.data))
    recs.sort(key=lambda r: r.seq)
    return recs
