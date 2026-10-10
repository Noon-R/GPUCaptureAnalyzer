# PIX GPU Capture (.wpix) バイナリフォーマット解析ノート

- 対象: **PIX 2603.25** で `WinPixGpuCapturer.dll` の `CaptureNextFrame` により取得した D3D12 GPU キャプチャ
- 確度: ✅ 確定（複数キャプチャ・既知値・PIX の export-to-cpp で検証済み） / 🔶 推定（整合するが反例テスト未実施） / ❓ 未解明
- 実装: `src/gcap/Wpix/`（C#, 本体）/ `tools/gcap/wpix/`（Python, リファレンス）。両者の出力一致は `tests/test_csharp_parity.py` で検証
- 検証: `tests/test_wpix.py`（manifest + export-to-cpp 照合）、`tests/test_wpix_ext.py`（拡張 API・複数フレーム）。
  加えて独立検証で export-to-cpp の全コマンド・全リソース生成パラメータ・ヒープ全スロット・PSO 全フィールド・`resources.bin` の初期データ（バイト単位）と一致を確認（基本シナリオ v0〜v3）
- 検証済みキャプチャ: 基本 (`captures/pix`)、DXIL シェーダ (`pix_dxil`)、拡張 API (`pix_ext`: Placed/Reserved, Query, ExecuteIndirect,
  RenderPass, Bundle, PipelineStateStream, Mesh/Amplification, Enhanced Barriers, DXR, Copy/Compute キュー, PIX3 マーカー)、複数フレーム (`pix_multi`)

---

## 1. 全体構造 ✅

```
+0x0000  ファイルヘッダ (0x400 bytes)
           "GFXA" u32 0xFFFFFFFF "GFXB" u32 1 u32 1 ... (+0x40: u32 2, u32 3)   ❓ 各値の意味は未解明 (version?)
+0x0400  セクション "<CAP" ... "CAP>"        ← DIR1 エントリが指す
           +0x00  "<CAP" u32 version(=2)  (0x40 bytes ヘッダ)
           +0x40  ストリームのブロック列 (BLOKCOMP / BLOKDATA)
           ...    DIR3 テーブル + フッタ (DIR2)
           end-0x40  セクショントレーラ (0x40 bytes, 先頭 u32 ❓, 末尾 "CAP>")
...      DIR1 テーブル + フッタ (DIR0)
end-0x400 ファイルトレーラ (0x400 bytes, 末尾 "GFXB" "GFXA")
```

### 1.1 ディレクトリ (DIRn) ✅

テーブルは**後ろから読む**。フッタ位置は「トレーラ直前」で固定。

| 構造 | サイズ | 内容 |
|---|---|---|
| エントリ | 32 | `char[4] tag ("DIR1"/"DIR3")`, `u32 id`, `u64 offset (ファイル先頭から)`, `u64 size`, `u64 reserved` |
| フッタ | 32 | `u8[16] reserved`, `u64 tableBytes (= count*32)`, `u32 count`, `char[4] tag ("DIR0"/"DIR2")` |

- 外側: `DIR1` エントリ（id=0）→ `<CAP` セクション。フッタタグは `DIR0`
- 内側: `DIR3` エントリ（id=ストリーム ID）→ そのストリームのブロック列。フッタタグは `DIR2`
- **同じストリーム ID の `DIR3` エントリが複数あることがある**（複数フレーム / 大きいデータ）✅。
  出現順に連結したものがストリーム本体で、**レコードはエントリ境界をまたいで続く**（2 つ目のエントリの先頭はレコード途中から始まる）

### 1.2 ブロック ✅

| 種別 | レイアウト |
|---|---|
| 圧縮 | `"BLOKCOMP" u64 size` + `"CBLKDATA" u64 rawSize` + 圧縮データ (`size - 16` bytes) |
| 非圧縮 | `"BLOKDATA" u64 size` + 生データ (`size` bytes)。PNG 等の圧縮が効かないデータで使われる |

- **圧縮アルゴリズム = Windows Compression API の XPRESS (Plain LZ77), RAW モード**
  (`CreateDecompressor(COMPRESS_ALGORITHM_XPRESS | COMPRESS_RAW)`)。`WinPixGpuCapturer.dll` が `cabinet.dll` を ordinal import していることから特定し、総当たりで確認。
- 1 ストリームは複数ブロックになり得る（大きいデータ）。展開後を連結したものがストリーム本体。

### 1.3 ストリーム ID（観測値）🔶

レコードの種類ごとにストリームが分かれている。

