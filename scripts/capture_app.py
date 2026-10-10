"""任意の D3D12 アプリを pixtool で起動して GPU キャプチャを取る (自作サンプル以外の実アプリでの検証用)。

    python scripts/capture_app.py <app.exe> [--args "-dx12"] [--out captures/real/name.wpix] [--frames 1] [--no-oracle]

pixtool launch <exe> take-capture --open save-capture <out> を実行し、続けて save-event-list で正解データ
(<out>_events.csv) を出力する。キャプチャは起動直後 (pixtool がアプリの D3D12 初期化を検出した時点) に取られる。
キャプチャにはマシン名・パス等が含まれるので captures/ (git 管理外) に置くこと。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_pix import ROOT, find_pixtool  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("exe", type=Path)
    ap.add_argument("--args", default="", help="アプリに渡すコマンドライン (例: \"-dx12\")")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--no-oracle", action="store_true")
    a = ap.parse_args()
    out = (a.out or ROOT / "captures" / "real" / f"{a.exe.stem}.wpix").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    pixtool = find_pixtool()
    cmd = [str(pixtool), "launch", str(a.exe.resolve())]
    if a.args:
        cmd.append(f"--command-line={a.args}")   # 値に空白を含む場合も 1 引数で渡す
    cmd += ["take-capture", f"--frames={a.frames}", "--open", "save-capture", str(out)]
    print("[pixtool]", " ".join(cmd))
    subprocess.run(cmd, check=True, timeout=900)
    if not a.no_oracle:
        cmd = [str(pixtool), "open-capture", str(out), "save-event-list", str(out.with_name(out.stem + "_events.csv"))]
        print("[pixtool]", " ".join(cmd))
        subprocess.run(cmd, check=True, timeout=900)
    print("saved", out)


if __name__ == "__main__":
    main()
