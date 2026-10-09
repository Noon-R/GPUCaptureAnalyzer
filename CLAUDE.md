# CLAUDE.md — GPUCaptureAnalyzer

GPU キャプチャ (PIX .wpix / 将来 Nsight Graphics) のバイナリを独自デコードするプロジェクト。詳細は README.md。

## 環境
- Windows 11, AMD Radeon RX 6800 XT（**NVIDIA GPU なし → Nsight Graphics のキャプチャはこの PC では取得不可**）
- PIX 2603.25 (`C:\Program Files\Microsoft PIX\2603.25`, pixtool.exe あり)
- VS 2026 (18) + Windows SDK 10.0.26100。CMake は VS 同梱 (`Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe`, 4.x) を使う（PATH の 3.21 は VS 18 ジェネレータ非対応）
- Python 3.13, **外部パッケージなし**（pytest も無いので unittest）。`tools/` を PYTHONPATH/カレントにして `python -m gcap ...`

## 作業の基本ループ（docs/methodology.md）
1. `samples/DX12CaptureTarget/main.cpp` に API/既知値を追加（KnownValues + WriteManifest）
2. `python scripts/capture_pix.py --variants 0 1 2 3`（キャプチャ + pixtool の event list / export-to-cpp = オラクル）
3. `cd tools && python -m gcap wpix calls ../captures/pix/v0.wpix --failed` で失敗レコードを特定
4. `python -m gcap.wpix.inspect v0.wpix [v1.wpix --diff] --seq N` で生バイト/差分を見る
5. 規則を `tools/gcap/wpix/{decoder.py,pix_events.py,knowledge/type_bases.json}` に反映
6. `python -m unittest discover -s tests -v`（manifest 照合 + export-to-cpp 照合）
7. `docs/formats/wpix.md` を確度マーク（✅/🔶/❓）付きで更新

## 重要な規約・知見
- デコーダは「ペイロードを過不足なく消費できたか」で正しさを判定する。`ok=False` を握りつぶさない
- typeId = base[宣言インターフェース] + 自前メソッド順。base は `knowledge/type_bases.json` に追記する
- 型情報は SDK ヘッダから生成（`python -m gcap meta rebuild` → `tools/gcap/d3dmeta/d3d12_meta.json`）。手で編集しない
- シリアライズの例外は decoder.py の `UNION_SELECTORS` / `UNION_ALL_MEMBERS` / `ARRAY_PARAM_OVERRIDES` / `CPU_HANDLE_KIND_BY_PARAM` に集約
- `captures/` は生成物（git 管理外）。キャプチャにはマシン名等が含まれる
- Bash ツールのヒアドキュメントで `'` を多く含む Python を流すと失敗することがある → スクリプトは scratchpad に Write してから実行
