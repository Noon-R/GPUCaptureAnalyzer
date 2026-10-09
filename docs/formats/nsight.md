# Nsight Graphics キャプチャ — 現状と計画

## 現状（2026-10-09）

- **この開発 PC では Nsight Graphics のキャプチャを取得できていない。**
  - Nsight Graphics が未インストール
  - GPU が **AMD Radeon RX 6800 XT**。Nsight Graphics の Frame Debugger / GPU Trace は NVIDIA GPU を前提としている
    （非 NVIDIA 環境でどこまで動くかは、使用予定のバージョンのリリースノート/システム要件で要確認）
- そのため PIX (.wpix) を先行して解析した。解析基盤（サンプルアプリ・既知値・差分・probe ツール）は形式非依存で、Nsight にもそのまま使える。

## Nsight 対応の進め方（NVIDIA GPU 環境が用意できたら）

1. **キャプチャ取得**
   - サンプルアプリは `--no-pix` で PIX の capturer をロードせずに起動できる（Nsight と競合させないため）
   - Nsight Graphics の Frame Debugger / Graphics Capture で `bin/DX12CaptureTarget.exe --no-pix --variant N --frames 100000 --manifest vN.json`
     を起動してキャプチャし、`captures/nsight/vN.<拡張子>` に保存
   - 自動化: Nsight Graphics 同梱の CLI（`ngfx.exe` 等。オプションはバージョン依存のため `--help` で確認）で
     `scripts/capture_pix.py` と同等の `scripts/capture_nsight.py` を作る
   - オラクル: Nsight の「C++ Capture（キャプチャを C++ プロジェクトとして出力）」機能が使えれば export-to-cpp と同じ役割になる
2. **初期調査（形式非依存ツール）**
   ```bash
   cd tools
   python -m gcap probe ../captures/nsight/v0.<ext> --manifest ../captures/nsight/v0.json
   ```
   - 先頭/末尾のマジック、頻出タグ、エントロピー分布（圧縮領域の推定）、既知値のヒット位置が出る
   - 既知値がヒットしない → 圧縮されている。`gcap.probe.try_decompress` で zlib / deflate / Windows Compression API を総当たり。
     zstd / LZ4 の可能性もあるので Nsight の DLL の import / 文字列を確認する（PIX で cabinet.dll を見つけたのと同じ手順）
3. **コンテナ → レコード → API 対応** の順に PIX と同じ手順で進める（`docs/methodology.md`）
4. `tools/gcap/nsight/` パッケージとして実装し、`gcap nsight ...` サブコマンドを追加

## 代替案

- NVIDIA GPU 搭載のマシン（別 PC / クラウド GPU インスタンス）でキャプチャだけ取得し、解析はこの PC で行う
  （サンプルアプリ・manifest・キャプチャファイルを持ち込めば解析は GPU 不要）
