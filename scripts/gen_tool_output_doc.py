"""docs/tool-output.md (gcap.exe の各コマンドの出力例) を実際のキャプチャから生成する。

    python scripts/gen_tool_output_doc.py [--capture captures/pix_ext/v0.wpix]

マシン名・ユーザー名・リポジトリの絶対パスは伏せ字にする (公開リポジトリに置くため)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "gcap.exe"
NL = "\n"

CALLS_KEEP = [r"CreateResource\(|CreatePlacedResource|Name=\"GCA_BLAS", r"\.Barrier\(", r"BeginRenderPass",
              r"DispatchRays", r"ExecuteIndirect", r"IndirectArguments", r"InstanceDescs", r"ExecuteBundle",
              r"DispatchMesh", r"_text=", r"QueryResults"]
CMD_KEEP = [r"^===", r"> ", r"\* ", r"\|", r"Dispatch", r"Draw", r"RenderPass", r"Barrier\(", r"ExecuteIndirect",
            r"Close"]
OBJ_KEEP = [r"GCA_(ExtRT|BLAS|TLAS|PlacedBuffer|ReservedBuffer|EBTexture|StreamPSO|MeshPSO|RTPSO|ExtHeap|Bundle|"
            r"DrawSignature|TimestampHeap|CopyQueue|ComputeQueue)"]


def redactor():
    root = str(ROOT)
    home = os.path.expanduser("~")
    subs = [(root, "<repo>"), (root.replace("\\", "/"), "<repo>"), (root.replace("\\", "\\\\"), "<repo>"),
            (home, "<home>"), (home.replace("\\", "/"), "<home>"), (home.replace("\\", "\\\\"), "<home>")]
    machine = os.environ.get("COMPUTERNAME")

    def fn(text: str) -> str:
        for a, b in subs:
            text = text.replace(a, b)
        if machine:
            text = text.replace(machine, "<machine>")
        return text
    return fn


def run(*args: str) -> str:
    r = subprocess.run([str(EXE), *args], capture_output=True, timeout=600)
    return (r.stdout + r.stderr).decode("utf-8", "replace").replace("\r\n", "\n")


def excerpt(text: str, keep: list[str], head: int = 0, width: int = 220, per_pattern: int = 0) -> str:
    """keep のいずれかにマッチする行を抜き出す。per_pattern > 0 ならパターンごとの最大行数。"""
    lines = text.splitlines()
    out = lines[:head]
    counts = [0] * len(keep)
    for ln in lines[head:]:
        for i, k in enumerate(keep):
            if re.search(k, ln) and (per_pattern == 0 or counts[i] < per_pattern):
                counts[i] += 1
                out.append(ln)
                break
    return NL.join(ln if len(ln) <= width else ln[:width] + " …" for ln in out)


def block(text: str, lang: str = "") -> str:
    return f"```{lang}{NL}{text.strip()}{NL}```"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capture", type=Path, default=ROOT / "captures" / "pix_ext" / "v0.wpix")
    a = ap.parse_args()
    cap = str(a.capture)
    red = redactor()
    rel = a.capture.resolve().relative_to(ROOT).as_posix()

    info = run("wpix", "info", cap)
    calls = run("wpix", "calls", cap)
    calls_json = run("wpix", "calls", cap, "--json")
    cmdlists = run("wpix", "cmdlists", cap)
    objects = run("wpix", "objects", cap)
    draws = run("wpix", "draws", cap)
    draws_json = json.loads(run("wpix", "draws", cap, "--json"))
    with tempfile.TemporaryDirectory() as td:
        exdir, shdir = Path(td, "extract"), Path(td, "shaders")
        ex = run("wpix", "extract", cap, "-o", str(exdir)).replace(str(exdir), "out")
        ex_files = sorted(p.relative_to(exdir).as_posix() for p in exdir.rglob("*") if p.is_file())
        sh = run("wpix", "shaders", cap, "-o", str(shdir), "--symbols", str(ROOT / "bin" / "shaders" / "pdb"))
        sh = sh.replace(str(shdir), "out")
        sh_json = json.loads((shdir / "shaders.json").read_text("utf-8"))
        sh_files = sorted(p.relative_to(shdir).as_posix() for p in shdir.rglob("*") if p.is_file())

    one_json = next(ln for ln in calls_json.splitlines() if '::DrawIndexedInstanced"' in ln)
    rays = next(d for d in draws_json if d["call"] == "DispatchRays")
    sh_one = dict(next(s for s in sh_json["shaders"] if s.get("sources")))
    sh_one["compileArgs"] = sh_one.get("compileArgs", [])[:6] + ["…"]

    parts = [
        "# gcap.exe の出力例",
        "",
        f"`{rel}`（サンプルアプリ `--extras --dxil`: 拡張 API 一式を使った 1 フレーム）に対する実際の出力。",
        "`python scripts/gen_tool_output_doc.py` で再生成できる（マシン名・パスは伏せ字）。長い行は「…」で省略。",
        "",
        "| コマンド | 出力 | 用途 |",
        "|---|---|---|",
        "| `wpix info` | コンテナ構造（ストリーム・ブロック・レコード数・含まれる種別） | ファイルの全体像、未知レコードの所在 |",
        "| `wpix calls [--json] [--failed]` | 全レコードのデコード結果（1 行 1 レコード / JSON Lines） | 全 API 呼び出しの機械処理、デコード失敗の確認 |",
        "| `wpix cmdlists` | キュー・コマンドリスト単位のコマンド列（マーカー階層・Bundle 展開付き） | フレームの流れを読む |",
        "| `wpix objects` | オブジェクト一覧（ID・型・名前・リソース情報） | 名前と ID の対応、リソースの一覧 |",
        "| `wpix draws [--json]` | Draw/Dispatch 毎の PSO・ルート引数・実リソース・RT/DS・間接引数・シェーダテーブル | 「このドローは何を読んで何に書いたか」の自動解析 |",
        "| `wpix extract -o DIR` | リソース初期データ（RGBA8 は PNG も）・シェーダ・ルートシグネチャ・スクリーンショット | データの取り出し |",
        "| `wpix shaders -o DIR` | シェーダ + PDB 照合（HLSL ソース・コンパイル引数）/ 未発見は IL | シェーダのデバッグ情報の回収 |",
        "| `meta rebuild` | SDK ヘッダから型メタデータを再生成 | SDK 更新時 |",
        "",
        "## wpix info", "", block(red(info)), "",
        "## wpix calls（抜粋）", "", block(red(excerpt(calls, CALLS_KEEP, per_pattern=3))), "",
        "`--json`（1 レコード）:", "", block(red(one_json), "json"), "",
        "## wpix cmdlists（抜粋）", "", block(red(excerpt(cmdlists, CMD_KEEP))), "",
        "## wpix objects（抜粋）", "", block(red(excerpt(objects, OBJ_KEEP))), "",
        "## wpix draws（抜粋）", "", block(red(excerpt(draws, [r"."]))), "",
        "`--json` の 1 件（DispatchRays）:", "", block(red(json.dumps(rays, indent=1, ensure_ascii=False)), "json"), "",
        "## wpix extract", "", block(red(ex)), "", block(NL.join(ex_files)), "",
        "## wpix shaders", "", block(red(sh)), "", block(NL.join(sh_files)), "",
        "`shaders.json` の 1 件:", "", block(red(json.dumps(sh_one, indent=1, ensure_ascii=False)), "json"), "",
    ]
    (ROOT / "docs" / "tool-output.md").write_text(NL.join(parts), encoding="utf-8")
    print("wrote docs/tool-output.md")


if __name__ == "__main__":
    main()
