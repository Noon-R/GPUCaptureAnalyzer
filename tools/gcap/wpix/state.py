"""コマンドリストを再生して、各 Draw/Dispatch 時点のパイプライン状態とバインドされたリソースを解決する。

    from gcap.wpix.capture import Capture
    from gcap.wpix.state import analyze_draws
    for d in analyze_draws(Capture.open("v0.wpix")): print(d)

制限 (TODO):
  * ディスクリプタヒープはキャプチャ開始時のスナップショット (PIX::DescriptorHeap) を使う。
    フレーム内での CreateShaderResourceView / CopyDescriptors による書き換えは未反映
  * Bundle / ExecuteIndirect の間接引数は未展開
"""
from __future__ import annotations

import copy
from typing import Any

from ..rootsig import parse_root_signature
from .capture import Capture

REG_PREFIX = {"SRV": "t", "UAV": "u", "CBV": "b", "Sampler": "s"}
DRAW_APIS = ("DrawInstanced", "DrawIndexedInstanced", "Dispatch", "ExecuteIndirect", "DispatchRays", "DispatchMesh")


def _new_state() -> dict[str, Any]:
    return {"pso": None, "rootSig": {"Graphics": None, "Compute": None}, "heaps": [],
            "root": {"Graphics": {}, "Compute": {}}, "vb": [], "ib": None, "topology": None,
            "rtv": [], "dsv": None, "viewports": None, "scissors": None, "stencilRef": None, "blendFactor": None}


