"""コマンドリストを再生して、各 Draw/Dispatch 時点のパイプライン状態とバインドされたリソースを解決する。

    from gcap.wpix.capture import Capture
    from gcap.wpix.state import analyze_draws
    for d in analyze_draws(Capture.open("v0.wpix")): print(d)

対応:
  * Draw*/Dispatch/DispatchMesh/DispatchRays/ExecuteIndirect (間接引数は PIX::IndirectArguments から展開)
  * Bundle (ExecuteBundle は呼び出し元の状態を継承して中身を再生)
  * RenderPass (BeginRenderPass の RT/DS を出力先として扱う)
  * ルートシグネチャ (RTS0) を解析してディスクリプタテーブル -> 実リソースを解決
ディスクリプタヒープの内容 = PIX::DescriptorHeap (2044) のスナップショット + PIX::WriteDescriptors (2033) の書き込みのうち
そのコマンドリストを実行する ExecuteCommandLists より前のもの (GPU は実行時にディスクリプタを読むため)。
"""
from __future__ import annotations

import copy
from typing import Any

from ..rootsig import parse_root_signature
from ..shaderrefl import is_used, used_bindings
from .capture import Capture

REG_PREFIX = {"SRV": "t", "UAV": "u", "CBV": "b", "Sampler": "s"}
DRAW_APIS = ("DrawInstanced", "DrawIndexedInstanced", "Dispatch", "DispatchMesh", "DispatchRays", "ExecuteIndirect")
SHADER_STAGES = ("VS", "PS", "DS", "HS", "GS", "CS", "AS", "MS")
# ルートパラメータの ShaderVisibility -> そのパラメータが見えるステージ
VISIBLE_STAGES = {"ALL": SHADER_STAGES, "VERTEX": ("VS",), "HULL": ("HS",), "DOMAIN": ("DS",), "GEOMETRY": ("GS",),
                  "PIXEL": ("PS",), "AMPLIFICATION": ("AS",), "MESH": ("MS",)}
COMPUTE_INDIRECT_TYPES = {"D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH", "D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH_RAYS"}


def _new_state() -> dict[str, Any]:
    return {"pso": None, "stateObject": None, "rootSig": {"Graphics": None, "Compute": None}, "heaps": [],
            "root": {"Graphics": {}, "Compute": {}}, "vb": [], "ib": None, "topology": None,
            "rtv": [], "dsv": None, "renderPass": False, "viewports": None, "scissors": None}


