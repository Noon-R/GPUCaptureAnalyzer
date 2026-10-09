# GPUCaptureAnalyzer

GPU キャプチャファイル（PIX `.wpix`、将来的に Nsight Graphics）のバイナリを独自にデコードし、
PIX などの GUI/CLI では取り出しにくい情報を自動解析できるようにするプロジェクト。

- **本体: `gcap.exe`（C# / .NET 10、単一ファイル exe・ランタイム同梱）**
- 検証用リファレンス実装: `tools/gcap`（Python。解析作業用ツールと、C# 版との出力照合に使う）

## 現在できること（PIX 2603.25 / D3D12）

- `.wpix` コンテナの完全パース（XPRESS 圧縮ブロックの展開、ストリーム/ディレクトリ構造）
- 記録された **全 API 呼び出しのデコード**（d3d12.h から自動生成した型情報 + 解析で判明したシリアライズ規則）
  - 引数・構造体・enum 名・オブジェクト名（SetName）・GPU VA（リソース+オフセット）・ディスクリプタの中身
- コマンドリスト単位のコマンド列再構成（PIX マーカーの階層付き）
- **Draw/Dispatch 毎のバインディング解決**（ルートシグネチャ blob を解析し、ディスクリプタテーブル → 実リソース、VB/IB、RTV/DSV を特定）
- キャプチャ開始時点のリソース内容・ステート・ディスクリプタヒープのスナップショット
- リソース初期データ / シェーダバイトコード / ルートシグネチャ / スクリーンショットの抽出
- 未知形式向けのバイナリ調査ツール（Python 版のみ: エントロピー、タグ、既知値検索、圧縮総当たり）

**解析手順書（他プラットフォームにも適用可）: [docs/reverse-engineering-guide.md](docs/reverse-engineering-guide.md)**

フォーマットの詳細: [docs/formats/wpix.md](docs/formats/wpix.md) / 作業ループ: [docs/methodology.md](docs/methodology.md) /
Nsight の状況: [docs/formats/nsight.md](docs/formats/nsight.md)

## 使い方（gcap.exe）

```bash
# ビルド (dist/gcap.exe が生成される)
dotnet publish src/gcap -c Release -o dist

gcap wpix info     capture.wpix              # コンテナ/ストリーム概要
gcap wpix calls    capture.wpix [--json]     # 全 API 呼び出し (--json で JSON Lines, --failed で失敗のみ)
gcap wpix cmdlists capture.wpix              # コマンドリスト毎・マーカー階層付き
gcap wpix objects  capture.wpix              # オブジェクト一覧 (名前・型・リソース desc)
gcap wpix draws    capture.wpix [--json]     # Draw/Dispatch 毎の PSO・ルート引数・実リソース
gcap wpix extract  capture.wpix -o outdir    # リソース初期データ/シェーダ/ルートシグネチャ/スクリーンショット
gcap meta rebuild                            # Windows SDK ヘッダから型メタデータを再生成
```

- 初回実行時に Windows SDK のヘッダ（d3d12.h 等）から型メタデータを生成し、`%LOCALAPPDATA%\gcap\d3d12_meta.json` にキャッシュする。
  SDK が別の場所にある場合は環境変数 `GCAP_SDK_INCLUDE`（`...\Windows Kits\10\Include\<ver>`）か `meta rebuild --sdk` で指定する。
- 任意のアプリのキャプチャ（PIX GUI で保存した .wpix）も同じコマンドで解析できる。
  未対応 API は `Unknown_<typeId>` または `!! ... bytes left over` として表示される。

## 構成

```
src/gcap/                    本体 (C#)。Program.cs (CLI), Wpix/ (コンテナ・デコーダ・PIX 独自イベント・状態解決), Meta/ (SDK ヘッダ解析)
tools/gcap/                  Python リファレンス実装 + 解析用ツール (inspect: hexdump/差分, probe: 未知形式調査)
  wpix/knowledge/            解析で判明した知識 (typeId base 表)。C# 版にも埋め込まれる単一の情報源
samples/DX12CaptureTarget/   既知の値を埋め込んだ DX12 テストアプリ (variant で値が変化, manifest 出力, PIX プログラマティックキャプチャ)
scripts/capture_pix.py       variant 毎にキャプチャ + pixtool で正解データ (event list / export-to-cpp) を生成
tests/test_wpix.py           Python 版: manifest 照合 + export-to-cpp 照合
tests/test_csharp_parity.py  C# 版 == Python 版 (calls / draws / extract の出力一致)
docs/                        フォーマット仕様・手法
captures/                    キャプチャ置き場 (git 管理外)
```

## 開発

```bash
# テストアプリのビルド (VS 2026 / Windows SDK 10.0.26100)
cmake -S samples/DX12CaptureTarget -B samples/DX12CaptureTarget/build -G "Visual Studio 18 2026" -A x64
cmake --build samples/DX12CaptureTarget/build --config Release

# キャプチャ取得 (variant 0..3) + pixtool による正解データ生成
python scripts/capture_pix.py --variants 0 1 2 3

# 解析作業用 (Python)
cd tools
python -m gcap wpix calls ../captures/pix/v0.wpix --failed
python -m gcap.wpix.inspect ../captures/pix/v0.wpix ../captures/pix/v1.wpix --seq 67 --diff
python -m gcap probe FILE --manifest known.json

# テスト (Python 版の正しさ + C# 版との一致)
cd .. && dotnet publish src/gcap -c Release -o dist && python -m unittest discover -s tests -v
```

デコード規則を変えるときは **Python 版と C# 版の両方**を直し、`test_csharp_parity` で一致を確認する
（手順は [docs/methodology.md](docs/methodology.md)）。

## 注意

- キャプチャには **マシン名・プロセス名・時刻**（PIX::CaptureEnvironment）やリソース内容がそのまま入る。共有時は注意。
- 形式は PIX のバージョンで変わり得る。確認済みは PIX 2603.25 のみ。

## ロードマップ

当面は PIX (.wpix) の対応範囲を広げる。Nsight Graphics は NVIDIA GPU 環境の用意後に着手する。

1. **シェーダシンボル**: シンボルサーバ / キャプチャと同じフォルダ / 指定フォルダを再帰探索して PDB を照合
   （必要分が見つかった時点で打ち切り）。見つからないシェーダは IL（DXIL/DXBC 逆アセンブル）で書き出す
2. ExecuteIndirect / CommandSignature、Enhanced Barriers (`Barrier`)、RenderPass
3. PipelineStateStream (`CreatePipelineState`)、Placed / Reserved リソース、CopyDescriptors
4. 複数キュー（Copy / Compute）・複数コマンドリスト・Bundle・Query
5. DXR（`DispatchRays`、AS ビルド）、Mesh shader、PIX3 形式のマーカー
6. フレーム内でのディスクリプタ書き換えを `draws` に反映、複数フレームキャプチャ
7. 他プラットフォーム（[解析手順書](docs/reverse-engineering-guide.md) §9 のチェックリストに沿って追加）

## 必要環境

- 実行: Windows 10/11 (x64)、Windows SDK（型メタデータ生成用のヘッダ）。`gcap.exe` は .NET ランタイム不要
- ビルド: .NET 10 SDK
- 解析作業・テスト: Python 3.11+（外部パッケージ不要）、PIX on Windows、Visual Studio
