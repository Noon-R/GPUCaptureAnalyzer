# PIX GPU Capture (.wpix) バイナリフォーマット解析ノート

- 対象: **PIX 2603.25** で `WinPixGpuCapturer.dll` の `CaptureNextFrame` により取得した D3D12 GPU キャプチャ
- 確度: ✅ 確定（複数キャプチャ・既知値・PIX の export-to-cpp で検証済み） / 🔶 推定（整合するが反例テスト未実施） / ❓ 未解明
- 実装: `tools/gcap/wpix/`（container → events → decoder / pix_events）
- 検証: `tests/test_wpix.py`（manifest 照合 + export-to-cpp 照合）。加えて独立検証で export-to-cpp の全コマンド・全リソース生成パラメータ・ヒープ全スロット・PSO 全フィールド・`resources.bin` の初期データ（バイト単位）と一致を確認（v0〜v3）

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
| 3,4,5,7,8,9,10,11,14 | オブジェクト生成（種類別: Queue, GraphicsPSO, ComputePSO, ?, DescriptorHeap, Allocator, CommandList, RootSig, Fence） |
| 100 / 101 | リソース生成 (1700) / リソース初期内容 (2029) |
| 300 | SetName |
| 1000 | キュー操作 (ExecuteCommandLists, Signal, Wait, Present) |
| 1001 | コマンドリストの記録（全コマンドリスト共通） |
| 10000 / 10001 | 付随 blob の参照 / 本体（スクリーンショット PNG） |
| 10002 | キャプチャ環境情報 |

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
| ID3D12CommandQueue | 1063 | ExecuteCommandLists=1065, Signal=1069, Wait=1070 |
| ID3D12Device | 1074 | CreateCommandQueue=1075, CreateRootSignature=1083, CreateFence=1103 |
| ID3D12Device4 | 1711 | CreateCommandList1=1711 |

base の間隔はヘッダ上のインターフェース順と完全には一致しない（間に別インターフェースが入る）。
新しい API を使ったキャプチャで未知 typeId が出たら、`this` の型とペイロード長の完全一致で base を特定して追記する。

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
| 1065 | ExecuteCommandLists | 汎用規則と異なる独自形式。§3.4 |

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
u32 kind   0=Empty 1=CBV 2=SRV 3=UAV (4 以降 ❓)
kind=1: u8 present + D3D12_CONSTANT_BUFFER_VIEW_DESC         (BufferLocation は GPUVA 形式)
kind=2: u8 present + D3D12_SHADER_RESOURCE_VIEW_DESC, u32 resource, u8 present + D3D12_RESOURCE_DESC
kind=3: u8 present + D3D12_UNORDERED_ACCESS_VIEW_DESC, u32 resource, u32 counterResource, u8 present + D3D12_RESOURCE_DESC
```
❓ counterResource ≠ 0 の場合、Sampler ヒープ、RTV/DSV がヒープスナップショットに出るケースは未検証。

### 3.4 ExecuteCommandLists（typeId 1065, 独自形式） ✅

```
u32 queue, u32 NumCommandLists, u64 count,
{ u32 commandList, u64 firstSeq, u64 numRecords } × count
```
`firstSeq .. firstSeq+numRecords-1` がそのコマンドリストの記録（stream 1001 の seq）。
コマンドリストの記録は Reset から Close までで、ECL から逆引きしてコマンド列を再構成できる。

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

```
u32 ❓(=0), u32 device
u8 present + D3D12_DESCRIPTOR_HEAP_DESC   (NodeMask は 1 を観測)
u32 ❓(=0)
u8 present + u64 count(=NumDescriptors) + ディスクリプタ[count]   ← 全スロット。空きは kind=0 (4 bytes)
u32 numDescriptorEntries (= NumDescriptors) ✅
GUID riid (ID3D12DescriptorHeap)
u8 present + u32 heapId
```

---

## 5. 未解明・今後の課題

- ファイルヘッダ/セクショントレーラの各フィールド、`META` の 20 bytes、EVT ヘッダ reserved
- D3D12CreateDevice (1566) 後半の caps 構造、2043
- 未カバー API: ExecuteIndirect, Barrier (Enhanced Barriers), RenderPass, DispatchRays / DXR, Mesh shader, PipelineStateStream (CreatePipelineState), Placed/Reserved resources, 複数キュー, Bundle, Query, CopyDescriptors, PIX3 形式のマーカー (Metadata=2)
- 複数フレームキャプチャ、大容量リソースの複数ブロック分割
- PIX のバージョン差（ファイルヘッダ値との対応）
