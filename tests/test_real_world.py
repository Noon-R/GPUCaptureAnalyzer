"""実アプリ (自作サンプル以外) のキャプチャに対する回帰テスト。

captures/real/*.wpix を対象にする (scripts/capture_app.py で取得。git 管理外なので無ければ skip)。
例: UE5 のパッケージ済みゲームを -dx12 で 1 フレーム (49k レコード, 展開後 2.7GB)。

  * 全レコードを過不足なくデコードできる (C# / Python とも)
  * C# と Python の calls / draws が一致
  * シェーダが使うディスクリプタは全て解決でき、レジスタ種別 (t/u/s/b) とディスクリプタ種別が一致する
    (2033 の適用タイミングと PSV0/RDEF の読み取りの検証)
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_csharp_parity import EXE, ROOT, normalize, run_exe, run_py  # noqa: E402

CAPTURES = sorted((ROOT / "captures" / "real").glob("*.wpix"))
KIND_OF_REGISTER = {"t": "SRV", "u": "UAV", "s": "Sampler", "b": "CBV"}


@unittest.skipUnless(EXE and CAPTURES, "gcap.exe or captures/real/*.wpix not found")
class TestRealWorld(unittest.TestCase):
    def test_calls_decode_and_parity(self):
        for cap in CAPTURES:
            with self.subTest(cap=cap.name):
                cs = [json.loads(x) for x in run_exe("wpix", "calls", str(cap), "--json").splitlines() if x]
                py = [json.loads(x) for x in run_py("wpix", "calls", str(cap), "--json").splitlines() if x]
                failed = [f"{r['seq']}:{r['api']}:{r.get('error')}" for r in cs if not r.get("ok", True)]
                self.assertEqual(failed, [], "undecoded records")
                self.assertEqual(len(cs), len(py))
                for a, b in zip(cs, py):
                    self.assertEqual(normalize(a), normalize(b), f"seq={a['seq']} api={a['api']}")

    def test_draws_bindings(self):
        for cap in CAPTURES:
            with self.subTest(cap=cap.name):
                cs = json.loads(run_exe("wpix", "draws", str(cap), "--json"))
                self.assertEqual(normalize(cs), normalize(json.loads(run_py("wpix", "draws", str(cap), "--json"))))
                used = bad = 0
                for d in cs:
                    for b in d.get("bindings", []):
                        for e in b.get("descriptors", []):
                            if not e["used"]:
                                continue
                            used += 1
                            if e["kind"] != KIND_OF_REGISTER.get((e["register"] or "?")[0]):
                                bad += 1
                self.assertGreater(used, 0)
                self.assertEqual(bad, 0, f"{bad}/{used} used descriptors unresolved or of the wrong kind")


if __name__ == "__main__":
    unittest.main()