class DrawAnalyzer:
    def __init__(self, cap: Capture):
        self.cap = cap
        self.rootsigs: dict[int, dict] = {}
        self.pso_bindings: dict[int, dict[str, list | None]] = {}   # PSO -> {ステージ: シェーダが使うバインディング}
        self.heaps: dict[int, dict[int, dict]] = {}       # heap -> {slot: descriptor}
        self.heap_size: dict[int, int] = {}               # heap -> 既知の最大スロット + 1 (上限なしレンジ用)
        self._writes: list = []                           # 2033 (seq 順)
        self._next_write = 0
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
                    except Exception:  # noqa: BLE001 - 解析不能な blob は無視
                        pass
            elif c.api.endswith("PipelineState") and c.api.split("::")[-1].startswith("Create") and c.ok:
                raw = self.cap.raw_args(c)
                pso = (raw.get("ppPipelineState") or {}).get("obj")
                desc = raw.get("pDesc") or {}
                if pso:
                    self.pso_bindings[pso] = {
                        stage: used_bindings(bytes(bc)) for stage in SHADER_STAGES
                        if (bc := (desc.get(stage) or {}).get("pShaderBytecode"))}
            elif c.api == "PIX::DescriptorHeap" and c.ok:
                self.heaps[c.args["ppHeap"]] = dict(enumerate(c.args.get("descriptors") or []))
                self.heap_size[c.args["ppHeap"]] = len(c.args.get("descriptors") or [])
            elif c.api == "PIX::WriteDescriptors" and c.ok:
                self._writes.append(c)

    def _apply_writes(self, before_seq: int) -> None:
        """seq < before_seq の 2033 を適用する (コマンドリストは ExecuteCommandLists の seq 順に処理される)。"""
        while self._next_write < len(self._writes) and self._writes[self._next_write].seq < before_seq:
            a = self._writes[self._next_write].args
            heap = self.heaps.setdefault(a["heap"], {})
            for i, dsc in enumerate(a.get("descriptors") or []):
                heap[a["index"] + i] = dsc
            self.heap_size[a["heap"]] = max(self.heap_size.get(a["heap"], 0), a["index"] + len(a.get("descriptors") or []))
            self._next_write += 1

    def name(self, oid: int | None) -> str:
        return self.cap.object_name(oid)

    def _resolve_table(self, rs: dict | None, param_index: int, handle: dict, used: list | None) -> list[dict]:
        heap = self.heaps.get(handle["heap"], {})
        out = []
        ranges = []
        if rs and param_index < len(rs["parameters"]):
            ranges = rs["parameters"][param_index].get("ranges", [])
        if not ranges:
            ranges = [{"type": "?", "num": 1, "offset": 0, "baseRegister": None, "space": 0}]
        for rg in ranges:
            size = self.heap_size.get(handle["heap"], 0)
            num = rg["num"] if rg["num"] != 0xFFFFFFFF else max(0, size - handle["index"] - rg["offset"])
            for k in range(min(num, 64)):
                slot = handle["index"] + rg["offset"] + k
                d = heap.get(slot, {"kind": "OutOfSnapshot"})
                reg = None if rg["baseRegister"] is None else \
                    f"{REG_PREFIX.get(rg['type'], '?')}{rg['baseRegister'] + k}"
                out.append({"register": reg, "space": rg.get("space"), "heapSlot": slot, "kind": d.get("kind"),
                            "resource": self.name(d.get("resource")) if d.get("resource") else None,
                            "view": d.get("view"), "used": is_used(used, reg, rg.get("space"))})
        return out

    @staticmethod
    def _visible_bindings(stages: dict[str, list | None] | None, visibility: str | None) -> list | None:
        """visibility のステージが使うバインディングの和。PSO 不明・判定できないステージがあれば None。"""
        if stages is None:
            return None
        out: list = []
        for s in VISIBLE_STAGES.get(visibility or "ALL", SHADER_STAGES):
            if s in stages:
                if stages[s] is None:
                    return None
                out += stages[s]
        return out

    def _bindings(self, st: dict, pipe: str, stages: dict | None) -> list[dict]:
        rs_id = st["rootSig"][pipe]
        rs = self.rootsigs.get(rs_id)
        out = []
        for idx in sorted(st["root"][pipe]):
            kind, val = st["root"][pipe][idx]
            p = rs["parameters"][idx] if rs and idx < len(rs["parameters"]) else {}
            b: dict[str, Any] = {"rootParam": idx, "type": kind, "visibility": p.get("visibility")}
            if kind == "table":
                # compute パイプラインでは ShaderVisibility に関係なく CS が全パラメータを見る
                vis = "ALL" if pipe == "Compute" else p.get("visibility")
                b["descriptors"] = self._resolve_table(rs, idx, val, self._visible_bindings(stages, vis))
            elif kind == "constants":
                b["register"] = f"b{p['register']}" if "register" in p else None
                b["values"] = val
            else:
                b["register"] = f"{REG_PREFIX[kind]}{p['register']}" if "register" in p else None
                b["gpuva"] = {"resource": self.name(val["resource"]), "offset": val["offset"]}
            out.append(b)
        return out

    def _indirect(self, c) -> tuple[str, list | None]:
        """ExecuteIndirect のパイプライン種別と、PIX が記録した実際の間接引数。"""
        seq = (c.args.get("_pix") or {}).get("argumentsSeq")
        snap = self.cap.call_by_seq(seq, "PIX::IndirectArguments") if seq is not None else None
        sig = self.cap.objects.get((c.args.get("pCommandSignature") or {}).get("obj"), {}).get("desc") or {}
        types = {a.get("Type") for a in sig.get("pArgumentDescs") or []}
        pipe = "Compute" if types & COMPUTE_INDIRECT_TYPES else "Graphics"
        return pipe, (snap.args.get("commands") if snap else None)

    def _shader_table_exports(self, c) -> list | None:
        seq = (c.args.get("_pix") or {}).get("shaderTablesSeq")
        snap = self.cap.call_by_seq(seq, "PIX::ShaderTables") if seq is not None else None
        return snap.args.get("exports") if snap else None

    def _process(self, c, st: dict, cl: dict, draws: list, bundle: str | None = None) -> dict:
        m = c.api.split("::")[-1]
        a = c.args
        if m == "Reset":
            st = _new_state()
            st["pso"] = (a.get("pInitialState") or {}).get("obj") or None
        elif m == "SetPipelineState":
            st["pso"] = a["pPipelineState"]["obj"]
            st["stateObject"] = None
        elif m == "SetPipelineState1":
            st["stateObject"] = a["pStateObject"]["obj"]
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
        elif m == "BeginRenderPass":
            st["rtv"] = [{"resource": self.name(t["cpuDescriptor"]["resource"]), "view": t["cpuDescriptor"]["view"]}
                         for t in a.get("pRenderTargets") or []]
            ds = a.get("pDepthStencil")
            st["dsv"] = None if ds is None else {"resource": self.name(ds["cpuDescriptor"]["resource"]),
                                                 "view": ds["cpuDescriptor"]["view"]}
            st["renderPass"] = True
        elif m == "EndRenderPass":
            st["rtv"], st["dsv"], st["renderPass"] = [], None, False
        elif m == "RSSetViewports":
            st["viewports"] = a.get("pViewports")
        elif m == "RSSetScissorRects":
            st["scissors"] = a.get("pRects")
        elif m == "ExecuteBundle":
            bname = self.name(a["pCommandList"]["obj"])
            for bc in self.cap.bundle_commands(c):
                st = self._process(bc, st, cl, draws, bundle=bname)
        elif m in DRAW_APIS:
            if m == "ExecuteIndirect":
                pipe, indirect = self._indirect(c)
            else:
                pipe = "Compute" if m in ("Dispatch", "DispatchRays") else "Graphics"
                indirect = None
            d: dict[str, Any] = {
                "seq": c.seq, "queue": self.name(cl["queue"]), "commandList": self.name(cl["list"]),
                "bundle": bundle, "call": m,
                "args": {k: v for k, v in a.items() if k != "_pix"},
                "pso": self.name(st["stateObject"] if m == "DispatchRays" else st["pso"]),
                "rootSignature": self.name(st["rootSig"][pipe]),
                # DispatchRays はステートオブジェクト (ライブラリ) なので使用判定しない
                "bindings": self._bindings(st, pipe, None if m == "DispatchRays" else self.pso_bindings.get(st["pso"])),
            }
            if m == "DispatchRays":
                d["shaderTableExports"] = self._shader_table_exports(c)
            if m == "ExecuteIndirect":
                d["indirectCommands"] = indirect
            if pipe == "Graphics":
                d.update(topology=st["topology"], vertexBuffers=copy.deepcopy(st["vb"]), indexBuffer=st["ib"],
                         renderTargets=st["rtv"], depthStencil=st["dsv"], inRenderPass=st["renderPass"],
                         viewports=st["viewports"], scissors=st["scissors"])
            draws.append(d)
        return st

    def run(self) -> list[dict]:
        draws: list[dict] = []
        for cl in self.cap.command_lists():
            self._apply_writes(cl["executeSeq"])
            st = _new_state()
            for c in cl["commands"]:
                st = self._process(c, st, cl, draws)
        return draws


def analyze_draws(cap: Capture) -> list[dict]:
    return DrawAnalyzer(cap).run()
