"""拡張機能 (--extras) と複数フレームキャプチャのデコード検証 (Python リファレンス実装)。

captures/pix_ext/vN.wpix   : scripts/capture_pix.py --variants 0 1 --out captures/pix_ext --app-args --extras --dxil
captures/pix_multi/v0.wpix : scripts/capture_pix.py --variants 0 --out captures/pix_multi --app-args --capture-count 2
"""
from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from gcap.wpix.capture import Capture  # noqa: E402
from gcap.wpix.decoder import ApiDecoder  # noqa: E402
from gcap.wpix.state import analyze_draws  # noqa: E402

EXT_DIR = ROOT / "captures" / "pix_ext"
MULTI = ROOT / "captures" / "pix_multi" / "v0.wpix"
EXT_VARIANTS = sorted(int(p.stem[1:]) for p in EXT_DIR.glob("v*.wpix") if p.stem[1:].isdigit())
_DEC = None


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def load(path: Path) -> Capture:
    global _DEC
    if _DEC is None:
        _DEC = ApiDecoder()
    return Capture.open(path, _DEC)


@unittest.skipUnless(EXT_VARIANTS, "no extras captures")
class TestExtras(unittest.TestCase):
    def test_all(self):
        for v in EXT_VARIANTS:
            with self.subTest(variant=v):
                cap = load(EXT_DIR / f"v{v}.wpix")
                m = json.loads((EXT_DIR / f"v{v}.json").read_text())["extras"]
                self._check(cap, m)

    def name_of(self, cap: Capture, oid) -> str:
        return cap.objects.get(oid, {}).get("name") or ""

    def _check(self, cap: Capture, m: dict):
        self.assertEqual([(c.seq, c.api, c.error) for c in cap.failed()], [])
        sup = m["supported"]
        by_name = {o.get("name"): oid for oid, o in cap.objects.items() if o.get("name")}
        draws = analyze_draws(cap)

        # ---- 非同期キュー (Copy / Compute) と Fence 同期 ----
        lists = cap.command_lists()
        queues = {self.name_of(cap, cl["queue"]) for cl in lists}
        self.assertTrue({"GCA_CopyQueue", "GCA_ComputeQueue", "GCA_DirectQueue"} <= queues)
        async_d = next(d for d in draws if "GCA_ComputeQueue" in d["queue"])
        self.assertEqual(async_d["args"]["ThreadGroupCountX"], m["asyncDispatchX"])
        self.assertIn("GCA_AsyncUav", async_d["bindings"][0]["gpuva"]["resource"])
        waits = [c for c in cap.find("::Wait") if self.name_of(cap, c.args["pFence"]["obj"]) == "GCA_ComputeFence"]
        self.assertTrue(waits)

        # ---- Placed / Reserved ----
        placed = cap.find("PIX::CreatePlacedResource")[0].args
        self.assertEqual(placed["heapOffset"], 65536)
        self.assertEqual(self.name_of(cap, placed["heap"]), "GCA_PlacedHeap")
        copies = {self.name_of(cap, c.args["pDstBuffer"]["obj"]): c.args["NumBytes"] for c in cap.find("::CopyBufferRegion")}
        self.assertEqual(copies["GCA_PlacedBuffer"], m["placedCopyBytes"])
        if sup["tiledResources"]:
            self.assertTrue(cap.find("PIX::CreateReservedResource"))
            utm = cap.find("::UpdateTileMappings")[0].args
            self.assertEqual(utm["pResourceRegionStartCoordinates"][0]["X"], 1)

        # ---- Enhanced Barriers ----
        if sup["enhancedBarriers"]:
            b = cap.find("::Barrier")
            self.assertEqual(len(b), 2)
            g = b[0].args["pBarrierGroups"]
            self.assertEqual([x["Type"] for x in g],
                             ["D3D12_BARRIER_TYPE_GLOBAL", "D3D12_BARRIER_TYPE_BUFFER", "D3D12_BARRIER_TYPE_TEXTURE"])
            tex = g[2]["pTextureBarriers"]
            self.assertEqual(tex[0]["LayoutAfter"], "D3D12_BARRIER_LAYOUT_COPY_DEST")
            self.assertEqual(tex[1]["Subresources"]["IndexOrFirstMipLevel"], 1)
            res = {self.name_of(cap, c.args["resource"]): c.args for c in cap.find("PIX::CreateResource")}
            self.assertEqual(res["GCA_EBTexture"]["layoutAtCaptureStart"], "D3D12_BARRIER_LAYOUT_COMMON")
            self.assertEqual(res["GCA_EBTexture2"]["layoutAtCaptureStart"], "D3D12_BARRIER_LAYOUT_SHADER_RESOURCE")
            self.assertEqual(len(res["GCA_EBTexture2"]["subresourceLayoutsAtCapture"]), 2)

        # ---- DXR ----
        if sup["raytracing"]:
            so = cap.find("PIX::CreateStateObject")[0].args["pDesc"]
            types = [s["Type"] for s in so["pSubobjects"]]
            self.assertIn("D3D12_STATE_SUBOBJECT_TYPE_HIT_GROUP", types)
            hit = next(s["desc"] for s in so["pSubobjects"] if s["Type"].endswith("HIT_GROUP"))
            self.assertEqual((hit["HitGroupExport"], hit["ClosestHitShaderImport"]), ("GCA_HitGroup", "ClosestHit"))
            inst = cap.find("PIX::InstanceDescs")[0].args["instances"][0]
            self.assertEqual(inst["InstanceID"], m["tlasInstanceId"])
            self.assertEqual(self.name_of(cap, inst["AccelerationStructure"]["resource"]), "GCA_BLAS")
            rays = next(d for d in draws if d["call"] == "DispatchRays")
            self.assertEqual([rays["args"]["pDesc"][k] for k in ("Width", "Height", "Depth")], m["rays"])
            self.assertIn("GCA_RTPSO", rays["pso"])
            self.assertEqual(rays["shaderTableExports"][:3], ["RayGen", "Miss", "GCA_HitGroup"])
            consts = next(b for b in rays["bindings"] if b["type"] == "constants")["values"]
            self.assertEqual(consts, struct.pack("<4f", *m["missColor"]).hex())
            self.assertTrue(cap.find("PIX::AccelerationStructureData"))

        # ---- ExecuteIndirect (実引数は PIX::IndirectArguments から) ----
        ei = [d for d in draws if d["call"] == "ExecuteIndirect"]
        disp = next(d for d in ei if d["indirectCommands"][0][0]["type"].endswith("DISPATCH"))
        a = disp["indirectCommands"][0][0]["args"]
        self.assertEqual([a["ThreadGroupCountX"], a["ThreadGroupCountY"], a["ThreadGroupCountZ"]], m["indirectDispatch"])
        drw = next(d for d in ei if d["indirectCommands"][0][0]["type"].endswith("_DRAW"))
        got = [[c[0]["args"][k] for k in ("VertexCountPerInstance", "InstanceCount", "StartVertexLocation",
                                          "StartInstanceLocation")] for c in drw["indirectCommands"]]
        self.assertEqual(got, m["indirectDraw"])

        # ---- RenderPass / Bundle / Mesh / Stream PSO / フレーム内ディスクリプタ ----
        brp = cap.find("::BeginRenderPass")[0].args
        rt = brp["pRenderTargets"][0]
        self.assertEqual(rt["BeginningAccess"]["Clear"]["ClearValue"]["Color"], [f32(x) for x in m["extClearColor"]])
        self.assertEqual(brp["pDepthStencil"]["DepthBeginningAccess"]["Clear"]["ClearValue"]["DepthStencil"]["Depth"],
                         f32(m["extDepthClear"]))
        rp_draw = next(d for d in draws if d["call"] == "DrawInstanced" and d.get("inRenderPass") and not d["bundle"])
        self.assertIn("GCA_ExtRT", rp_draw["renderTargets"][0]["resource"])
        srv = [x["resource"] for b in rp_draw["bindings"] if b["type"] == "table" for x in b["descriptors"]]
        self.assertIn("GCA_ExtTexB", srv[0])   # フレーム内 CreateShaderResourceView
        self.assertIn("GCA_ExtTexC", srv[1])   # フレーム内 CopyDescriptorsSimple
        bundle = next(d for d in draws if d["bundle"])
        self.assertEqual(bundle["args"]["InstanceCount"], m["bundleInstances"])
        if sup["meshShader"]:
            mesh = [d["args"]["ThreadGroupCountX"] for d in draws if d["call"] == "DispatchMesh"]
            self.assertEqual(mesh, [m["meshGroups"], m["ampGroups"]])
            psos = {self.name_of(cap, c.args["ppPipelineState"]["obj"]): c.args["pDesc"]
                    for c in cap.find("::CreatePipelineState")}
            self.assertIsNotNone(psos["GCA_AmpMeshPSO"]["AS"]["pShaderBytecode"])
            self.assertIsNotNone(psos["GCA_AmpMeshPSO"]["MS"]["pShaderBytecode"])
            self.assertIsNotNone(psos["GCA_StreamComputePSO"]["CS"]["pShaderBytecode"])
            self.assertEqual(psos["GCA_StreamPSO"]["DSVFormat"], "DXGI_FORMAT_D32_FLOAT")
        scs = next(d for d in draws if "GCA_StreamComputePSO" in d["pso"])
        self.assertEqual(scs["args"]["ThreadGroupCountX"], m["streamCsGroups"])

        # ---- Query ----
        q = cap.find("PIX::QueryResults")
        self.assertEqual(len(q[0].args["values"]), 2)   # timestamp x2
        self.assertGreater(q[1].args["values"][0], 0)   # occlusion (サンプル数)

        # ---- PIX3 マーカー ----
        texts = [c.args.get("_text") for c in cap.find("::BeginEvent") + cap.find("::SetMarker")]
        self.assertIn(m["pix3Begin"], texts)
        self.assertIn(m["pix3Marker"], texts)


@unittest.skipUnless(MULTI.exists(), "no multi-frame capture")
class TestMultiFrame(unittest.TestCase):
    def test_two_frames(self):
        cap = load(MULTI)
        self.assertEqual(cap.failed(), [])
        self.assertEqual(len(cap.find("PIX::Present")), 2)
        main = [cl for cl in cap.command_lists() if len(cl["commands"]) > 10]
        self.assertEqual(len(main), 2)
        self.assertEqual(main[0]["commands"][-1].api.split("::")[-1], "Close")
        draws = analyze_draws(cap)
        self.assertEqual(sum(1 for d in draws if d["call"] == "DrawIndexedInstanced"), 2)


if __name__ == "__main__":
    unittest.main()
