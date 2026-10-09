"""gcap.exe wpix shaders (シェーダ PDB 探索) のテスト。

前提: captures/pix_dxil/v0.wpix (`scripts/capture_pix.py --variants 0 --out captures/pix_dxil --app-args --dxil`)
      bin/shaders/pdb/*.pdb (DX12CaptureTarget のビルドで dxc が生成)
各シナリオで PDB を一時フォルダに配置し、探索方法ごとに正しく解決 / 打ち切り / IL 書き出しされるかを確認する。
"""
from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_csharp_parity import EXE  # noqa: E402

CAPTURE = ROOT / "captures" / "pix_dxil" / "v0.wpix"
PDB_DIR = ROOT / "bin" / "shaders" / "pdb"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass


@unittest.skipUnless(EXE and CAPTURE.exists() and PDB_DIR.exists(), "gcap.exe / DXIL capture / PDBs not found")
class TestShaderSymbols(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcap_sym_"))
        self.cap_dir = self.tmp / "cap"
        self.cap_dir.mkdir()
        self.cap = self.cap_dir / "frame.wpix"
        shutil.copy(CAPTURE, self.cap)
        self.pdbs = {p.name: p for p in PDB_DIR.glob("*.pdb")}
        self.assertEqual(len(self.pdbs), 3)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_shaders(self, *extra: str) -> dict:
        out = self.tmp / "out"
        shutil.rmtree(out, ignore_errors=True)
        r = subprocess.run([str(EXE), "wpix", "shaders", str(self.cap), "-o", str(out), *extra],
                           capture_output=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
        rep = json.loads((out / "shaders.json").read_text("utf-8"))
        rep["_out"] = out
        return rep

    def by_name(self, rep: dict) -> dict:
        return {s["debugName"]: s for s in rep["shaders"]}

    def names(self) -> list[str]:
        return sorted(self.pdbs)

    # ---------------------------------------------------------------------------------------------
    def test_missing_writes_il(self):
        rep = self.run_shaders()
        self.assertEqual(rep["resolved"], 0)
        for s in rep["shaders"]:
            self.assertFalse(s["resolved"])
            il = rep["_out"] / s["il"]
            self.assertGreater(il.stat().st_size, 100)
            self.assertIn("define void", il.read_text("utf-8"))   # DXIL (LLVM IR) の逆アセンブル

    def test_capture_dir_recursive(self):
        deep = self.cap_dir / "a" / "b" / "c"
        deep.mkdir(parents=True)
        for n, p in self.pdbs.items():
            shutil.copy(p, deep / n)
        rep = self.run_shaders()
        self.assertEqual(rep["resolved"], 3)
        for s in rep["shaders"]:
            self.assertEqual((s["foundBy"], s["matchedBy"]), ("capture-dir", "hash(pdbutils)"))
            self.assertTrue((rep["_out"] / s["pdb"]).exists())
            self.assertTrue(s["sources"])   # PDB から HLSL ソースを取り出せる
            self.assertNotIn("il", s)       # 解決済みは IL を書かない (--il-all で書く)

    def test_folder_store_and_order(self):
        a, b, c = self.names()
        folder = self.tmp / "folder" / "x"
        folder.mkdir(parents=True)
        shutil.copy(self.pdbs[a], folder / a)
        # ストア形式 (<root>/<name>/<HASH>/<name>)
        store = self.tmp / "store"
        hash_b = b.removesuffix(".pdb").upper()
        (store / b / hash_b).mkdir(parents=True)
        shutil.copy(self.pdbs[b], store / b / hash_b / b)
        rep = self.run_shaders("--symbols", str(self.tmp / "folder"), "--symbol-server", str(store))
        s = self.by_name(rep)
        self.assertEqual(s[a]["foundBy"], "folder")
        self.assertEqual(s[b]["foundBy"], "symbol-store")
        self.assertFalse(s[c]["resolved"])
        self.assertTrue((rep["_out"] / s[c]["il"]).exists())

    def test_http_symbol_server_with_cache(self):
        a = self.names()[0]
        root = self.tmp / "http"
        h = a.removesuffix(".pdb").upper()
        (root / a / h).mkdir(parents=True)
        shutil.copy(self.pdbs[a], root / a / h / a)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(root)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cache = self.tmp / "cache"
            rep = self.run_shaders("--symbol-server", f"srv*{cache}*http://127.0.0.1:{srv.server_address[1]}")
        finally:
            srv.shutdown()
            srv.server_close()
        s = self.by_name(rep)
        self.assertEqual(s[a]["foundBy"], "symbol-server")
        self.assertEqual(s[a]["matchedBy"], "hash(pdbutils)")
        self.assertTrue((cache / a / h / a).exists())   # cache にストア形式で保存される

    def test_stop_when_all_found(self):
        for n, p in self.pdbs.items():
            shutil.copy(p, self.cap_dir / n)
        decoys = self.tmp / "decoys"
        decoys.mkdir()
        for i in range(5):
            (decoys / f"decoy{i}.pdb").write_bytes(b"not a pdb" * 10)
        rep = self.run_shaders("--symbols", str(decoys))
        self.assertEqual(rep["resolved"], 3)
        self.assertEqual(rep["searched"]["filesExamined"], 3)   # decoys は見に行かない (打ち切り)

    def test_renamed_and_stale_pdb(self):
        a, b, _ = self.names()
        folder = self.tmp / "folder"
        folder.mkdir()
        shutil.copy(self.pdbs[a], folder / "renamed_symbols.pdb")   # 名前が違っても hash で特定
        shutil.copy(self.pdbs[a], folder / b)                       # 名前は b だが中身は a (stale)
        rep = self.run_shaders("--symbols", str(folder))
        s = self.by_name(rep)
        self.assertTrue(s[a]["resolved"])
        self.assertFalse(s[b]["resolved"])   # 名前一致でもハッシュ不一致なら採用しない


if __name__ == "__main__":
    unittest.main()
