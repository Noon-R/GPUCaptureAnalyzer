# 解析手法と拡張手順（PIX 作業ループ）

> プラットフォーム共通の詳しい手順は [reverse-engineering-guide.md](reverse-engineering-guide.md)。

本プロジェクトでは、GPU キャプチャのバイナリ形式を次の 3 つの手掛かりを組み合わせて解析する。

| 手法 | 内容 | 本プロジェクトでの実装 |
|---|---|---|
| **既知平文 (known plaintext)** | 解析対象アプリに特徴的な値（`0xDEADBEEF`、`GCA_` 接頭辞の名前、特定の float）を埋め込み、バイナリ中の位置を探す | `samples/DX12CaptureTarget`（値は `--manifest` で JSON 出力）、`gcap probe --manifest` |
| **差分解析 (differential)** | 1 パラメータだけ変えた複数キャプチャを比較し、変化したバイトをそのフィールドと特定する | `--variant N`（全既知値が N に応じて決定的に変化）、`python -m gcap.wpix.inspect a.wpix b.wpix --seq S --diff` |
| **オラクル (oracle)** | ツール自身の解釈を正解として使う | `pixtool save-event-list` / `pixtool export-to-cpp`（API 呼び出しを C++ で再現。オブジェクト ID も一致） |

加えて、**SDK ヘッダからの型情報の自動抽出**（`gcap/d3dmeta/header_parser.py`）により、
D3D12 API のシリアライズは個別に解析しなくても汎用デコーダで大半が読める。
デコーダは「ペイロードを過不足なく消費できたか」を常に検査するため、規則の誤りは即座に検出される。

## 解析の進め方（実際に辿った順序）

1. **コンテナ層**: hexdump でタグ（`GFXA`, `<CAP`, `BLOKCOMP`, `CBLKDATA`, `DIR3`）を発見 → サイズフィールドの意味を位置計算で確定
2. **圧縮**: 断片化した文字列から LZ 系と推定 → `WinPixGpuCapturer.dll` の import に `cabinet.dll` → Compression API の全アルゴリズムを総当たり → XPRESS RAW で一致
3. **レコード層**: 展開データ先頭の `<EVT` から固定長ヘッダを特定。`seq` が全ストリーム通し番号であることを発見
4. **API 対応付け**: event list / export-to-cpp の順序と突き合わせ → typeId が vtable 順に並ぶことを発見 → 「宣言インターフェース毎の base + 自前メソッド順」と確定
5. **シリアライズ規則**: d3d12.h の宣言と並べてバイト列を読む（present フラグ、u64 count、ID 化されたポインタ、パディングなし）
6. **汎用デコーダ**: ヘッダから引数型を取り、規則で読み、消費長で検証 → 失敗したものだけを個別解析
7. **PIX 独自イベント**: 既知値（クリア色、テクセルパターン、`DEADBEEF`）が現れる位置から構造を逆算
8. **カバレッジ拡大**: サンプルアプリに API を追加 → 再キャプチャ → `--failed` で失敗レコードだけ調査

## 新しい API をカバーする手順

```bash
# 1. samples/DX12CaptureTarget/main.cpp に調べたい API 呼び出しを追加
#    - 値は KnownValues に追加し variant で変化させる。WriteManifest() にも出力する
# 2. ビルド & キャプチャ（全 variant + oracle）
cmake --build samples/DX12CaptureTarget/build --config Release
python scripts/capture_pix.py --variants 0 1 2 3
# 3. デコード失敗レコードを確認
cd tools
python -m gcap wpix calls ../captures/pix/v0.wpix --failed
# 4. 生バイトと差分を見る
python -m gcap.wpix.inspect ../captures/pix/v0.wpix --seq 67
python -m gcap.wpix.inspect ../captures/pix/v0.wpix ../captures/pix/v1.wpix --seq 67 --diff
# 5. 規則を Python (tools/gcap/wpix/decoder.py, pix_events.py) と C# (src/gcap/Wpix/ApiDecoder.cs, PixEvents.cs) の両方に反映
#    typeId base は tools/gcap/wpix/knowledge/type_bases.json (両者で共有)
# 6. tests/test_wpix.py に manifest 照合を追加し、C# 版を再ビルドして全テスト (C#/Python 一致を含む)
cd .. && dotnet publish src/gcap -c Release -o dist && python -m unittest discover -s tests -v
# 7. docs/formats/wpix.md を更新（確度マーク付き）
```

### 未知の typeId が出たとき

- `Unknown_<id>` と表示される。`this`（先頭 u32）のオブジェクトの型を `wpix objects` で調べる
- そのインターフェースの継承チェーン上の各インターフェースについて「`id - 自前メソッド順` = base」を仮定し、
  汎用デコードがペイロードを**ちょうど**消費する候補を探す → `knowledge/type_bases.json` に base を追記
- どの候補も合わなければ PIX 独自イベント。`pix_events.py` に個別デコーダを書く

### 既知のハマりどころ

- `typedef interface X X;` を型解決で辿ると COM ポインタが判定できなくなる（header_parser で除外済み）
- `D3D12_GPU_VIRTUAL_ADDRESS` は typedef では UINT64 だが、PIX は 12 bytes（offset + resource）で書く
- SAL が単一ポインタでも配列として書かれる引数がある（`ARRAY_PARAM_OVERRIDES`）
- union は基本「選択メンバのみ」だが `D3D12_CLEAR_VALUE` は全メンバ
