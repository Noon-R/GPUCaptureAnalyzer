"""EVT レコード -> API 呼び出しへのデコーダ。

type ID の決定規則 (解析結果):
    typeId = base[宣言インターフェース] + (そのインターフェースが自身で宣言したメソッド中の順番)
    例: ID3D12GraphicsCommandList の自前メソッドは Close から始まり base=992。
base は knowledge/type_bases.json に蓄積する (解析が進むたびに追記)。
PIX 独自イベント (リソース初期データ等) は knowledge/pix_events.json + custom デコーダで扱う。
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..d3dmeta.header_parser import HeaderDB, Method, Param, load_db
from .events import EvtRecord
from .serializer import DecodeError, Reader, is_primitive, read_primitive

KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")
META_SIZE = 24  # "META" + u32 x5 (常に 0 を観測)

# union を持つ構造体: 判別フィールドと 値 -> メンバ名
UNION_SELECTORS: dict[str, tuple[str, dict[int, str]]] = {
    "D3D12_RESOURCE_BARRIER": ("Type", {0: "Transition", 1: "Aliasing", 2: "UAV"}),
    "D3D12_ROOT_PARAMETER": ("ParameterType", {0: "DescriptorTable", 1: "Constants", 2: "Descriptor",
                                               3: "Descriptor", 4: "Descriptor"}),
    "D3D12_ROOT_PARAMETER1": ("ParameterType", {0: "DescriptorTable", 1: "Constants", 2: "Descriptor",
                                                3: "Descriptor", 4: "Descriptor"}),
    "D3D12_SHADER_RESOURCE_VIEW_DESC": ("ViewDimension", {
        1: "Buffer", 2: "Texture1D", 3: "Texture1DArray", 4: "Texture2D", 5: "Texture2DArray", 6: "Texture2DMS",
        7: "Texture2DMSArray", 8: "Texture3D", 9: "TextureCube", 10: "TextureCubeArray",
        11: "RaytracingAccelerationStructure"}),
    "D3D12_UNORDERED_ACCESS_VIEW_DESC": ("ViewDimension", {
        1: "Buffer", 2: "Texture1D", 3: "Texture1DArray", 4: "Texture2D", 5: "Texture2DArray",
        6: "Texture2DMS", 7: "Texture2DMSArray", 8: "Texture3D"}),
    "D3D12_RENDER_TARGET_VIEW_DESC": ("ViewDimension", {
        1: "Buffer", 2: "Texture1D", 3: "Texture1DArray", 4: "Texture2D", 5: "Texture2DArray", 6: "Texture2DMS",
        7: "Texture2DMSArray", 8: "Texture3D"}),
    "D3D12_DEPTH_STENCIL_VIEW_DESC": ("ViewDimension", {
        1: "Texture1D", 2: "Texture1DArray", 3: "Texture2D", 4: "Texture2DArray", 5: "Texture2DMS",
        6: "Texture2DMSArray"}),
    "D3D12_VERSIONED_ROOT_SIGNATURE_DESC": ("Version", {1: "Desc_1_0", 2: "Desc_1_1", 3: "Desc_1_2"}),
    "D3D12_TEXTURE_COPY_LOCATION": ("Type", {0: "SubresourceIndex", 1: "PlacedFootprint"}),
}

# union の全メンバを (選択せずに) 順に書き出す構造体
UNION_ALL_MEMBERS = {"D3D12_CLEAR_VALUE"}

# CPU ディスクリプタハンドル引数 -> ディスクリプタ種別 (RTV/DSV は種別フィールドなし)
CPU_HANDLE_KIND_BY_PARAM = {
    "RenderTargetView": "RTV", "pRenderTargetDescriptors": "RTV",
    "DepthStencilView": "DSV", "pDepthStencilDescriptor": "DSV",
    "ViewCPUHandle": "CBV_SRV_UAV",
}

# CBV_SRV_UAV / SAMPLER ヒープ上のディスクリプタ種別 (u32)。PIX 独自の列挙
DESCRIPTOR_KIND = {0: "Empty", 1: "CBV", 2: "SRV", 3: "UAV", 4: "Sampler?", 5: "RTV?", 6: "DSV?"}


# SAL 上は単一ポインタだが PIX は配列 (present + u64 count + 要素) として記録する引数
ARRAY_PARAM_OVERRIDES: set[tuple[str, str]] = {
    ("OMSetRenderTargets", "pRenderTargetDescriptors"),
}


@dataclass
class Call:
    seq: int
    stream: int
    type: int
    api: str                       # "ID3D12GraphicsCommandList::DrawInstanced" など
    this: int | None = None
    hr: int | None = None
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True                # ペイロードを過不足なく消費できたか
    error: str = ""
    leftover: bytes = b""
    raw: bytes = b""

    def to_json(self) -> dict:
        d = {"seq": self.seq, "stream": self.stream, "type": self.type, "api": self.api}
        if self.this is not None:
            d["this"] = self.this
        if self.hr is not None:
            d["hr"] = self.hr
        d["args"] = self.args
        if not self.ok:
            d["ok"] = False
            d["error"] = self.error
            d["raw"] = self.raw.hex()
        return d


def _jsonable(v: Any) -> Any:
    if isinstance(v, bytes):
        return {"bytes": len(v), "hex": v[:64].hex() + ("..." if len(v) > 64 else "")}
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return v


class ApiDecoder:
    def __init__(self, db: HeaderDB | None = None):
        self.db = db or load_db()
        self.bases: dict[str, int] = json.loads((KNOWLEDGE_DIR / "type_bases.json").read_text("utf-8"))["bases"]
        self.type_map: dict[int, tuple[str, Method]] = {}
        self._build_type_map()
        self.custom: dict[int, tuple[str, Callable[[Reader, "ApiDecoder"], dict]]] = {}
        from . import pix_events
        pix_events.register(self)
        self.iid_map = self.db.iid_to_interface()
        self.objects: dict[int, dict[str, Any]] = {}
        self._cpu_kind: str | None = None

    # ---- type map -------------------------------------------------------------------------------
    def own_methods(self, iface: str) -> list[Method]:
        itf = self.db.interfaces.get(iface)
        if not itf:
            return []
        return [m for m in itf.methods if m.declared_in == iface]

    def _build_type_map(self) -> None:
        for iface, base in self.bases.items():
            if iface.startswith("_"):
                continue
            for i, m in enumerate(self.own_methods(iface)):
                self.type_map[base + i] = (iface, m)

    def api_name(self, typ: int) -> str:
        if typ in self.custom:
            return self.custom[typ][0]
        if typ in self.type_map:
            iface, m = self.type_map[typ]
            return f"{iface}::{m.name}"
        return f"Unknown_{typ}"

    # ---- object tracking ------------------------------------------------------------------------
    def note_object(self, oid: int, **kw) -> None:
        if oid:
            self.objects.setdefault(oid, {}).update(kw)

    # ---- value decoding -------------------------------------------------------------------------
    def is_interface(self, typ: str) -> bool:
        return typ in self.db.interfaces or typ in ("IUnknown",) or (typ.startswith("ID3D12") and "_" not in typ) \
            or (typ.startswith("IDXGI") and "_" not in typ)

    def resolve(self, typ: str) -> str:
        seen = 0
        while typ in self.db.typedefs and seen < 8 and not is_primitive(typ) and typ not in self.db.structs \
                and typ not in self.db.enums:
            t = self.db.typedefs[typ]
            if "*" in t or t.split()[0] in ("struct", "enum", "union"):
                break
            typ = t
            seen += 1
        return typ

    def read_type(self, r: Reader, typ: str, ptr: int = 0) -> Any:
        # --- 特殊型 (typedef 解決前に判定する) ---
        if typ == "D3D12_GPU_VIRTUAL_ADDRESS":
            off = r.u64()
            res = r.u32()
            return {"resource": res, "offset": off}
        if typ == "D3D12_GPU_DESCRIPTOR_HANDLE":
            return {"heap": r.u32(), "index": r.u32()}
        if typ == "D3D12_CPU_DESCRIPTOR_HANDLE":
            return self.read_cpu_descriptor_handle(r, self._cpu_kind)
        if typ in ("LPCWSTR", "LPWSTR"):
            return self.read_string(r, wide=True)
        if typ in ("LPCSTR", "LPSTR"):
            return self.read_string(r, wide=False)
        if typ in ("D3D12_RECT", "RECT"):
            return [r.i32() for _ in range(4)]
        typ = self.resolve(typ)
        if is_primitive(typ):
            return read_primitive(r, typ)
        if typ in self.db.enums:
            v = r.u32()
            name = self.db.enum_name(typ, v)
            return name if name is not None else v
        if typ in self.db.structs:
            return self.read_struct(r, typ)
        if self.is_interface(typ):
            return {"obj": r.u32()}
        raise DecodeError(f"unknown type {typ}")

    def read_string(self, r: Reader, wide: bool) -> str | None:
        if not r.flag():
            return None
        n = r.u64()
        return r.wstr(n) if wide else r.astr(n)

    def read_cpu_descriptor_handle(self, r: Reader, kind: str | None) -> dict:
        """CPU ディスクリプタハンドルは「ハンドル値」ではなく、そのディスクリプタの中身 (作成時の引数) で記録される。
        RTV/DSV: u8 present + ViewDesc, u32 resource, u8 present + D3D12_RESOURCE_DESC
        CBV_SRV_UAV: u32 種別 + 種別毎の内容 (read_descriptor 参照)
        """
        if kind in ("RTV", "DSV"):
            vd = "D3D12_RENDER_TARGET_VIEW_DESC" if kind == "RTV" else "D3D12_DEPTH_STENCIL_VIEW_DESC"
            view = self.read_type(r, vd) if r.flag() else None
            res = r.u32()
            rdesc = self.read_type(r, "D3D12_RESOURCE_DESC") if r.flag() else None
            return {"kind": kind, "view": view, "resource": res, "resourceDesc": rdesc}
        if kind == "CBV_SRV_UAV":
            return self.read_descriptor(r)
        raise DecodeError(f"cpu descriptor handle in unknown context ({kind})")

    def read_descriptor(self, r: Reader) -> dict:
        """CBV_SRV_UAV ヒープのディスクリプタ: u32 種別 + 内容。PIX::DescriptorHeap(2044) でも同じ形式。"""
        k = r.u32()
        kind = DESCRIPTOR_KIND.get(k, str(k))
        out: dict[str, Any] = {"kind": kind}
        if k == 0:
            return out
        if k == 1:
            out["view"] = self.read_type(r, "D3D12_CONSTANT_BUFFER_VIEW_DESC") if r.flag() else None
            return out
        if k == 2:
            out["view"] = self.read_type(r, "D3D12_SHADER_RESOURCE_VIEW_DESC") if r.flag() else None
            out["resource"] = r.u32()
        elif k == 3:
            out["view"] = self.read_type(r, "D3D12_UNORDERED_ACCESS_VIEW_DESC") if r.flag() else None
            out["resource"] = r.u32()
            out["counterResource"] = r.u32()
            # TODO: counterResource != 0 の場合に追加の RESOURCE_DESC があるか未確認
        else:
            raise DecodeError(f"descriptor kind {k} not yet understood")
        out["resourceDesc"] = self.read_type(r, "D3D12_RESOURCE_DESC") if r.flag() else None
        return out

    def read_struct(self, r: Reader, sname: str) -> dict:
        st = self.db.structs[sname]
        out: dict[str, Any] = {}
        sel = UNION_SELECTORS.get(sname)
        union_done: set[int] = set()
        for f in st.fields:
            if f.union >= 0 and sname not in UNION_ALL_MEMBERS:
                if f.union in union_done:
                    continue
                if not sel:
                    raise DecodeError(f"union in {sname} without selector")
                disc = out.get(sel[0])
                disc_v = disc if isinstance(disc, int) else self.db.enums.get(
                    self.db.structs[sname].fields[[x.name for x in st.fields].index(sel[0])].type, {}).get(disc)
                member = sel[1].get(disc_v)
                if member is None:
                    raise DecodeError(f"{sname}: unknown union selector {disc}")
                if f.name != member:
                    # 同 union の選択メンバを探す
                    fm = next((x for x in st.fields if x.union == f.union and x.name == member), None)
                    if fm is None:
                        raise DecodeError(f"{sname}: member {member} not found")
                    f = fm
                union_done.add(f.union)
            out[f.name] = self.read_field(r, f.type, f.ptr, f.array, sname, f.name)
        return out

    def read_field(self, r: Reader, typ: str, ptr: int, array: str, owner: str, name: str) -> Any:
        rtyp = self.resolve(typ)
        if array:
            # 構造体内の固定長配列: u64 要素数 + 要素 (present フラグなし)
            n = r.u64()
            declared = self._array_len(array)
            if n > declared:
                raise DecodeError(f"{owner}.{name}: array count {n} > declared {declared}")
            if rtyp in ("WCHAR", "wchar_t"):
                return r.wstr(n).rstrip("\x00")
            if rtyp in ("CHAR", "char"):
                return r.astr(n).rstrip("\x00")
            return [self.read_type(r, typ) for _ in range(n)]
        if ptr == 0:
            return self.read_type(r, typ)
        if self.is_interface(rtyp) and ptr == 1:
            return {"obj": r.u32()}
        if rtyp in ("LPCWSTR", "LPCSTR") or (rtyp in ("WCHAR", "wchar_t", "CHAR", "char") and ptr == 1):
            if not r.flag():
                return None
            n = r.u64()
            return r.wstr(n) if rtyp in ("LPCWSTR", "WCHAR", "wchar_t") else r.astr(n)
        # データポインタ: present + u64 count + 要素
        if not r.flag():
            return None
        n = r.u64()
        if rtyp == "void":
            return r.take(n)
        if self.is_interface(rtyp):
            return [{"obj": r.u32()} for _ in range(n)]
        return [self.read_type(r, typ) for _ in range(n)]

    def _array_len(self, expr: str) -> int:
        try:
            return int(expr, 0)
        except ValueError:
            pass
        for e in self.db.enums.values():
            if expr in e:
                return e[expr]
        known = {"D3D12_SIMULTANEOUS_RENDER_TARGET_COUNT": 8, "D3D12_SO_BUFFER_SLOT_COUNT": 4,
                 "D3D12_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE": 16}
        if expr in known:
            return known[expr]
        raise DecodeError(f"array length {expr}")

    def read_param(self, r: Reader, p: Param, method: str = "") -> Any:
        rtyp = self.resolve(p.type)
        sal = p.sal
        if (method, p.name) in ARRAY_PARAM_OVERRIDES:
            sal = sal + " _In_reads_(override)"
        self._cpu_kind = CPU_HANDLE_KIND_BY_PARAM.get(p.name)
        if p.array:
            # 関数引数の固定長配列 (例 const FLOAT ColorRGBA[4]) は present + count + 要素
            if not r.flag():
                return None
            n = r.u64()
            return [self.read_type(r, p.type) for _ in range(n)]
        if p.ptr == 0:
            return self.read_type(r, p.type)
        if rtyp in ("LPCWSTR", "LPCSTR"):
            return self.read_type(r, rtyp)
        if self.is_interface(rtyp) or (rtyp == "void" and p.ptr == 2):
            if p.ptr == 1:
                return {"obj": r.u32()}
            if "Outptr" in sal or "_Out" in sal:
                if not r.flag():
                    return None
                return {"obj": r.u32()}
            # 入力配列 (例 ID3D12DescriptorHeap *const *ppHeaps)
            if not r.flag():
                return None
            n = r.u64()
            return [{"obj": r.u32()} for _ in range(n)]
        if not r.flag():
            return None
        if rtyp == "void":
            n = r.u64()
            return r.take(n)
        if "reads" in sal or "writes" in sal or "updates" in sal:
            n = r.u64()
            if rtyp in ("WCHAR", "wchar_t"):
                return r.wstr(n)
            return [self.read_type(r, p.type) for _ in range(n)]
        return self.read_type(r, p.type)

    # ---- record decoding ------------------------------------------------------------------------
    def decode(self, rec: EvtRecord) -> Call:
        payload = rec.payload
        if payload[:4] != b"META":
            return Call(rec.seq, rec.stream_id, rec.type, self.api_name(rec.type), ok=False,
                        error="payload does not start with META", raw=payload)
        body = payload[META_SIZE:]
        call = Call(rec.seq, rec.stream_id, rec.type, self.api_name(rec.type), raw=body)
        r = Reader(body)
        try:
            if rec.type in self.custom:
                call.args = self.custom[rec.type][1](r, self)
            elif rec.type in self.type_map:
                iface, m = self.type_map[rec.type]
                call.this = r.u32()
                if m.ret == "HRESULT":
                    call.hr = r.u32()
                for p in m.params:
                    call.args[p.name] = self.read_param(r, p, m.name)
                self._post_process(call, iface, m)
            else:
                raise DecodeError("unknown type id")
            if r.remaining():
                call.ok = False
                call.leftover = body[r.pos:]
                call.error = f"{r.remaining()} bytes left over"
        except (DecodeError, KeyError, IndexError, struct.error) as e:
            call.ok = False
            call.error = f"{type(e).__name__}: {e} (at {r.pos})"
        call.args = _jsonable(call.args)
        return call

    def _post_process(self, call: Call, iface: str, m: Method) -> None:
        # 生成オブジェクトの型・名前を追跡
        riid = None
        for p in m.params:
            v = call.args.get(p.name)
            if p.type in ("REFIID",):
                riid = v
            if isinstance(v, dict) and "obj" in v and ("Outptr" in p.sal) and riid:
                self.note_object(v["obj"], interface=self.iid_map.get(riid, riid), created_by=call.api,
                                 seq=call.seq)
        if m.name == "SetName" and call.this is not None:
            self.note_object(call.this, name=call.args.get("Name"))