| stream | 内容 |
|---|---|
| 2 | D3D12CreateDevice + アダプタ情報 |
| 3,4,5,6,7,8,9,10,11,12,13,14,15,17 | オブジェクト生成（種類別: Queue, GraphicsPSO, ComputePSO, PipelineStateStream, ?, DescriptorHeap, Allocator, CommandList, RootSig, CommandSignature, QueryHeap, Fence, Heap, StateObject） |
| 100 / 101 | リソース生成 (1700/1767/1770) / リソース初期内容 (2029) |
| 102 / 103 | タイルマッピング (2037) / 加速構造データ (2041) |
| 300 | SetName |
| 1000 | キュー操作 (ExecuteCommandLists, Signal, Wait, UpdateTileMappings, Present) + ディスクリプタ書き込み (2033) + アップロードヒープのページ (2034) |
| 1001 | コマンドリストの記録（direct/compute/copy 全コマンドリスト共通） |
| 1002 | Bundle の記録 |
| 2000 | コマンド実行時のスナップショット（間接引数, シェーダテーブル, TLAS インスタンス, Query 結果） |
| 2001 | ExecuteCommandLists に付随する（2139 = シェーダテーブルのレコード内容）🔶 |
| 10000 / 10001 | 付随 blob の参照 / 本体（スクリーンショット PNG） |
| 10002 | キャプチャ環境情報 |

実ゲーム（UE5, 1 フレーム）では stream 101（リソース初期データ）が**展開後 2.57 GB**（圧縮 54 MB, 2449 ブロック）になった。
1 ストリームを 1 つの配列に展開すると 2 GB を超えるので、実装はブロック単位で必要な範囲だけ展開する（`WpixStream.Read`）。

ストリーム ID は固定ではなくレコード種別に依存して増える可能性がある。デコーダはストリーム ID に依存せず、**全ストリームのレコードを seq でソート**して扱う。

---

## 2. イベントレコード (`<EVT`) ✅

ストリーム本体は `<EVT` レコードの連続。

```
+0x00 char[4] "<EVT"
+0x04 u32     typeId        API/イベント種別
+0x08 u64     seq           全ストリーム共通の通し番号 (= 記録順。ExecuteCommandLists などが参照する)
+0x10 u64     payloadSize   ヘッダ以降のバイト数
+0x18 u8[40]  reserved      (0 を観測)
+0x40 payload:
        "META" u32[5]  (24 bytes, 全て 0 を観測) ❓ スレッド ID / タイムスタンプ用か
        body           typeId ごとの引数シリアライズ
```

- `seq` は 1 つのレコードが複数ストリームに分かれる場合（blob 参照 36 と本体 37）同じ値になる。

### 2.1 typeId の決定規則 ✅

```
typeId = base[宣言インターフェース] + (そのインターフェースが自分で宣言したメソッドの中での順番)
```

- 「宣言インターフェース」は d3d12.h の `DECLSPEC_XFGVIRT(Iface, Method)` の Iface。
  例: `SetName` はどのオブジェクトに対しても `ID3D12Object` の宣言なので常に 973。
- 確認済みの base（`tools/gcap/wpix/knowledge/type_bases.json`）:

| インターフェース | base | 例 |
|---|---|---|
| ID3D12Object | 970 | SetName = 973 |
| ID3D12GraphicsCommandList | 992 | Close=992, Reset=993, DrawInstanced=995, Dispatch=997, ResourceBarrier=1009, BeginEvent=1040 |
| ID3D12GraphicsCommandList1 | 1620 | OMSetDepthBounds=1622（UE5 で観測） |
| ID3D12GraphicsCommandList2 | 1698 | WriteBufferImmediate=1698（UE5 で観測） |
| ID3D12CommandQueue | 1063 | ExecuteCommandLists=1065, Signal=1069, Wait=1070 |
| ID3D12Device | 1074 | CreateCommandQueue=1075, CreateRootSignature=1083, CreateFence=1103 |
| ID3D12Device2 | 1629 | CreatePipelineState=1629（PIX 独自形式。§3.6） |
| ID3D12Device4 | 1711 | CreateCommandList1=1711 |
| ID3D12GraphicsCommandList4 | 1919 | BuildRaytracingAccelerationStructure=1923, SetPipelineState1=1926, DispatchRays=1927 |
| ID3D12GraphicsCommandList5 | 1952 | RSSetShadingRate=1952, RSSetShadingRateImage=1953（UE5 で観測） |
| ID3D12GraphicsCommandList6 | 1999 | DispatchMesh=1999 |
| ID3D12GraphicsCommandList7 | 2081 | Barrier=2081 |