class DrawAnalyzer:
    def __init__(self, cap: Capture):
        self.cap = cap
        self.rootsigs: dict[int, dict] = {}
        self.heaps: dict[int, list[dict]] = {}
        self.pso_rootsig: dict[int, int] = {}
        self._collect()

    def _collect(self) -> None:
        for c in self.cap.calls:
            if c.api.endswith("::CreateRootSignature") and c.ok:
                raw = self.cap.raw_args(c)
                rs = (raw.get("ppvRootSignature") or {}).get("obj")
                blob = raw.get("pBlobWithRootSignature")
                if rs and isinstance(blob, (bytes, bytearray)):
                    try:
                        self.rootsigs[rs] = parse_root_signature(bytes(blob))
                    except (ValueError, Exception):  # noqa: BLE001 - 解析不能な blob は無視
                        pass
            elif c.api == "PIX::DescriptorHeap" and c.ok:
                self.heaps[c.args["ppHeap"]] = c.args.get("descriptors") or []
            elif c.api.endswith("PipelineState") and "Create" in c.api and c.ok:
                pso = (c.args.get("ppPipelineState") or {}).get("obj")
                rs = ((c.args.get("pDesc") or {}).get("pRootSignature") or {}).get("obj")
                if pso and rs:
                    self.pso_rootsig[pso] = rs

    def name(self, oid: int | None) -> str:
        return self.cap.object_name(oid)

    def _resolve_table(self, rs: dict | None, param_index: int, handle: dict) -> list[dict]:
        heap = self.heaps.get(handle["heap"], [])
        out = []
        ranges = []
        if rs and param_index < len(rs["parameters"]):
            ranges = rs["parameters"][param_index].get("ranges", [])
        if not ranges:
            ranges = [{"type": "?", "num": 1, "offset": 0, "baseRegister": None, "space": 0}]
        for rg in ranges:
            num = rg["num"] if rg["num"] != 0xFFFFFFFF else max(0, len(heap) - handle["index"] - rg["offset"])
            for k in range(min(num, 64)):
                slot = handle["index"] + rg["offset"] + k
                d = heap[slot] if slot < len(heap) else {"kind": "OutOfSnapshot"}
                reg = None if rg["baseRegister"] is None else                     f"{REG_PREFIX.get(rg['type'], '?')}{rg['baseRegister'] + k}"
                out.append({"register": reg, "space": rg.get("space"), "heapSlot": slot, "kind": d.get("kind"),
                            "resource": self.name(d.get("resource")) if d.get("resource") else None,
                            "view": d.get("view")})
        return out

    def _bindings(self, st: dict, pipe: str) -> list[dict]:
        rs_id = st["rootSig"][pipe]
        rs = self.rootsigs.get(rs_id)
        out = []
        for idx in sorted(st["root"][pipe]):
            kind, val = st["root"][pipe][idx]
            p = rs["parameters"][idx] if rs and idx < len(rs["parameters"]) else {}
            b: dict[str, Any] = {"rootParam": idx, "type": kind, "visibility": p.get("visibility")}
            if kind == "table":
                b["descriptors"] = self._resolve_table(rs, idx, val)
            elif kind == "constants":
                b["register"] = f"b{p['register']}" if "register" in p else None
                b["values"] = val
            else:
                b["register"] = f"{REG_PREFIX[kind]}{p['register']}" if "register" in p else None
                b["gpuva"] = {"resource": self.name(val["resource"]), "offset": val["offset"]}
            out.append(b)
        return out

    def run(self) -> list[dict]:
        draws = []
        for cl in self.cap.command_lists():
            st = _new_state()
            for c in cl["commands"]:
                m = c.api.split("::")[-1]
                a = c.args
                if m == "Reset":
                    st = _new_state()
                    st["pso"] = (a.get("pInitialState") or {}).get("obj") or None
                elif m == "SetPipelineState":
                    st["pso"] = a["pPipelineState"]["obj"]
                elif m in ("SetGraphicsRootSignature", "SetComputeRootSignature"):
                    pipe = "Graphics" if "Graphics" in m else "Compute"
                    st["rootSig"][pipe] = a["pRootSignature"]["obj"]
                    st["root"][pipe] = {}
                elif m == "SetDescriptorHeaps":
                    st["heaps"] = [h["obj"] for h in a.get("ppDescriptorHeaps") or []]
                elif m.startswith(("SetGraphicsRoot", "SetComputeRoot")):
                    pipe = "Graphics" if m.startswith("SetGraphics") else "Compute"
                    idx = a.get("RootParameterIndex")
                    if m.endswith("DescriptorTable"):
                        st["root"][pipe][idx] = ("table", a["BaseDescriptor"])
                    elif m.endswith("32BitConstants"):
                        st["root"][pipe][idx] = ("constants", a["pSrcData"]["hex"])
                    elif m.endswith("32BitConstant"):
                        st["root"][pipe][idx] = ("constants", a.get("SrcData"))
                    elif m.endswith("ConstantBufferView"):
                        st["root"][pipe][idx] = ("CBV", a["BufferLocation"])
                    elif m.endswith("ShaderResourceView"):
                        st["root"][pipe][idx] = ("SRV", a["BufferLocation"])
                    elif m.endswith("UnorderedAccessView"):
                        st["root"][pipe][idx] = ("UAV", a["BufferLocation"])
                elif m == "IASetVertexBuffers":
                    for i, v in enumerate(a.get("pViews") or []):
                        slot = a["StartSlot"] + i
                        st["vb"] = [x for x in st["vb"] if x["slot"] != slot] + [{
                            "slot": slot, "resource": self.name(v["BufferLocation"]["resource"]),
                            "offset": v["BufferLocation"]["offset"], "size": v["SizeInBytes"],
                            "stride": v["StrideInBytes"]}]
                elif m == "IASetIndexBuffer":
                    v = a.get("pView")
                    st["ib"] = None if v is None else {"resource": self.name(v["BufferLocation"]["resource"]),
                                                       "offset": v["BufferLocation"]["offset"],
                                                       "size": v["SizeInBytes"], "format": v["Format"]}
                elif m == "IASetPrimitiveTopology":
                    st["topology"] = a["PrimitiveTopology"]
                elif m == "OMSetRenderTargets":
                    st["rtv"] = [{"resource": self.name(h["resource"]), "view": h["view"]}
                                 for h in a.get("pRenderTargetDescriptors") or []]
                    d = a.get("pDepthStencilDescriptor")
                    st["dsv"] = None if d is None else {"resource": self.name(d["resource"]), "view": d["view"]}
                elif m == "RSSetViewports":
                    st["viewports"] = a.get("pViewports")
                elif m == "RSSetScissorRects":
                    st["scissors"] = a.get("pRects")
                elif m == "OMSetStencilRef":
                    st["stencilRef"] = a["StencilRef"]
                elif m == "OMSetBlendFactor":
                    st["blendFactor"] = a["BlendFactor"]
                elif m in DRAW_APIS:
                    pipe = "Compute" if m.startswith("Dispatch") else "Graphics"
                    d: dict[str, Any] = {
                        "seq": c.seq, "commandList": self.name(cl["list"]), "call": m,
                        "args": {k: v for k, v in a.items()},
                        "pso": self.name(st["pso"]),
                        "rootSignature": self.name(st["rootSig"][pipe]),
                        "bindings": self._bindings(st, pipe),
                    }
                    if pipe == "Graphics":
                        d.update(topology=st["topology"], vertexBuffers=copy.deepcopy(st["vb"]), indexBuffer=st["ib"],
                                 renderTargets=st["rtv"], depthStencil=st["dsv"], viewports=st["viewports"],
                                 scissors=st["scissors"])
                    draws.append(d)
        return draws


def analyze_draws(cap: Capture) -> list[dict]:
    return DrawAnalyzer(cap).run()
