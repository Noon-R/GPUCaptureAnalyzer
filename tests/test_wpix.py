"""PIX .wpix デコーダの回帰テスト。

captures/pix/vN.wpix (scripts/capture_pix.py で生成) を独自デコードし、
  1) アプリが埋め込んだ既知値 (vN.json = manifest) と一致するか
  2) PIX 自身の解釈 (vN_cpp/ = pixtool export-to-cpp) と一致するか
を検証する。キャプチャが無い場合は skip。

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import re
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from gcap.wpix.capture import Capture  # noqa: E402
from gcap.wpix.decoder import ApiDecoder  # noqa: E402

CAP_DIR = ROOT / "captures" / "pix"
VARIANTS = sorted(int(p.stem[1:]) for p in CAP_DIR.glob("v*.wpix") if p.stem[1:].isdigit())
_DECODER = None


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def load(v: int) -> tuple[Capture, dict]:
    global _DECODER
    if _DECODER is None:
        _DECODER = ApiDecoder()
    cap = Capture.open(CAP_DIR / f"v{v}.wpix", _DECODER)
    man = json.loads((CAP_DIR / f"v{v}.json").read_text())
    return cap, man


def one(cap: Capture, api: str, pred=lambda c: True):
    cs = [c for c in cap.find(api) if pred(c)]
    assert len(cs) >= 1, f"{api} not found"
    return cs[0]


@unittest.skipUnless(VARIANTS, "no captures (run scripts/capture_pix.py)")
class TestManifest(unittest.TestCase):
    """独自デコード結果 == アプリが埋め込んだ既知値"""

    def test_all(self):
        for v in VARIANTS:
            with self.subTest(variant=v):
                cap, m = load(v)
                self._check(cap, m)

    def _check(self, cap: Capture, m: dict):
        # すべてのレコードが過不足なくデコードできる
        self.assertEqual([(c.seq, c.api, c.error) for c in cap.failed()], [])

        d = one(cap, "::Dispatch").args
        self.assertEqual([d["ThreadGroupCountX"], d["ThreadGroupCountY"], d["ThreadGroupCountZ"]], m["dispatch"])
        vc, ic, idx = m["drawVertexCount_InstanceCount_IndexCount"]
        d = one(cap, "::DrawInstanced").args
        self.assertEqual((d["VertexCountPerInstance"], d["InstanceCount"]), (vc, ic))
        self.assertEqual(one(cap, "::DrawIndexedInstanced").args["IndexCountPerInstance"], idx)
        self.assertEqual(one(cap, "::CopyBufferRegion").args["NumBytes"], m["copyBytes"])

        # クリア値 (backbuffer / offscreen RT / depth)
        clears = cap.find("::ClearRenderTargetView")
        bb = next(c for c in clears if c.args["RenderTargetView"]["view"] is None)
        self.assertEqual(bb.args["ColorRGBA"], [f32(x) for x in m["clearColor"]])
        off = next(c for c in clears if c.args["RenderTargetView"]["view"] is not None)
        self.assertEqual(off.args["ColorRGBA"], [f32(x) for x in m["rtClearColor"]])
        self.assertEqual(off.args["RenderTargetView"]["view"]["Texture2D"]["MipSlice"], 1)
        self.assertEqual(off.args["pRects"], [[0, 0, 16, 8]])
        ds = one(cap, "::ClearDepthStencilView").args
        self.assertEqual(ds["Depth"], f32(m["depthClear"]))
        self.assertEqual(ds["Stencil"], m["stencilClear"])

        # root constants / viewport / scissor / OM state
        rc = struct.pack("<4I", *m["rootConstants"]).hex()
        self.assertEqual(one(cap, "::SetComputeRoot32BitConstants").args["pSrcData"]["hex"], rc)
        self.assertEqual(one(cap, "::SetGraphicsRoot32BitConstants").args["pSrcData"]["hex"], rc)
        vp = one(cap, "::RSSetViewports").args["pViewports"][0]
        self.assertEqual([vp["TopLeftX"], vp["TopLeftY"], vp["Width"], vp["Height"]], m["viewport"])
        self.assertEqual(one(cap, "::RSSetScissorRects").args["pRects"][0], m["scissor"])
        self.assertEqual(one(cap, "::OMSetStencilRef").args["StencilRef"], m["stencilRef"])
        self.assertEqual(one(cap, "::OMSetBlendFactor").args["BlendFactor"], [f32(x) for x in m["blendFactor"]])
        self.assertEqual(one(cap, "::ClearUnorderedAccessViewUint").args["Values"], m["uavClear"])

        # PIX マーカー文字列
        texts = []
        for c in cap.find("::BeginEvent") + cap.find("::SetMarker"):
            texts.append(bytes.fromhex(c.args["pData"]["hex"]).decode("utf-16-le").rstrip("\x00"))
        self.assertEqual(sorted(texts), sorted(m["markers"]))

        # オブジェクト名 (参照されたオブジェクトのみ記録される)
        names = {o.get("name") for o in cap.objects.values()}
        for n in ("GCA_Device", "GCA_DirectQueue", "GCA_VertexBuffer", "GCA_ConstantBuffer", "GCA_Texture8x8",
                  "GCA_GraphicsPSO", "GCA_ComputePSO", "GCA_Depth", "GCA_OffscreenRT", "GCA_SrvUavHeap"):
            self.assertIn(n, names)

        # リソース初期データ
        by_name = {cap.objects.get(rid, {}).get("name"): d for rid, d in cap.resource_initial_data()}
        cb = by_name["GCA_ConstantBuffer"]["data"]
        self.assertEqual(struct.unpack_from("<4I", cb, 0), tuple(m["cbMagic"]))
        self.assertEqual(struct.unpack_from("<4f", cb, 16), tuple(f32(x) for x in m["cbFloats"]))
        src = by_name["GCA_CopySource"]["data"]
        self.assertEqual(struct.unpack_from("<4I", src, 0), tuple(m["uploadMagic"] + i for i in range(4)))
        tex = by_name["GCA_Texture8x8"]
        pitch = tex["footprints"][0]["Footprint"]["RowPitch"]
        for y in range(8):
            for x in range(8):
                self.assertEqual(struct.unpack_from("<I", tex["data"], y * pitch + x * 4)[0],
                                 m["textureMagic"] | (y << 4) | x)
        dep = by_name["GCA_Depth"]
        self.assertEqual(len(dep["footprints"]), 2)   # D24S8 = depth plane + stencil plane
        d24 = struct.unpack_from("<I", dep["data"], 0)[0] & 0xFFFFFF
        self.assertAlmostEqual(d24 / 0xFFFFFF, m["depthClear"], places=5)
        self.assertEqual(dep["data"][dep["footprints"][1]["Offset"]], m["stencilClear"])
        self.assertEqual(len(by_name["GCA_OffscreenRT"]["footprints"]), 3)   # 3 mips

        # リソース生成 (optimized clear value)
        cr = {cap.objects.get(c.args["resource"], {}).get("name"): c.args for c in cap.find("PIX::CreateResource")}
        self.assertEqual(cr["GCA_Depth"]["optimizedClearValue"]["DepthStencil"]["Depth"], f32(m["depthClear"]))
        self.assertEqual(cr["GCA_OffscreenRT"]["optimizedClearValue"]["Color"], [f32(x) for x in m["rtClearColor"]])
        self.assertEqual(cr["GCA_OffscreenRT"]["subresourceStatesAtCapture"], [4, 4, 4])

        # ディスクリプタヒープ
        heap = one(cap, "PIX::DescriptorHeap").args
        self.assertEqual(heap["desc"]["NumDescriptors"], m["srvHeapSize"])
        kinds = [x["kind"] for x in heap["descriptors"]]
        self.assertEqual(kinds[:6], ["SRV", "UAV", "CBV", "SRV", "SRV", "UAV"])
        self.assertEqual(kinds[-1], "SRV")
        self.assertTrue(all(k == "Empty" for k in kinds[6:-1]))
        self.assertEqual(heap["descriptors"][3]["view"]["Buffer"]["FirstElement"], 2)

        # スクリーンショット
        blobs = list(cap.blobs())
        self.assertTrue(blobs and blobs[0]["data"].startswith(b"\x89PNG"))


_RE_CALL = re.compile(r"GetCommandList\((\d+)\)->(\w+)\(([^;]*)\);")


@unittest.skipUnless(VARIANTS, "no captures")
class TestOracle(unittest.TestCase):
    """独自デコード結果 == PIX export-to-cpp が生成した C++ (オブジェクト ID も含めて一致)"""

    def test_all(self):
        for v in VARIANTS:
            cpp_dir = CAP_DIR / f"v{v}_cpp"
            if not cpp_dir.exists():
                continue
            with self.subTest(variant=v):
                cap, _ = load(v)
                cpp = "\n".join(p.read_text(errors="replace") for p in cpp_dir.glob("CommandLists_*.cpp"))
                expected = [(int(a), n, s) for a, n, s in _RE_CALL.findall(cpp)
                            if n in ("Dispatch", "DrawInstanced", "DrawIndexedInstanced", "CopyBufferRegion",
                                     "OMSetStencilRef", "SetPipelineState", "SetComputeRootSignature",
                                     "SetGraphicsRootSignature")]
                self.assertTrue(expected)
                for lst, name, argstr in expected:
                    got = [c for c in cap.find("::" + name) if c.this == lst]
                    self.assertTrue(got, f"{name} on list {lst} not decoded")
                    a = got[0].args
                    if name == "Dispatch":
                        ex = [int(x) for x in argstr.split(",")]
                        self.assertEqual([a["ThreadGroupCountX"], a["ThreadGroupCountY"], a["ThreadGroupCountZ"]], ex)
                    elif name == "DrawInstanced":
                        self.assertEqual([a["VertexCountPerInstance"], a["InstanceCount"], a["StartVertexLocation"],
                                          a["StartInstanceLocation"]], [int(x) for x in argstr.split(",")])
                    elif name == "CopyBufferRegion":
                        ids = [int(x) for x in re.findall(r"GetResource\((\d+)\)", argstr)]
                        nums = [int(x) for x in re.sub(r"GetResource\(\d+\)\.Get\(\)", "", argstr).split(",") if x.strip()]
                        self.assertEqual([a["pDstBuffer"]["obj"], a["pSrcBuffer"]["obj"]], ids)
                        self.assertEqual([a["DstOffset"], a["SrcOffset"], a["NumBytes"]], nums)
                    elif name in ("SetPipelineState",):
                        ids = {int(x) for x in re.findall(r"GetPipelineState\((\d+)\)", argstr)}
                        self.assertTrue(ids & {c.args["pPipelineState"]["obj"] for c in got})
                    elif name.endswith("RootSignature"):
                        ids = {int(x) for x in re.findall(r"GetRootSignature\((\d+)\)", argstr)}
                        self.assertTrue(ids & {c.args["pRootSignature"]["obj"] for c in got})


if __name__ == "__main__":
    unittest.main()
