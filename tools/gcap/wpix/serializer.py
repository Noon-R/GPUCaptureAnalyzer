"""PIX EVT ペイロードの値シリアライズ規則 (解析で判明したもの)。

基本規則 (docs/formats/wpix.md 参照):
  * パディングなし・リトルエンディアン・自然サイズ (UINT=4, UINT64=8, UINT16=2, BOOL=4, enum=4)
  * COM インターフェースポインタ: u32 オブジェクト ID (0 = null)
  * データへのポインタ: u8 present + (present なら) 値
      - 配列 (_In_reads_(N) 等): u8 present + u64 要素数 + 要素
      - バイト列 (void* / _In_reads_bytes_): u8 present + u64 バイト数 + bytes
      - 文字列 (LPCWSTR/LPCSTR): u8 present + u64 文字数 + 文字 (終端なし)
      - 単一構造体 (_In_ const T*): u8 present + T
  * REFIID/REFGUID/GUID: 16 bytes
  * D3D12_GPU_VIRTUAL_ADDRESS: u64 offset + u32 resourceId  (リソース相対に正規化されている)
  * D3D12_GPU_DESCRIPTOR_HANDLE: u32 heapId + u32 index
  * D3D12_CPU_DESCRIPTOR_HANDLE: 状況依存 (cpu_descriptor_handle 参照)
  * 構造体内のポインタ+個数 ペア (例 D3D12_SHADER_BYTECODE): u8 present + u64 count + data
"""
from __future__ import annotations

import struct
import uuid
from typing import Any


class DecodeError(Exception):
    pass


class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise DecodeError(f"read past end (pos={self.pos}, n={n}, len={len(self.data)})")
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def guid(self) -> str:
        return str(uuid.UUID(bytes_le=self.take(16)))

    def flag(self) -> bool:
        v = self.u8()
        if v not in (0, 1):
            raise DecodeError(f"invalid presence flag {v:#x} at {self.pos - 1}")
        return v == 1

    def wstr(self, n: int) -> str:
        return self.take(n * 2).decode("utf-16-le", errors="replace")

    def astr(self, n: int) -> str:
        return self.take(n).decode("latin1")


# ------------------------------------------------------------------------------------------------
# プリミティブ型
# ------------------------------------------------------------------------------------------------
PRIM_U32 = {"UINT", "UINT32", "DWORD", "ULONG", "D3D12_RECT_UINT", "UINT_PTR32", "D3D12_COMMAND_QUEUE_PRIORITY"}
PRIM_I32 = {"INT", "LONG", "INT32", "HRESULT"}
PRIM_BOOL = {"BOOL"}
PRIM_U64 = {"UINT64", "SIZE_T", "ULONG_PTR", "ULONGLONG", "D3D12_GPU_VIRTUAL_ADDRESS_RAW", "DWORD64"}
PRIM_I64 = {"INT64", "LONG64", "LONGLONG"}
PRIM_U16 = {"UINT16", "USHORT", "WORD"}
PRIM_U8 = {"UINT8", "BYTE", "BOOLEAN", "UCHAR"}
PRIM_F32 = {"FLOAT", "float"}
PRIM_F64 = {"DOUBLE", "double"}
PRIM_GUID = {"REFIID", "REFGUID", "GUID", "IID"}
PRIM_HANDLE = {"HANDLE", "HWND", "HMONITOR"}


def read_primitive(r: Reader, typ: str) -> Any:
    if typ in PRIM_U32:
        return r.u32()
    if typ in PRIM_I32:
        return r.i32()
    if typ in PRIM_BOOL:
        return bool(r.u32())
    if typ in PRIM_U64:
        return r.u64()
    if typ in PRIM_I64:
        return r.i64()
    if typ in PRIM_U16:
        return r.u16()
    if typ in PRIM_U8:
        return r.u8()
    if typ in PRIM_F32:
        return r.f32()
    if typ in PRIM_F64:
        return r.f64()
    if typ in PRIM_GUID:
        return r.guid()
    if typ in PRIM_HANDLE:
        return r.u64()
    raise KeyError(typ)


def is_primitive(typ: str) -> bool:
    return any(typ in s for s in (PRIM_U32, PRIM_I32, PRIM_BOOL, PRIM_U64, PRIM_I64, PRIM_U16, PRIM_U8,
                                  PRIM_F32, PRIM_F64, PRIM_GUID, PRIM_HANDLE))