**規則に従わない ID（aliases）**: `BeginRenderPass=1849`, `EndRenderPass=1850`（GCL4 の base からは 1919/1920 になるはずだが PIX は別番号で記録）。
`type_bases.json` の `aliases` に登録する。

base の間隔はヘッダ上のインターフェース順と完全には一致しない（間に別インターフェースが入る）。
新しい API を使ったキャプチャで未知 typeId が出たら、`this` の型とペイロード長の完全一致で base を特定して追記する。
実アプリのキャプチャでは、pixtool のイベント一覧（`save-event-list`）の API 別件数と未知 typeId の件数を突き合わせると候補が絞れる
（例: 未知 1698 が 2403 件 ↔ WriteBufferImmediate 2388 件 → 中身が `this, u32 Count, D3D12_WRITEBUFFERIMMEDIATE_PARAMETER[], MODE[]` で
過不足なく読めることを確認）。

### 2.2 PIX 独自 typeId 🔶

| typeId | 名前（本プロジェクトでの命名） | 内容 |
|---|---|---|
| 36 | PIX::BlobReference | `GUID kind, u64 size, u64 seq` |
| 37 | PIX::BlobData | `GUID kind, u64 size, bytes` — kind `7c29896a-6790-4b40-ab07-f9876ee0e614` = スクリーンショット PNG |
| 1566 | D3D12CreateDevice | `u32 pAdapter, u32 MinFeatureLevel, GUID riid, opt u32 device, u64 n + wchar[n] adapterDesc, u64 driverVersion, ...` 後半（caps）❓ |
| 1700 | PIX::CreateResource | §4.1 |
| 1860 | PIX::PrepareForPresent | `u32 commandList`（DXGI 内部コマンドリスト） |
| 1885 | PIX::Present | `u32 queue, u32 backBuffer, u32 PresentFlags, u64 screenshotSeq`（SyncInterval は記録されない） |
| 2029 | PIX::ResourceInitialData | §4.2 |
| 2042 | PIX::CaptureEnvironment | CPU 名, アーキ, 物理メモリ, OS, OS ビルド, **マシン名**, プロセス名, 時刻(FILETIME), PID, PIX バージョン |
| 2043 | PIX::Unknown2043 | `u32`（0 を観測）❓ |
| 2044 | PIX::DescriptorHeap | §4.3 |
| 2033 | PIX::WriteDescriptors | フレーム中のディスクリプタヒープへの書き込み。§4.8 ✅ |
| 2034 | PIX::UploadPages | CPU が書き込んだアップロードヒープのページ内容。§4.9 ✅ |
| 1065 | ExecuteCommandLists | 汎用規則と異なる独自形式。§3.4 |
| 1629 | ID3D12Device2::CreatePipelineState | Stream を固定構造に展開した独自形式。§3.6 |
| 1767 | PIX::CreatePlacedResource | §4.4 |
| 1770 | PIX::CreateReservedResource | §4.4 |
| 2030 | PIX::IndirectArguments | ExecuteIndirect 実行時の引数（§4.6） |
| 2037 | PIX::TileMappings | Reserved リソースのタイルマッピング（`u32 resource` + ❓） |
| 2038 | PIX::ShaderTables | DispatchRays 実行時のシェーダテーブル（識別子 → エクスポート名）🔶 |
| 2039 | PIX::InstanceDescs | TLAS ビルド時のインスタンス記述（§4.7） |
| 2041 | PIX::AccelerationStructureData | 加速構造（BLAS/TLAS）の内容。`u64 ?, u32 resource, u32 deserializedSize, u32 ?` + `CopyRaytracingAccelerationStructure(SERIALIZE)` 形式のデータ 🔶。データ先頭: GUID driverOpaqueGuid, u8[16] versioning, u64 ❓, u64 serializedSize（= データ長 − 32）, u64 deserializedSize（✅ 前半の値と一致）, u32 ❓(BLAS/TLAS とも 0), u32 BLAS ポインタ数?（BLAS=0, TLAS=1）, 以降ドライバ依存 |
| 2045 | PIX::CreateStateObject | §3.7 |
| 2046 | PIX::QueryResults | ResolveQueryData の結果: `u64 size + bytes`（u64 列） |
| 2139 | PIX::ShaderTableRecords | DispatchRays を含む ExecuteCommandLists 後に記録。シェーダテーブルのレコード内容のスナップショット 🔶（16 B ヘッダ ❓ の後、`u64 バイト数 + bytes` が RayGen(256) / Miss(192) / HitGroup(128) の順。サイズはサンプルのテーブルと一致）。デコードは raw のまま |

> ⚠ 2042 にはマシン名・プロセス名・時刻が平文で入る。キャプチャを外部共有する際は注意。

