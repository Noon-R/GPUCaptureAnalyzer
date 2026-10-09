"""C# 版 gcap.exe と Python 版 (検証用リファレンス実装) の出力一致テスト。

  * wpix calls --json  : 全レコードのデコード結果 (seq/api/this/hr/args/ok) が一致
  * wpix draws --json  : Draw/Dispatch 毎のバインディング解決結果が一致
  * wpix extract       : 抽出ファイル (リソース/シェーダ/ルートシグネチャ/PNG以外の blob) がバイト一致

gcap.exe の場所: 環境変数 GCAP_EXE、無ければ src/gcap の publish / build 出力を探す。見つからなければ skip。

    python -m unittest tests.test_csharp_parity -v
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from gcap.__main__ import main as py_main  # noqa: E402

CAP_DIR = ROOT / "captures" / "pix"
CAPTURES = [p for d in ("pix", "pix_dxil", "pix_ext", "pix_multi") for p in sorted((ROOT / "captures" / d).glob("v*.wpix"))]


def find_exe() -> Path | None:
    env = os.environ.get("GCAP_EXE")
    if env and Path(env).exists():
        return Path(env)
    base = ROOT / "src" / "gcap" / "bin" / "Release" / "net10.0-windows" / "win-x64"
    for p in (ROOT / "dist" / "gcap.exe", base / "publish" / "gcap.exe", base / "gcap.exe"):
        if p.exists():
            return p
    return None


EXE = find_exe()


def run_exe(*args: str) -> str:
    r = subprocess.run([str(EXE), *args], capture_output=True, timeout=300)
    if r.returncode != 0:
        raise AssertionError(f"gcap.exe {args} failed: {r.stderr.decode(errors='replace')}")
    return r.stdout.decode("utf-8")


def run_py(*args: str) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        py_main(list(args))
    return buf.getvalue()


def normalize(v):
    """数値型の差 (320 と 320.0) を吸収して比較する。"""
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [normalize(x) for x in v]
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


@unittest.skipUnless(EXE and CAPTURES, "gcap.exe or captures not found")
class TestCSharpParity(unittest.TestCase):
    def test_calls(self):
        for cap in CAPTURES:
            with self.subTest(capture=f"{cap.parent.name}/{cap.name}"):
                cs = [json.loads(line) for line in run_exe("wpix", "calls", str(cap), "--json").splitlines() if line]
                py = [json.loads(line) for line in run_py("wpix", "calls", str(cap), "--json").splitlines() if line]
                self.assertEqual(len(cs), len(py))
                for a, b in zip(cs, py):
                    self.assertEqual(normalize(a), normalize(b), f"seq={b.get('seq')} api={b.get('api')}")

    def test_draws(self):
        for cap in CAPTURES:
            with self.subTest(capture=f"{cap.parent.name}/{cap.name}"):
                cs = json.loads(run_exe("wpix", "draws", str(cap), "--json"))
                py = json.loads(run_py("wpix", "draws", str(cap), "--json"))
                self.assertEqual(normalize(cs), normalize(py))

    def test_meta(self):
        """SDK ヘッダから生成する型メタデータが Python 版と一致する。"""
        with tempfile.TemporaryDirectory() as td:
            cs_meta = Path(td, "cs_meta.json")
            run_exe("meta", "rebuild", "--meta", str(cs_meta))
            from gcap.d3dmeta.header_parser import load_db
            py = load_db().to_json()
            cs = json.loads(cs_meta.read_text("utf-8"))
            for k in ("version", "interfaces", "structs", "enums", "typedefs"):
                self.assertEqual(cs[k], py[k], k)

    def test_extract(self):
        for cap in CAPTURES:
            if cap.parent.name in ("pix", "pix_ext"):
                with self.subTest(capture=f"{cap.parent.name}/{cap.name}"):
                    self._extract(cap)

    def _extract(self, cap):
        with tempfile.TemporaryDirectory() as td:
            cs_dir, py_dir = Path(td, "cs"), Path(td, "py")
            run_exe("wpix", "extract", str(cap), "-o", str(cs_dir))
            run_py("wpix", "extract", str(cap), "-o", str(py_dir))
            cs_files = sorted(p.relative_to(cs_dir).as_posix() for p in cs_dir.rglob("*") if p.is_file())
            py_files = sorted(p.relative_to(py_dir).as_posix() for p in py_dir.rglob("*") if p.is_file())
            self.assertEqual(cs_files, py_files)
            for rel in cs_files:
                # 自前 PNG エンコードは zlib の圧縮結果が実装で異なるため除外 (blob の PNG はそのままコピーなので比較)
                if rel.startswith("resources/") and rel.endswith(".png"):
                    continue
                if rel == "extract.json":
                    self.assertEqual(normalize(json.loads((cs_dir / rel).read_text("utf-8"))),
                                     normalize(json.loads((py_dir / rel).read_text("utf-8"))))
                    continue
                self.assertEqual((cs_dir / rel).read_bytes(), (py_dir / rel).read_bytes(), rel)


if __name__ == "__main__":
    unittest.main()
