# シェーダシンボル（PDB）の探索と照合

`gcap wpix shaders <capture.wpix> -o <outdir>` はキャプチャ中の全シェーダを書き出し、対応する PDB を探して照合する。
PDB が見つかったシェーダは PDB・HLSL ソース・コンパイル引数を、見つからなかったシェーダは IL（逆アセンブル）を出力する。

実装: `src/gcap/Shaders/`（DxContainer / Dxc / SymbolResolver / ShaderExporter）、テスト: `tests/test_shader_symbols.py`

## 1. シェーダ側の識別情報 ✅

シェーダバイトコードは DXBC コンテナ（`"DXBC"` ヘッダ + パート列）。照合に使うパート:

| パート | 内容 |
|---|---|
| `HASH` | `u32 flags` + `u8[16] digest`。dxc のシェーダハッシュ |
| `ILDN` | `u16 flags` + `u16 nameLength` + 名前。PDB のデバッグ名（`-Fd <dir>\` で自動命名した場合 `<digest の hex>.pdb`） |
| `ILDB` / `SDBG` / `SPDB` | 埋め込みデバッグ情報（あれば PDB 不要。`foundBy: embedded`） |
| `DXIL` / `SHEX` / `SHDR` | 本体（DXIL = SM6 / SHEX, SHDR = SM5 以前の DXBC） |

`dxc -Zi -Qstrip_debug -Fd <dir>\` でビルドした DXIL では、本体に `ILDN` と `HASH` が残り、デバッグ情報は別ファイルの PDB に分離される。

## 2. PDB 側 ✅

- dxc の PDB は **MSF 7.00 形式**（`Microsoft C/C++ MSF 7.00`）。中に DXIL コンテナ（ILDB, SRCI, HASH 等）が格納されている
- `IDxcPdbUtils`（dxcompiler.dll）で読める。`GetHash` は **HASH パートと同じ 20 バイト（flags + digest）** を返すので、末尾 16 バイトで比較する
- `GetSource*` / `GetArg*` / `GetEntryPoint` / `GetTargetProfile` でソースとコンパイル条件が取れる

## 3. 照合方法（確度の高い順）

| matchedBy | 方法 | 備考 |
|---|---|---|
| `hash(pdbutils)` | PDB を `IDxcPdbUtils` で読み、ハッシュがシェーダの `HASH` と一致 | 最も確実。名前が違っても特定できる（リネームされた PDB） |
| `hash(bytes)` | PDB ファイル中に digest の 16 バイト列が含まれる | dxcompiler.dll が見つからない場合のフォールバック |
| `name-only` | ファイル名が `ILDN` のデバッグ名と一致 | ハッシュを確かめられない場合のみ。**ハッシュが取れて不一致なら採用しない**（古い PDB を誤用しない） |
| `embedded` | シェーダにデバッグ情報が埋め込まれている | 探索しない |

## 4. 探索順と打ち切り

```
1. キャプチャ (.wpix) と同じフォルダ以下 (再帰)            … --no-capture-dir で無効
2. --symbols <dir> で指定したフォルダ以下 (再帰, 指定順)
3. --symbol-server <spec> (指定順) + 環境変数 GCAP_SYMBOL_PATH (';' 区切り)
     srv*<cache>*<https://url>   HTTP。取得した PDB を <cache> にストア形式で保存し、次回は cache から
     srv*<url> / <https://url>   HTTP (cache なし: 一時フォルダに保存)
     <ローカルパス>              ストア形式で直接参照 → 無ければ再帰探索
```

- **全シェーダの PDB が見つかった時点で探索を打ち切る**（以降のフォルダ・サーバは見ない）
- 再帰探索で調べるのは拡張子 `.pdb` か、デバッグ名と同名のファイルのみ（256MB 超は無視）
- ストア形式: `<root>/<pdb名>/<index>/<pdb名>`、無ければ `<root>/<pdb名>`
  - `index` は **symstore / symsrv 標準の PDB 索引 = GUID の書式化（Data1/2/3 はリトルエンディアン）+ Age（hex）** ✅
  - dxc の PDB は info stream（MSF stream 1）の **GUID の生バイト = シェーダの HASH digest、Age = 1** ✅
    （例: HASH `3fff508c ce44 5d7f 99d1…` → index `8C50FF3F44CE7F5D99D19F2DE52D373C1`。先頭 8 バイトの並びが入れ替わる）
  - 互換のため HASH の hex そのまま（大文字・小文字）の `<root>/<pdb名>/<HASH>/<pdb名>` も試す

検証（2026-10）:
- PIX 同梱の `symsrv.dll` の `SymbolServerGetIndexStringW(GUID=HASH, Age=1)` が上記 index と一致 ✅
- PIX は `SymbolHandling.dll` 経由で dbghelp の `SymFindFileInPathW` / `SymSrvGetFileIndexInfoW` を使う（=標準のシンボルサーバ方式）✅
- `tests/test_shader_symbols.py::test_symstore_layout`: PDB の info stream から独立に計算した index でストア / HTTP を作り解決できる ✅
- ❓ PIX の GUI が実際にこの index で問い合わせるか: キャプチャを開くだけでは問い合わせが発生しなかった（シェーダ表示時に解決すると推定）。
  PIX の Symbol Path にストアを設定してシェーダを開き、ソースが表示されるかで確認できる
- ❓ dbghelp の `SymSrvGetFileIndexInfoW` を dxc の PDB に対して呼ぶと失敗した（原因未調査）。`symstore.exe`（この PC に無い）で
  そのまま登録できるかは未確認。登録できない場合は上記 index でフォルダを作って配置すればよい

## 5. 出力

```
<outdir>/
  shaders.json                 探索条件・結果の一覧 (hash, debugName, 使用 PSO/ステージ, foundBy, matchedBy, ...)
  000_VS_<PSO名>.dxil          バイトコード (DXBC なら .dxbc)
  000_VS_<PSO名>.pdb           解決済み: PDB のコピー
  000_VS_<PSO名>.src/*.hlsl    解決済み: PDB に含まれる HLSL ソース (dxcompiler.dll がある場合)
  001_PS_<PSO名>.il.txt        未解決: IL (DXIL は dxcompiler の逆アセンブル、DXBC は D3DDisassemble)
```

- `--il-all` で解決済みのシェーダも IL を出力する
- 同じバイトコードを複数の PSO が使っている場合は 1 つにまとめ、`uses` に全ての PSO/ステージを列挙する

## 6. 必要なもの・制限

- dxcompiler.dll: `GCAP_DXCOMPILER` → gcap.exe と同じフォルダ → Windows SDK (`Windows Kits\10\bin\<ver>\x64`) → PIX のインストール先 の順に探す。
  見つからない場合、ハッシュ照合は `hash(bytes)` に落ち、DXIL の IL 化・ソース取り出しはできない
- DXBC（fxc / D3DCompile）のシェーダは通常 `ILDN` / `HASH` を持たないため照合対象にならず、IL（D3DDisassemble）で出力される
- `shaders.json` の `compileArgs` / ソースのパスにはビルド環境の**絶対パス**が含まれる。共有時は注意