---

## 3. 引数のシリアライズ規則 ✅

D3D12 API レコードの body は **`u32 this` → (戻り値が HRESULT なら `u32 hr`) → 引数を宣言順** に並ぶ。
**パディングなし・リトルエンディアン・自然サイズ**（構造体も C のアラインメントを無視して詰める）。

| 型 | 表現 |
|---|---|
| UINT/INT/enum/BOOL | 4 bytes（BOOL も 4） |
| UINT8 | 1 byte（例: `RenderTargetWriteMask`, `StencilReadMask`, ClearDepthStencilView の `Stencil`） |
| UINT16 | 2 bytes（`DepthOrArraySize`, `MipLevels`） |
| UINT64/SIZE_T | 8 bytes |
| COM インターフェースポインタ | `u32 オブジェクト ID`（0 = null）。**ID は export-to-cpp の `GetResource(N)` 等と一致** |
| REFIID/GUID | 16 bytes |
| ポインタ（単一構造体 `_In_ const T*`） | `u8 present` + T |
| ポインタ（配列 `_In_reads_(N)` 等） | `u8 present` + `u64 count` + 要素 × count |
| `void*` データ | `u8 present` + `u64 bytes` + bytes |
| 文字列 LPCWSTR/LPCSTR | `u8 present` + `u64 文字数` + 文字（**終端なし**） |
| 関数引数の固定長配列 (`const FLOAT ColorRGBA[4]`) | `u8 present` + `u64 count` + 要素 |
| 構造体内の固定長配列 (`RenderTarget[8]`, `RTVFormats[8]`) | `u64 count` + 要素（present なし） |
| 構造体内のポインタ (`pShaderBytecode`, `pInputElementDescs`, `SemanticName`) | `u8 present` + `u64 count` + data。対応する長さフィールド（`BytecodeLength`, `NumElements`）も別途そのまま書かれる |
| union | 判別フィールドで選ばれたメンバのみ（`D3D12_RESOURCE_BARRIER.Type`, `*_VIEW_DESC.ViewDimension`, `D3D12_TEXTURE_COPY_LOCATION.Type` 等） |
| **例外**: `D3D12_CLEAR_VALUE` | union の**全メンバ**を書く: `Format, u64 4 + float Color[4], {f32 Depth, u8 Stencil}` |
| `D3D12_GPU_VIRTUAL_ADDRESS` | `u64 offset` + `u32 resourceId`（**リソース相対に正規化**されている） |
| `D3D12_GPU_DESCRIPTOR_HANDLE` | `u32 heapId` + `u32 index` |
| `D3D12_CPU_DESCRIPTOR_HANDLE` | ハンドル値ではなく**ディスクリプタの中身**（§3.2） |

- SAL では単一ポインタだが PIX が配列として書く引数がある: `OMSetRenderTargets.pRenderTargetDescriptors`（`ARRAY_PARAM_OVERRIDES`）。
- union の選択表（`UNION_SELECTORS`）で `""` は「その判別値では union に何も書かない」を表す
  （`D3D12_INDIRECT_ARGUMENT_DESC` の DRAW/DISPATCH、`D3D12_RENDER_PASS_BEGINNING_ACCESS` の PRESERVE 等）。
  `D3D12_BARRIER_GROUP` は Type で `pGlobalBarriers / pTextureBarriers / pBufferBarriers` を選ぶ。
- **RenderPass の `D3D12_CLEAR_VALUE` は 1700 と違い片方だけ**: Format が深度なら `DepthStencil`、それ以外は `Color[4]` ✅
- 構造体内の CPU ディスクリプタハンドル（`D3D12_RENDER_PASS_RENDER_TARGET_DESC.cpuDescriptor` → RTV、
  `D3D12_RENDER_PASS_DEPTH_STENCIL_DESC.cpuDescriptor` → DSV）も §3.2 の形式。
- `D3D12_BUILD_RAYTRACING_ACCELERATION_STRUCTURE_INPUTS`: union は Type（TOP_LEVEL → `InstanceDescs` (GPUVA)）と
  DescsLayout（ARRAY → `pGeometryDescs` / ARRAY_OF_POINTERS → `ppGeometryDescs`）で選択。TOP_LEVEL では GPUVA の後に `u64 ?`（0）✅
- union 内の無名 struct（例: `D3D12_INDIRECT_ARGUMENT_DESC` の `Constant`）は、ヘッダパーサが合成型 `<親>.<名前>` として扱う。

### 3.x メソッド末尾の追加データ（PIX が付け足す） ✅

