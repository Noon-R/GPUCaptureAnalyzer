"""DX12CaptureTarget を variant 毎に起動して PIX キャプチャを取得し、pixtool で正解データ(oracle)を出力する。

Usage:
    python scripts/capture_pix.py --variants 0 1 2 3 [--no-oracle] [--out captures/pix]

出力 (variant N 毎):
    vN.wpix          PIX GPU capture
    vN.json          アプリが埋め込んだ既知値 (manifest)
    vN_events.csv    pixtool save-event-list
    vN_cpp/          pixtool export-to-cpp (C++ 再現コード = API 呼び出しの正解)
"""
from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "bin" / "DX12CaptureTarget.exe"


def find_pixtool() -> Path:
    cands = sorted(glob.glob(r"C:\Program Files\Microsoft PIX\*\pixtool.exe"))
    if not cands:
        sys.exit("pixtool.exe not found")
    return Path(cands[-1])


def wait_stable(path: Path, timeout: float = 30.0) -> bool:
    """キャプチャファイルが書き終わる (サイズが安定する) まで待つ。"""
    end = time.time() + timeout
    last = -1
    while time.time() < end:
        if path.exists():
            size = path.stat().st_size
            if size == last and size > 0:
                return True
            last = size
        time.sleep(0.5)
    return path.exists()


def capture(variant: int, out: Path, extra_args: list[str]) -> Path:
    wpix = out / f"v{variant}.wpix"
    manifest = out / f"v{variant}.json"
    if wpix.exists():
        wpix.unlink()
    cmd = [str(APP), "--variant", str(variant), "--pix-capture", str(wpix), "--manifest", str(manifest), *extra_args]
    print("[run]", " ".join(cmd))
    subprocess.run(cmd, check=True, timeout=120)
    if not wait_stable(wpix):
        raise RuntimeError(f"capture not produced: {wpix}")
    return wpix


def oracle(pixtool: Path, wpix: Path) -> None:
    stem = wpix.with_suffix("")
    cmd = [str(pixtool), "open-capture", str(wpix),
           "save-event-list", f"{stem}_events.csv",
           "export-to-cpp", f"{stem}_cpp", "--force"]
    print("[pixtool]", " ".join(cmd))
    subprocess.run(cmd, check=True, timeout=600)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--out", type=Path, default=ROOT / "captures" / "pix")
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--app-args", nargs=argparse.REMAINDER, default=[])
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    pixtool = find_pixtool()
    for v in a.variants:
        wpix = capture(v, a.out.resolve(), a.app_args)
        if not a.no_oracle:
            oracle(pixtool, wpix)
    print("done")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
