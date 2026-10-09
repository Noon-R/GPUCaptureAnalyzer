# GPUCaptureAnalyzer

GPU キャプチャファイル（PIX `.wpix`、将来的に Nsight Graphics）のバイナリを独自にデコードし、
PIX などの GUI/CLI では取り出しにくい情報を自動解析できるようにするプロジェクト。

## 現在できること（PIX 2603.25 / D3D12）

- `.wpix` コンテナの完全パース（XPRESS 圧縮ブロックの展開、ストリーム/ディレクトリ構造）
- 記録された **全 API 呼び出しのデコード**（d3d12.h から自動生成した型情報 + 解析で判明したシリアライズ規則）
  - 引数・構造体・enum 名・オブジェクト名（SetName）・GPU VA（リソース+オフセット）・ディスクリプタの中身
- コマンドリスト単位のコマンド列再構成（PIX マーカーの階層付き）
- **Draw/Dispatch 毎のバインディング解決**（ルートシグネチャ blob を解析し、ディスクリプタテーブル → 実リソース、VB/IB、RTV/DSV を特定）
- キャプチャ開始時点のリソース内容・ステート・ディスクリプタヒープのスナップショット
- リソース初期データ / シェーダバイトコード / ルートシグネチャ / スクリーンショットの抽出
- 未知形式向けのバイナリ調査ツール（エントロピー、タグ、既知値検索、圧縮総当たり）

**解析手順書（他プラットフォームにも適用可）: [docs/reverse-engineering-guide.md](docs/reverse-engineering-guide.md)**

フォーマットの詳細: [docs/formats/wpix.md](docs/formats/wpix.md) / 作業ループ: [docs/methodology.md](docs/methodology.md) /
Nsight の状況: [docs/formats/nsight.md](docs/formats/nsight.md)

## 構成

```
samples/DX12CaptureTarget/   既知の値を埋め込んだ DX12 テストアプリ (variant で値が変化, manifest 出力, PIX プログラマティックキャプチャ)
scripts/capture_pix.py       variant 毎にキャプチャ + pixtool で正解データ (event list / export-to-cpp) を生成
tools/gcap/                  解析ツール (Python 3.11+, 外部依存なし, Windows)
  wincompress.py             Windows Compression API (cabinet.dll) ラッパ
  d3dmeta/header_parser.py   Windows SDK ヘッダ → インターフェース/構造体/enum メタデータ (d3d12_meta.json)
  wpix/container.py          ファイル/セクション/ディレクトリ/ブロック層
  wpix/events.py             <EVT レコード層
  wpix/decoder.py            汎用 API デコーダ (シリアライズ規則)
  wpix/pix_events.py         PIX 独自イベント (リソース生成/初期データ/ディスクリプタヒープ/...)
  wpix/knowledge/            解析で判明した知識 (typeId base 表)
  wpix/capture.py            高レベル API
  wpix/inspect.py            解析用 hexdump / 差分
  wpix/state.py              コマンドリスト再生 → Draw/Dispatch 毎のバインディング解決
  rootsig.py                 シリアライズ済みルートシグネチャ (RTS0) パーサ
  probe.py                   形式非依存のバイナリ調査
tests/test_wpix.py           manifest 照合 + export-to-cpp 照合の回帰テスト
docs/                        フォーマット仕様・手法
captures/                    キャプチャ置き場 (git 管理外)
```

## 使い方

```bash
# サンプルアプリのビルド (VS 2026 / Windows SDK 10.0.26100)
cmake -S samples/DX12CaptureTarget -B samples/DX12CaptureTarget/build -G "Visual Studio 18 2026" -A x64
cmake --build samples/DX12CaptureTarget/build --config Release

# キャプチャ取得 (variant 0..3) + pixtool による正解データ生成
python scripts/capture_pix.py --variants 0 1 2 3

# 解析
cd tools
python -m gcap wpix info     ../captures/pix/v0.wpix     # コンテナ/ストリーム概要
python -m gcap wpix calls    ../captures/pix/v0.wpix     # 全 API 呼び出し (--json で JSON Lines)
python -m gcap wpix cmdlists ../captures/pix/v0.wpix     # コマンドリスト毎・マーカー階層付き
python -m gcap wpix objects  ../captures/pix/v0.wpix     # オブジェクト一覧 (名前・型・リソース desc)
python -m gcap wpix draws    ../captures/pix/v0.wpix     # Draw/Dispatch 毎の PSO・ルート引数・実リソース (--json)
python -m gcap wpix extract  ../captures/pix/v0.wpix -o ../captures/pix/v0_extract
python -m gcap probe FILE --manifest known.json          # 未知形式の初期調査

# テスト
cd .. && python -m unittest discover -s tests -v
```

任意のアプリのキャプチャ（PIX GUI で保存した .wpix）も同じコマンドで解析できる。
未対応 API は `Unknown_<typeId>` または `!! ... bytes left over` として表示されるので、
[docs/methodology.md](docs/methodology.md) の手順でカバー範囲を広げる。

## 注意

- キャプチャには **マシン名・プロセス名・時刻**（PIX::CaptureEnvironment）やリソース内容がそのまま入る。共有時は注意。
- 形式は PIX のバージョンで変わり得る。確認済みは PIX 2603.25 のみ。

## ロードマップ

当面は PIX (.wpix) の対応範囲を広げる。Nsight Graphics は NVIDIA GPU 環境の用意後に着手する。

1. ExecuteIndirect / CommandSignature、Enhanced Barriers (`Barrier`)、RenderPass
2. PipelineStateStream (`CreatePipelineState`)、Placed / Reserved リソース、CopyDescriptors
3. 複数キュー（Copy / Compute）・複数コマンドリスト・Bundle・Query
4. DXR（`DispatchRays`、AS ビルド）、Mesh shader、PIX3 形式のマーカー
5. フレーム内でのディスクリプタ書き換えを `draws` に反映、複数フレームキャプチャ
6. 他プラットフォーム（[解析手順書](docs/reverse-engineering-guide.md) §9 のチェックリストに沿って追加）

## 必要環境

- Windows 10/11、Python 3.11+（外部パッケージ不要）
- キャプチャ取得とテストアプリのビルドには PIX on Windows、Visual Studio、Windows SDK が必要
- `tools/gcap/d3dmeta/d3d12_meta.json` は初回実行時に Windows SDK のヘッダから自動生成される（`python -m gcap meta rebuild` でも可）