一部の API は引数の後ろに、対応するスナップショットレコードの seq 等が続く（デコード結果では `args["_pix"]`）。

| メソッド | 追加データ |
|---|---|
| ExecuteIndirect | `u64 argumentsSeq` → PIX::IndirectArguments (2030) |
| DispatchRays | `u64 shaderTablesSeq` → PIX::ShaderTables (2038) |
| BuildRaytracingAccelerationStructure | `u64 instanceDescsSeq` → PIX::InstanceDescs (2039)（TLAS） |
| ResolveQueryData | `u8 present + u64 resultSeq (→ 2046) + u64 n + u64[n]` |
| ExecuteBundle | `u32 bundle, u64 firstSeq, u64 numRecords`（ExecuteCommandLists と同じ） |

### 3.y PIX マーカー（BeginEvent / SetMarker の pData） ✅

| Metadata | 内容 |
|---|---|
| 0 | UTF-16 文字列（`WINPIX_EVENT_UNICODE_VERSION`） |
| 1 | ANSI 文字列 |
| 2 | PIX3 blob（WinPixEventRuntime の `PIXBeginEvent(cmdList, color, "...")`）: `UINT64[]` = `eventInfo`（種別 = bits 10-19: BeginEvent_NoArgs=2, SetMarker_NoArgs=8, VarArgs=1/7）, `color`, `stringInfo`（isANSI = bit 54, copyChunkSize = bits 55-59）, 文字列（8 バイト単位・終端込み）。末尾の BlockEndMarker はサイズに含まれない |

デコード結果では `args["_text"]` に文字列化した値を入れる。サンプルアプリで生成した PIX3 マーカーは PIX 自身も同じ文字列として表示した（エンコードの正しさを確認）。
`WINPIX_EVENT_PIX3BLOB_V2`（新しい WinPixEventRuntime）の形式は未検証。

### 3.1 例: ResourceBarrier（41 bytes）

```
05 00 00 00                  this = commandList #5
01 00 00 00                  NumBarriers = 1
01 01 00 00 00 00 00 00 00   pBarriers: present, count=1
00 00 00 00 00 00 00 00      Type=TRANSITION, Flags=NONE
08 00 00 00 ff ff ff ff      pResource=#8, Subresource=ALL
00 00 00 00 08 00 00 00      StateBefore=COMMON, StateAfter=UNORDERED_ACCESS
```

### 3.2 CPU ディスクリプタハンドル ✅

ハンドルが指すディスクリプタの**作成時の情報**が展開されて記録される。

- RTV / DSV（引数名から種別が決まる: `RenderTargetView`, `pRenderTargetDescriptors`, `DepthStencilView`, `pDepthStencilDescriptor`）
  ```
  u8 present + D3D12_RENDER_TARGET_VIEW_DESC / D3D12_DEPTH_STENCIL_VIEW_DESC
  u32 resource
  u8 present + D3D12_RESOURCE_DESC (48 bytes)
  ```
- CBV/SRV/UAV ヒープ上（`ClearUnorderedAccessView*.ViewCPUHandle`）: §3.3 の「ディスクリプタ」形式

### 3.3 ディスクリプタ（CBV_SRV_UAV） ✅

```
u32 kind   0=Empty 1=CBV 2=SRV 3=UAV 6=Sampler (4, 5 は未観測 ❓)
kind=1: u8 present + D3D12_CONSTANT_BUFFER_VIEW_DESC         (BufferLocation は GPUVA 形式)
kind=6: u8 present + D3D12_SAMPLER_DESC                      (Sampler ヒープ。UE5 で観測 ✅)
kind=2: u8 present + D3D12_SHADER_RESOURCE_VIEW_DESC, u32 resource, u8 present + D3D12_RESOURCE_DESC
kind=3: u8 present + D3D12_UNORDERED_ACCESS_VIEW_DESC, u32 resource, u32 counterResource, u8 present + D3D12_RESOURCE_DESC
```
❓ counterResource ≠ 0 の場合、RTV/DSV がヒープスナップショットに出るケースは未検証。

### 3.4 ExecuteCommandLists（typeId 1065, 独自形式） ✅

```
u32 queue, u32 NumCommandLists, u64 count,
{ u32 commandList, u64 firstSeq, u64 numRecords } × count
```
コマンドリストの記録は Reset から Close まで。`firstSeq` は先頭（Reset）の seq、
**`numRecords` は seq の範囲幅ではなく「そのリストに属するレコード数」** ✅
（間に Bundle の記録 (stream 1002) や実行時スナップショット (stream 2000) が別 seq で挟まるため）。
再構成は「seq >= firstSeq かつ `this == commandList` のレコードを先頭から numRecords 件」。
`PIX::PrepareForPresent`（1860, `commandList` フィールドを持つ）もそのリストのレコードに数える。

