"""シェーダバイトコード (DXBC / DXIL コンテナ) から「実際に使うリソースバインディング」を取り出す。

draws でディスクリプタテーブルのどのスロットをシェーダが本当に読むかを判定するのに使う
(UE5 などはテーブルを大きめに宣言し、未使用スロットには古い/未書き込みのディスクリプタが残る)。

    DXIL: PSV0 パート (DxilPipelineStateValidation.h)
        u32 runtimeInfoSize, PSVRuntimeInfo[runtimeInfoSize], u32 resourceCount,
        (resourceCount > 0 なら) u32 bindInfoSize, PSVResourceBindInfo[resourceCount]
        PSVResourceBindInfo0: u32 ResType, u32 Space, u32 LowerBound, u32 UpperBound (v1 で ResKind, ResFlags が続く)
    DXBC: RDEF チャンク (D3D11_SHADER_INPUT_BIND_DESC 相当)
        u32 cbCount, u32 cbOffset, u32 bindCount, u32 bindOffset, u8 minor, u8 major, ...
        bind: u32 name, type, retType, dim, numSamples, bindPoint, bindCount, flags (+ 5.1 以降 u32 space, u32 id)

結果は [(クラス 't'/'u'/'b'/'s', space, lower, upper(含む))]。upper = 0xFFFFFFFF は上限なし。
"""
from __future__ import annotations

import struct

from .rootsig import find_chunk

UNBOUNDED = 0xFFFFFFFF
# PSVResourceType
_PSV_CLASS = {1: "s", 2: "b", 3: "t", 4: "t", 5: "t", 6: "u", 7: "u", 8: "u", 9: "u"}
# D3D_SHADER_INPUT_TYPE
_RDEF_CLASS = {0: "b", 1: "t", 2: "t", 3: "s", 4: "u", 5: "t", 6: "u", 7: "t", 8: "u", 9: "u", 10: "u", 11: "u",
               12: "t", 13: "u"}

Binding = tuple[str, int, int, int]


def _psv0(p: bytes) -> list[Binding] | None:
    if len(p) < 4:
        return None
    rt_size = struct.unpack_from("<I", p, 0)[0]
    off = 4 + rt_size
    if off + 4 > len(p):
        return None
    n = struct.unpack_from("<I", p, off)[0]
    off += 4
    if n == 0:
        return []
    stride = struct.unpack_from("<I", p, off)[0]
    off += 4
    out = []
    for i in range(n):
        if off + i * stride + 16 > len(p):
            return None
        typ, space, lo, hi = struct.unpack_from("<4I", p, off + i * stride)
        if typ in _PSV_CLASS:
            out.append((_PSV_CLASS[typ], space, lo, hi))
    return out


def _rdef(p: bytes) -> list[Binding] | None:
    if len(p) < 28:
        return None
    _, _, n, boff = struct.unpack_from("<4I", p, 0)
    minor, major = p[16], p[17]
    stride = 40 if (major, minor) >= (5, 1) else 32
    out = []
    for i in range(n):
        o = boff + i * stride
        if o + stride > len(p):
            return None
        _, typ, _, _, _, point, count, _ = struct.unpack_from("<8I", p, o)
        space = struct.unpack_from("<I", p, o + 32)[0] if stride == 40 else 0
        if typ in _RDEF_CLASS:
            out.append((_RDEF_CLASS[typ], space, point, UNBOUNDED if count == 0 else point + count - 1))
    return out


def used_bindings(blob: bytes | None) -> list[Binding] | None:
    """シェーダが使うバインディング。判定できなければ None。"""
    if not blob or blob[:4] != b"DXBC":
        return None
    psv = find_chunk(blob, b"PSV0")
    if psv is not None:
        return _psv0(psv)
    rdef = find_chunk(blob, b"RDEF")
    if rdef is not None:
        return _rdef(rdef)
    return None


def is_used(bindings: list[Binding] | None, register: str | None, space: int | None) -> bool | None:
    """register ('t3' 等) をシェーダが使うか。bindings / register が不明なら None。"""
    if bindings is None or not register or register[0] not in "tubs":
        return None
    cls, reg = register[0], int(register[1:])
    return any(c == cls and s == (space or 0) and lo <= reg <= hi for c, s, lo, hi in bindings)
