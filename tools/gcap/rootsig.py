"""シリアライズ済みルートシグネチャ (D3D12SerializeRootSignature の出力 = DXBC コンテナ内の RTS0 チャンク) のパーサ。

RTS0 レイアウト (公開仕様 / DirectX-Specs と d3d12.h の構造体に準拠):
    u32 Version (1 = 1.0, 2 = 1.1, 3 = 1.2)
    u32 NumParameters, u32 ParametersOffset
    u32 NumStaticSamplers, u32 StaticSamplersOffset
    u32 Flags
    Parameter: u32 ParameterType, u32 ShaderVisibility, u32 PayloadOffset
      DescriptorTable: u32 NumRanges, u32 RangesOffset
        Range 1.0: RangeType, NumDescriptors, BaseShaderRegister, RegisterSpace, OffsetInDescriptorsFromTableStart
        Range 1.1: RangeType, NumDescriptors, BaseShaderRegister, RegisterSpace, Flags, Offset
      Constants: ShaderRegister, RegisterSpace, Num32BitValues
      Descriptor 1.0: ShaderRegister, RegisterSpace (1.1: + Flags)
    StaticSampler: D3D12_STATIC_SAMPLER_DESC (13 x u32 相当)
オフセットは全て RTS0 チャンク先頭からの相対。
"""
from __future__ import annotations

import struct
from typing import Any

PARAM_TYPES = {0: "DescriptorTable", 1: "32BitConstants", 2: "CBV", 3: "SRV", 4: "UAV"}
RANGE_TYPES = {0: "SRV", 1: "UAV", 2: "CBV", 3: "Sampler"}
VISIBILITY = {0: "ALL", 1: "VERTEX", 2: "HULL", 3: "DOMAIN", 4: "GEOMETRY", 5: "PIXEL", 6: "AMPLIFICATION", 7: "MESH"}
APPEND = 0xFFFFFFFF


def find_chunk(blob: bytes, tag: bytes) -> bytes | None:
    if blob[:4] != b"DXBC":
        return blob if blob[:4] != tag else None
    count = struct.unpack_from("<I", blob, 28)[0]
    for i in range(count):
        off = struct.unpack_from("<I", blob, 32 + 4 * i)[0]
        if blob[off:off + 4] == tag:
            size = struct.unpack_from("<I", blob, off + 4)[0]
            return blob[off + 8: off + 8 + size]
    return None


def parse_root_signature(blob: bytes) -> dict[str, Any]:
    rts = find_chunk(blob, b"RTS0")
    if rts is None:
        raise ValueError("RTS0 chunk not found")
    u = lambda o: struct.unpack_from("<I", rts, o)[0]  # noqa: E731
    ver, nparam, poff, nsamp, soff, flags = struct.unpack_from("<6I", rts, 0)
    params = []
    for i in range(nparam):
        ptype, vis, payload = struct.unpack_from("<3I", rts, poff + 12 * i)
        p: dict[str, Any] = {"type": PARAM_TYPES.get(ptype, ptype), "visibility": VISIBILITY.get(vis, vis)}
        if ptype == 0:
            nr, roff = u(payload), u(payload + 4)
            rsize = 20 if ver == 1 else 24
            ranges = []
            cursor = 0
            for j in range(nr):
                vals = struct.unpack_from("<6I" if ver != 1 else "<5I", rts, roff + rsize * j)
                rt, num, base, space = vals[:4]
                offset = vals[-1]
                if offset == APPEND:
                    offset = cursor
                cursor = offset + (num if num != 0xFFFFFFFF else 0)
                ranges.append({"type": RANGE_TYPES.get(rt, rt), "num": num, "baseRegister": base, "space": space,
                               "offset": offset, **({"flags": vals[4]} if ver != 1 else {})})
            p["ranges"] = ranges
        elif ptype == 1:
            reg, space, num = struct.unpack_from("<3I", rts, payload)
            p.update(register=reg, space=space, num32BitValues=num)
        else:
            reg, space = struct.unpack_from("<2I", rts, payload)
            p.update(register=reg, space=space)
        params.append(p)
    samplers = []
    for i in range(nsamp):
        v = struct.unpack_from("<5If3If3I", rts, soff + 52 * i)
        samplers.append({"filter": v[0], "address": v[1:4], "register": v[10], "space": v[11],
                         "visibility": VISIBILITY.get(v[12], v[12])})
    return {"version": ver, "flags": flags, "parameters": params, "staticSamplers": samplers}