### 3.5 ExecuteBundle と Bundle ✅

Bundle の Reset〜Close は stream 1002 に記録され、`ExecuteBundle` の追加データ（§3.x）で参照される。
Bundle はルートシグネチャ等を呼び出し元から継承し、Bundle 内のルート引数変更は呼び出し元に残る（D3D12 の仕様どおり）。

### 3.6 ID3D12Device2::CreatePipelineState（1629, 独自形式） ✅

Stream（サブオブジェクト列）ではなく、PIX が**全サブオブジェクトを固定順の構造体に展開**して記録する:

```
u32 this, u32 hr, u8 present +
  u32 pRootSignature, SHADER_BYTECODE VS, PS, DS, HS, GS, CS,
  D3D12_STREAM_OUTPUT_DESC, D3D12_BLEND_DESC, u32 SampleMask,
  D3D12_RASTERIZER_DESC2, D3D12_DEPTH_STENCIL_DESC2 (56B),
  D3D12_INPUT_LAYOUT_DESC, IBStripCutValue, PrimitiveTopologyType, u32 NumRenderTargets,
  u64 8 + DXGI_FORMAT RTVFormats[8], DSVFormat, DXGI_SAMPLE_DESC, u32 NodeMask, CachedPSO, Flags,
  u8[16] ❓, SHADER_BYTECODE MS, SHADER_BYTECODE AS, u8[3] ❓
GUID riid, u8 present + u32 pipelineState
```
- 省略したサブオブジェクトは既定値で埋まる（例: Blend は全 RT が既定）
- CS の位置（GS の直後）は Compute 用 Stream PSO、MS/AS の順序は AS 付き Mesh PSO で確定（差分解析）

### 3.7 PIX::CreateStateObject（2045） ✅

```
u32 ?, u32 device,
u8 present + u64 count(=1) + D3D12_STATE_OBJECT_DESC {
    u32 Type, u32 NumSubobjects,
    u64 count + { u32 Type, u8 present + 種別ごとの desc }[count] }
u8[16] ❓, GUID riid, u8 present + u32 stateObject
```
- 種別ごとの desc は D3D12 の構造体（DXIL_LIBRARY, HIT_GROUP, RAYTRACING_SHADER_CONFIG, GLOBAL/LOCAL_ROOT_SIGNATURE, PIPELINE_CONFIG ...）
- **サブオブジェクト内のポインタ配列（`pExports` 等）は present フラグなしで `u64 count` + 要素**（文字列は通常どおり present + 文字数）
- SUBOBJECT_TO_EXPORTS_ASSOCIATION（他サブオブジェクトへのポインタを含む）は未検証

---

## 4. 状態スナップショット（キャプチャ開始時点の状態）

PIX はキャプチャ開始時点で生存していて**フレーム内で参照された**オブジェクトを「生成」レコードとして記録する
（未参照のサンプラーヒープ等は出てこない）。アプリが実際に呼んだ API とは異なる場合がある
（例: CreateCommandList → `ID3D12Device4::CreateCommandList1`、Fence の InitialValue はキャプチャ時の値）。

### 4.1 PIX::CreateResource (1700) ✅

```
u32 device, u32 resource
D3D12_HEAP_PROPERTIES (20)
u32 HeapFlags
D3D12_RESOURCE_DESC1 (60)          ← SamplerFeedbackMipRegion (u32×3) 付き
u32 stateAtCaptureStart            ← キャプチャ開始時のステート (作成時ではない。= subresourceState[0]) ✅
u8 present + D3D12_CLEAR_VALUE     ← union 全メンバ (§3)
GUID riid (ID3D12Resource)
u64 initialDataSeq                 ← 対応する 2029 レコードの seq
u64 n + u32 subresourceState[n]    ← サブリソース毎のキャプチャ時ステート
```

### 4.2 PIX::ResourceInitialData (2029) ✅

```
u32 resource, u32 ❓(=1), u64 ❓(=0)
u64 n
D3D12_PLACED_SUBRESOURCE_FOOTPRINT[n]   (28 bytes each: u64 Offset, Format, Width, Height, Depth, RowPitch)
u64 totalSize
u8 data[totalSize]                      ← GetCopyableFootprints のレイアウト
```
- バッファは footprint が全て 0、data = バッファ全体
- D24S8 は 2 面（plane0 = R32_TYPELESS, plane1 = R8_TYPELESS）、ミップ付きテクスチャはミップ数分
- 行パディング部分には未初期化のゴミが入っていることがある

### 4.3 PIX::DescriptorHeap (2044) 🔶

**内容は実行時点のもの**（キャプチャ開始時ではない）✅: フレーム内で `CreateShaderResourceView` / `CopyDescriptorsSimple` で
書き換えたスロットは書き換え後の内容で記録され、書き換え呼び出し自体はレコードとして現れない。
（シェーダ可視ヒープのディスクリプタは実行時に読まれるため、GPU が見た内容と一致する）

検証方法: サンプルはキャプチャされるフレームだけスロット 3/4 を texB/texC に書き換え、他のフレームでは texA に戻す。
キャプチャ開始時点のヒープは texA だが、2044 には texB/texC が記録される（`--extras` キャプチャ、`draws` の t0/t1 で確認できる）。
なお `CaptureNextFrame` を frame N の描画直前に呼ぶと、実際にキャプチャされるのは frame N+1 だった ✅（同じ実験で判明）。

```
u32 ❓(=0), u32 device
u8 present + D3D12_DESCRIPTOR_HEAP_DESC   (NodeMask は 1 を観測)
u32 ❓(=0)
u8 present + u64 count + ディスクリプタ[count]   ← 先頭から count スロット。空きは kind=0 (4 bytes)
                                                   自作サンプルでは count = NumDescriptors。UE5 の 100 万スロットのヒープでは
                                                   count = 1 で、中身は 2033 (§4.8) で届く
u32 numDescriptorEntries (= NumDescriptors) ✅
GUID riid (ID3D12DescriptorHeap)
u8 present + u32 heapId
```

### 4.4 PIX::CreatePlacedResource (1767) / PIX::CreateReservedResource (1770) ✅

```
1767: u32 device, u32 resource, u32 heap, u64 heapOffset, D3D12_RESOURCE_DESC1, <後半>
1770: u32 device, u32 resource, D3D12_RESOURCE_DESC1, <後半 (state の後に u32 ❓)>, ... u64 tileMappingsSeq (→ 2037) を
      initialDataSeq の後に持ち、末尾にタイリング情報 (GetResourceTiling 相当と推定) ❓
<後半> = u32 state, u8 present + CLEAR_VALUE, GUID riid, u64 initialDataSeq, u64 n + u32 states[n]
```

### 4.5 Enhanced Barriers で作成したテクスチャ（レイアウト管理）✅ / 判別は 🔶

`CreateCommittedResource3` 等で**レイアウト**を持つテクスチャは、1700 の後半が次の形になる:
```
u32 1, u32 layoutAtCaptureStart (D3D12_BARRIER_LAYOUT), CLEAR_VALUE, riid, u64 seq,
u64 n + { u32 ❓(1), u32 layout }[n], u64 ❓
```
レガシーステートとの判別: テクスチャで先頭 u32 == 1（= `VERTEX_AND_CONSTANT_BUFFER`、テクスチャでは無効なステート）なら
レイアウト形式とみなす（🔶）。バッファはレイアウトを持たないためレガシー形式で記録される。

### 4.6 PIX::IndirectArguments (2030) ✅

```
u8[16] ❓, u64 numCommands,
{ { u32 argumentType, 引数 }[コマンドシグネチャの引数記述子数] }[numCommands],
u32 maxCommandCount?, u32 commandCount?, u32 ❓, u8[numCommands] ❓,
D3D12_COMMAND_SIGNATURE_DESC, u64 size + 引数バッファの生バイト
```
引数: DRAW = D3D12_DRAW_ARGUMENTS, DRAW_INDEXED, DISPATCH / DISPATCH_MESH = D3D12_DISPATCH_ARGUMENTS, CONSTANT = u32 × N。
（VERTEX/INDEX_BUFFER_VIEW, CBV/SRV/UAV, DISPATCH_RAYS 引数は未検証）
解釈にはコマンドシグネチャが必要なので、直前の ExecuteIndirect の `pCommandSignature` を使う。

### 4.7 PIX::InstanceDescs (2039) ✅

```
u8[16] ❓, u64 n,
{ u64 12 + float Transform[12], u32 InstanceID, u32 InstanceMask, u32 ContributionToHitGroupIndex, u32 Flags,
  GPUVA AccelerationStructure (u64 offset + u32 resource) }[n]
```
（`D3D12_RAYTRACING_INSTANCE_DESC` のビットフィールドは u32 に展開され、BLAS アドレスはリソース ID に正規化される）

---

### 4.8 PIX::WriteDescriptors (2033) ✅

フレーム中にシェーダ可視ヒープへ書き込まれたディスクリプタ（UE5 の GlobalResourceHeap / Sampler ヒープで 14,183 件）。

```
u32 ❓(=0), u32 heap, u32 index, u32 count
u8 present + u64 n + ディスクリプタ[n] (§3.3)      → heap[index .. index+n) の新しい内容
```

GPU は実行時にディスクリプタを読むので、**コマンドリストが見る内容 = 2044 + そのリストの ExecuteCommandLists より前の 2033**
として `draws` を解決する 🔶。UE5 のキャプチャで、シェーダが使うスロット（§4.10）20,274 個が全て解決し、
レジスタ種別（t/u/s）とディスクリプタ種別が全て一致した。ただし「SetXRootDescriptorTable より前」「Draw より前」の 2033 を
適用するモデルでも使用スロットの結果は 1 件も変わらないため、タイミングの正しさまでは区別できていない
（違いが出るのは未使用スロットだけ）。
自作サンプル（ヒープ 8 スロット）のフレーム内 CreateSRV / CopyDescriptorsSimple では 2033 は出ず、2044 に反映された（§4.3）。
どの条件で 2033 になるか（ヒープの大きさ・書き込み時期）は ❓。

### 4.9 PIX::UploadPages (2034) ✅

CPU が書き込んだ（Map された）アップロードヒープの内容。**コマンドリストの Reset の直前**に記録される（UE5 で 78 件全て, 最大 61 MB）。

```
u32 ❓(=0)
u64 M, M × { u64 resource, u64 N, N × { u64 page, u64 size(=0x1000), u8[size], u64 size }, u64 N }
u32 M
```

`resource=0, N=0` の空エントリが混じる。`page × size` がリソース先頭からのオフセット。resource は全て
`D3D12_HEAP_TYPE_UPLOAD` のバッファ（4 MB / 8 MB の UE のアロケータページ）で、中身は BC 圧縮テクスチャ・頂点データ等 ✅。
4 KB ページ単位なので、PIX は書き込み監視（write watch）で変更ページだけを記録していると推定 🔶。

### 4.10 シェーダが使うバインディング（draws の `used`） ✅

UE5 などはディスクリプタテーブルを大きめに宣言し（SRV 64 個等）、未使用スロットには他の割り当ての残骸が入る
（`DESCRIPTORS_VOLATILE` なので D3D12 として正当）。PSO の各ステージのシェーダから実際に使うレジスタを読み、
ルートパラメータの `ShaderVisibility` に対応するステージ分だけで判定する（PIXEL 可視のテーブルは PS だけ）。

- DXIL: `PSV0` パート（UE5 の 338 シェーダ全てで `dxc -dumpbin` の Resource Bindings と一致 ✅）: `u32 runtimeInfoSize, PSVRuntimeInfo, u32 resourceCount, u32 bindInfoSize, {u32 ResType, Space, LowerBound, UpperBound, ...}[]`
- DXBC: `RDEF` チャンクのバインド記述（5.1 以降 40 B: 末尾に space, id。5.1 は `fxc /dumpbin` と一致 ✅、5.0 の 32 B は未検証）
- compute パイプラインは ShaderVisibility に関係なく CS で判定する
- 判定できない場合（DispatchRays のライブラリ等）は `used = null` で全スロットを表示する

## 5. 未解明・今後の課題

- ファイルヘッダ/セクショントレーラの各フィールド、`META` の 20 bytes、EVT ヘッダ reserved
- D3D12CreateDevice (1566) 後半の caps 構造、2043
- 2037（タイルマッピング）・2038（シェーダテーブル）の詳細構造、2041 の ❓ 部分、2139 の 16 B ヘッダ、2030/2039/1629/2045 の ❓ 領域
- BLAS をフレーム内でビルドするケース（`pGeometryDescs` のシリアライズ）、EmitPostbuildInfo / CopyRaytracingAccelerationStructure
- 未検証の引数種別: ExecuteIndirect の VB/IB/CBV/SRV/UAV/DISPATCH_RAYS 引数、SUBOBJECT_TO_EXPORTS_ASSOCIATION
- 2033 と 2044 の使い分け条件、ディスクリプタ kind 4/5、2034 の先頭 u32
- pixtool のイベント一覧は、UE5 キャプチャのコマンドリストを 158 本しか表示しない（ファイルには Reset/Close 付きで 172 本。
  差の 14 本はタイムスタンプ用の小さなリスト #4 の記録と見られるが、どれが除外されたかは未特定）。理由は ❓
- WorkGraphs（DispatchGraph / SetProgram）、`WINPIX_EVENT_PIX3BLOB_V2` マーカー
- PIX のバージョン差（ファイルヘッダ値との対応）
