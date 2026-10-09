# gcap.exe の出力例

`captures/pix_ext/v0.wpix`（サンプルアプリ `--extras --dxil`: 拡張 API 一式を使った 1 フレーム）に対する実際の出力。
`python scripts/gen_tool_output_doc.py` で再生成できる（マシン名・パスは伏せ字）。長い行は「…」で省略。

| コマンド | 出力 | 用途 |
|---|---|---|
| `wpix info` | コンテナ構造（ストリーム・ブロック・レコード数・含まれる種別） | ファイルの全体像、未知レコードの所在 |
| `wpix calls [--json] [--failed]` | 全レコードのデコード結果（1 行 1 レコード / JSON Lines） | 全 API 呼び出しの機械処理、デコード失敗の確認 |
| `wpix cmdlists` | キュー・コマンドリスト単位のコマンド列（マーカー階層・Bundle 展開付き） | フレームの流れを読む |
| `wpix objects` | オブジェクト一覧（ID・型・名前・リソース情報） | 名前と ID の対応、リソースの一覧 |
| `wpix draws [--json]` | Draw/Dispatch 毎の PSO・ルート引数・実リソース・RT/DS・間接引数・シェーダテーブル | 「このドローは何を読んで何に書いたか」の自動解析 |
| `wpix extract -o DIR` | リソース初期データ（RGBA8 は PNG も）・シェーダ・ルートシグネチャ・スクリーンショット | データの取り出し |
| `wpix shaders -o DIR` | シェーダ + PDB 照合（HLSL ソース・コンパイル引数）/ 未発見は IL | シェーダのデバッグ情報の回収 |
| `meta rebuild` | SDK ヘッダから型メタデータを再生成 | SDK 更新時 |

## wpix info

```
file: <repo>\captures\pix_ext\v0.wpix  size=0xe8ce
header: u32@0x0c=1 u32@0x10=1 u32@0x40=(2, 3)
section <CAP> version=2 offset=0x400 size=0xe08e
  stream blocks       comp        raw  recs  types
       2      1        394        739     1  1566:D3D12CreateDevice
       3      1        145        402     3  1075:CreateCommandQueue
       4      1       5043       9361     1  1077:CreateGraphicsPipelineState
       5      1       3344       6772     2  1078:CreateComputePipelineState
       6      1      12939      31608     4  1629:CreatePipelineState
       7      1         71         92     1  2043:Unknown2043
       8      1        327       1283     2  2044:DescriptorHeap
       9      1        154        605     5  1076:CreateCommandAllocator
      10      1        177        645     5  1711:CreateCommandList1
      11      1        552       1474     5  1083:CreateRootSignature
      12      1        134        294     2  1108:CreateCommandSignature
      13      1        125        260     2  1106:CreateQueryHeap
      14      1        143        645     5  1103:CreateFence
      15      1        141        316     2  1095:CreateHeap
      17      1       4362       6288     1  2045:CreateStateObject
     100      1       1393       7511    33  1700:CreateResource, 1767:CreatePlacedResource, 1770:CreateReservedResource
     101      1      18402    1043564    31  2029:ResourceInitialData
     102      1        110        188     1  2037:TileMappings
     103      1        417       2548     2  2041:AccelerationStructureData
     300      1       1790       9011    67  973:SetName
    1000      1        329       1549    13  1063:UpdateTileMappings, 1065:ExecuteCommandLists, 1069:Signal, 1070:Wait, 1885:Present
    1001      1       2648      12782   102  992:Close, 993:Reset, 995:DrawInstanced, 996:DrawIndexedInstanced, 997:Dispatch, 998:CopyBufferRegion, ... (46 types)
    1002      1        164        629     6  992:Close, 993:Reset, 995:DrawInstanced, 1003:IASetPrimitiveTopology, 1008:SetPipelineState, 1019:SetGraphicsRoot32BitConstants
    2000      1        376       1179     6  2030:IndirectArguments, 2038:ShaderTables, 2039:InstanceDescs, 2046:QueryResults
    2001      1        109        712     1  2139:ShaderTableRecords
   10000      1         83        120     1  36:BlobReference
   10001      1       1685       2081     1  37:BlobData
   10002      1        425        573     1  2042:CaptureEnvironment
```

## wpix calls（抜粋）

```
14 [  100] PIX::CreateResource(device=2, resource=7, heapProperties={Type=D3D12_HEAP_TYPE_UPLOAD, CPUPageProperty=D3D12_CPU_PAGE_PROPERTY_UNKNOWN, MemoryPoolPreference=D3D12_MEMORY_POOL_UNKNOWN, CreationNodeMask=0, V …
    17 [  100] PIX::CreateResource(device=2, resource=8, heapProperties={Type=D3D12_HEAP_TYPE_DEFAULT, CPUPageProperty=D3D12_CPU_PAGE_PROPERTY_UNKNOWN, MemoryPoolPreference=D3D12_MEMORY_POOL_UNKNOWN, CreationNodeMask=0,  …
    36 [  100] PIX::CreateResource(device=2, resource=15, heapProperties={Type=D3D12_HEAP_TYPE_DEFAULT, CPUPageProperty=D3D12_CPU_PAGE_PROPERTY_UNKNOWN, MemoryPoolPreference=D3D12_MEMORY_POOL_UNKNOWN, CreationNodeMask=0, …
   190 [ 1001] #20.BeginEvent(Metadata=0, pData=<26 bytes>, Size=26, _text="GCA_Frame_v0")
   191 [ 1001] #20.BeginEvent(Metadata=0, pData=<38 bytes>, Size=38, _text="GCA_ComputePass_v0")
   206 [ 1001] #20.BeginEvent(Metadata=0, pData=<32 bytes>, Size=32, _text="GCA_DrawPass_v0")
   234 [ 1001] #20.Barrier(NumBarrierGroups=3, pBarrierGroups=[{Type=D3D12_BARRIER_TYPE_GLOBAL, NumBarriers=1, pGlobalBarriers=[{SyncBefore=D3D12_BARRIER_SYNC_ALL, SyncAfter=D3D12_BARRIER_SYNC_ALL, AccessBefore=D3D12_BAR …
   237 [ 1001] #20.Barrier(NumBarrierGroups=2, pBarrierGroups=[{Type=D3D12_BARRIER_TYPE_BUFFER, NumBarriers=1, pBufferBarriers=[{SyncBefore=D3D12_BARRIER_SYNC_COPY, SyncAfter=D3D12_BARRIER_SYNC_NONE, AccessBefore=D3D12_B …
   238 [ 1001] #20.BuildRaytracingAccelerationStructure(pDesc={DestAccelerationStructureData=GPUVA(#32(GCA_TLAS)+0), Inputs={Type=D3D12_RAYTRACING_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL, Flags=D3D12_RAYTRACING_ACCELERATIO …
   239 [ 2000] PIX::InstanceDescs(unk16?="00000000000000000000000000000000", instances=[{Transform=[1, 0, 0, 0, 0, 1, 0, 0, ...(12)], InstanceID=7, InstanceMask=255, InstanceContributionToHitGroupIndex=0, Flags=0, Accele …
   246 [ 1001] #20.DispatchRays(pDesc={RayGenerationShaderRecord={StartAddress=GPUVA(#68(GCA_ShaderTable)+0), SizeInBytes=64}, MissShaderTable={StartAddress=GPUVA(#68(GCA_ShaderTable)+64), SizeInBytes=64, StrideInBytes=6 …
   252 [ 1001] #20.ExecuteIndirect(pCommandSignature=#61(GCA_DispatchSignature), MaxCommandCount=1, pArgumentBuffer=#36(GCA_IndirectArgs), ArgumentBufferOffset=32, pCountBuffer=null, CountBufferOffset=0, _pix={argumentsS …
   253 [ 2000] PIX::IndirectArguments(unk16?="00000000000000000000000000000000", commands=[[{type=D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH, args={ThreadGroupCountX=2, ThreadGroupCountY=1, ThreadGroupCountZ=1}}]], maxCommand …
   257 [ 1001] #20.BeginRenderPass(NumRenderTargets=1, pRenderTargets=[{cpuDescriptor={kind="RTV", view=null, resource=#30(GCA_ExtRT)}, BeginningAccess={Type=D3D12_RENDER_PASS_BEGINNING_ACCESS_TYPE_CLEAR, Clear={ClearVal …
   268 [ 1001] #20.ExecuteIndirect(pCommandSignature=#60(GCA_DrawSignature), MaxCommandCount=2, pArgumentBuffer=#36(GCA_IndirectArgs), ArgumentBufferOffset=0, pCountBuffer=null, CountBufferOffset=0, _pix={argumentsSeq=26 …
   269 [ 2000] PIX::IndirectArguments(unk16?="00000000000000000000000000000000", commands=[[{type=D3D12_INDIRECT_ARGUMENT_TYPE_DRAW, args={VertexCountPerInstance=3, InstanceCount=1, StartVertexLocation=0, StartInstanceLo …
   276 [ 1001] #20.ExecuteBundle(pCommandList=#69, _pix={list=69, firstSeq=270, numRecords=6})
   278 [ 1001] #20.DispatchMesh(ThreadGroupCountX=1, ThreadGroupCountY=1, ThreadGroupCountZ=1)
   280 [ 1001] #20.DispatchMesh(ThreadGroupCountX=2, ThreadGroupCountY=1, ThreadGroupCountZ=1)
   284 [ 2000] PIX::QueryResults(size=16, values=[107343983798604, 107343983798696])
   286 [ 2000] PIX::QueryResults(size=8, values=[4096])
```

`--json`（1 レコード）:

```json
{"seq":227,"stream":1001,"type":996,"api":"ID3D12GraphicsCommandList::DrawIndexedInstanced","this":20,"args":{"IndexCountPerInstance":6,"InstanceCount":1,"StartIndexLocation":0,"BaseVertexLocation":0,"StartInstanceLocation":0}}
```

## wpix cmdlists（抜粋）

```
=== ExecuteCommandLists seq=22 queue=#4(GCA_CopyQueue) list=#5 (3 commands)
     21 Close()
=== ExecuteCommandLists seq=44 queue=#10(GCA_ComputeQueue) list=#11 (6 commands)
     42 Dispatch(ThreadGroupCountX=4, ThreadGroupCountY=1, ThreadGroupCountZ=1)
     43 Close()
=== ExecuteCommandLists seq=291 queue=#1(GCA_DirectQueue) list=#20 (90 commands)
  > GCA_Frame_v0
    > GCA_ComputePass_v0
        192 ResourceBarrier(NumBarriers=1, pBarriers=[{Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, Flags=D3D12_RESOURCE_BARRIER_FLAG_NONE, Transition={pResource=#23(GCA_UavBuffer), Subresource=4294967295, StateBefore=D3 …
        198 Dispatch(ThreadGroupCountX=7, ThreadGroupCountY=3, ThreadGroupCountZ=1)
        200 ResourceBarrier(NumBarriers=1, pBarriers=[{Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, Flags=D3D12_RESOURCE_BARRIER_FLAG_NONE, Transition={pResource=#23(GCA_UavBuffer), Subresource=4294967295, StateBefore=D3 …
      202 ResourceBarrier(NumBarriers=1, pBarriers=[{Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, Flags=D3D12_RESOURCE_BARRIER_FLAG_NONE, Transition={pResource=#26(GCA_Texture8x8), Subresource=4294967295, StateBefore=D3D …
      204 ResourceBarrier(NumBarriers=1, pBarriers=[{Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, Flags=D3D12_RESOURCE_BARRIER_FLAG_NONE, Transition={pResource=#26(GCA_Texture8x8), Subresource=4294967295, StateBefore=D3D …
    > GCA_DrawPass_v0
        207 ResourceBarrier(NumBarriers=1, pBarriers=[{Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, Flags=D3D12_RESOURCE_BARRIER_FLAG_NONE, Transition={pResource=#22(GCA_BackBuffer0), Subresource=4294967295, StateBefore= …
      * GCA_SetMarker_v0
        226 DrawInstanced(VertexCountPerInstance=3, InstanceCount=1, StartVertexLocation=0, StartInstanceLocation=0)
        227 DrawIndexedInstanced(IndexCountPerInstance=6, InstanceCount=1, StartIndexLocation=0, BaseVertexLocation=0, StartInstanceLocation=0)
        228 ResourceBarrier(NumBarriers=1, pBarriers=[{Type=D3D12_RESOURCE_BARRIER_TYPE_TRANSITION, Flags=D3D12_RESOURCE_BARRIER_FLAG_NONE, Transition={pResource=#22(GCA_BackBuffer0), Subresource=4294967295, StateBefore= …
    > GCA_Pix3Begin_v0
        234 Barrier(NumBarrierGroups=3, pBarrierGroups=[{Type=D3D12_BARRIER_TYPE_GLOBAL, NumBarriers=1, pGlobalBarriers=[{SyncBefore=D3D12_BARRIER_SYNC_ALL, SyncAfter=D3D12_BARRIER_SYNC_ALL, AccessBefore=D3D12_BARRIER_AC …
        237 Barrier(NumBarrierGroups=2, pBarrierGroups=[{Type=D3D12_BARRIER_TYPE_BUFFER, NumBarriers=1, pBufferBarriers=[{SyncBefore=D3D12_BARRIER_SYNC_COPY, SyncAfter=D3D12_BARRIER_SYNC_NONE, AccessBefore=D3D12_BARRIER_ …
        240 ResourceBarrier(NumBarriers=1, pBarriers=[{Type=D3D12_RESOURCE_BARRIER_TYPE_UAV, Flags=D3D12_RESOURCE_BARRIER_FLAG_NONE, UAV={pResource=#32(GCA_TLAS)}}])
        246 DispatchRays(pDesc={RayGenerationShaderRecord={StartAddress=GPUVA(#68(GCA_ShaderTable)+0), SizeInBytes=64}, MissShaderTable={StartAddress=GPUVA(#68(GCA_ShaderTable)+64), SizeInBytes=64, StrideInBytes=64}, Hit …
        252 ExecuteIndirect(pCommandSignature=#61(GCA_DispatchSignature), MaxCommandCount=1, pArgumentBuffer=#36(GCA_IndirectArgs), ArgumentBufferOffset=32, pCountBuffer=null, CountBufferOffset=0)
        255 Dispatch(ThreadGroupCountX=5, ThreadGroupCountY=1, ThreadGroupCountZ=1)
        257 BeginRenderPass(NumRenderTargets=1, pRenderTargets=[{cpuDescriptor={kind="RTV", view=null, resource=#30(GCA_ExtRT)}, BeginningAccess={Type=D3D12_RENDER_PASS_BEGINNING_ACCESS_TYPE_CLEAR, Clear={ClearValue={For …
        266 DrawInstanced(VertexCountPerInstance=3, InstanceCount=1, StartVertexLocation=0, StartInstanceLocation=0)
        268 ExecuteIndirect(pCommandSignature=#60(GCA_DrawSignature), MaxCommandCount=2, pArgumentBuffer=#36(GCA_IndirectArgs), ArgumentBufferOffset=0, pCountBuffer=null, CountBufferOffset=0)
          |   271 SetPipelineState(pPipelineState=#45(GCA_StreamPSO))
          |   272 IASetPrimitiveTopology(PrimitiveTopology="D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST")
          |   273 SetGraphicsRoot32BitConstants(RootParameterIndex=0, Num32BitValuesToSet=4, pSrcData=<16 bytes>, DestOffsetIn32BitValues=0)
          |   274 DrawInstanced(VertexCountPerInstance=3, InstanceCount=2, StartVertexLocation=0, StartInstanceLocation=0)
        278 DispatchMesh(ThreadGroupCountX=1, ThreadGroupCountY=1, ThreadGroupCountZ=1)
        280 DispatchMesh(ThreadGroupCountX=2, ThreadGroupCountY=1, ThreadGroupCountZ=1)
        281 EndRenderPass()
      * GCA_Pix3Marker_v0
    290 Close()
=== ExecuteCommandLists seq=299 queue=#1(GCA_DirectQueue) list=#71 (3 commands)
    298 Close()
```

## wpix objects（抜粋）

```
#4    ID3D12CommandQueue           GCA_CopyQueue                   
#8    ID3D12Resource               GCA_CopyQueueDst                 BUFFER 4096x1 fmt=DXGI_FORMAT_UNKNOWN mips=1 heap=D3D12_HEAP_TYPE_DEFAULT
#10   ID3D12CommandQueue           GCA_ComputeQueue                
#18   ID3D12Resource               GCA_ReservedBuffer               BUFFER 131072x1 fmt=DXGI_FORMAT_UNKNOWN mips=1 reserved
#30   ID3D12Resource               GCA_ExtRT                        TEXTURE2D 64x64 fmt=DXGI_FORMAT_R8G8B8A8_UNORM mips=1 heap=D3D12_HEAP_TYPE_DEFAULT
#32   ID3D12Resource               GCA_TLAS                         BUFFER 1424x1 fmt=DXGI_FORMAT_UNKNOWN mips=1 heap=D3D12_HEAP_TYPE_DEFAULT
#33   ID3D12Resource               GCA_BLAS                         BUFFER 720x1 fmt=DXGI_FORMAT_UNKNOWN mips=1 heap=D3D12_HEAP_TYPE_DEFAULT
#34   ID3D12Resource               GCA_EBTexture                    TEXTURE2D 16x16 fmt=DXGI_FORMAT_R8G8B8A8_UNORM mips=1 heap=D3D12_HEAP_TYPE_DEFAULT
#35   ID3D12Resource               GCA_EBTexture2                   TEXTURE2D 8x8 fmt=DXGI_FORMAT_R8G8B8A8_UNORM mips=2 heap=D3D12_HEAP_TYPE_DEFAULT
#38   ID3D12Resource               GCA_PlacedBuffer                 BUFFER 4096x1 fmt=DXGI_FORMAT_UNKNOWN mips=1 placed=#37(GCA_PlacedHeap)+0x10000
#45   ID3D12PipelineState          GCA_StreamPSO                   
#47   ID3D12PipelineState          GCA_MeshPSO                     
#53   ID3D12DescriptorHeap         GCA_ExtHeap                     
#59   ID3D12QueryHeap              GCA_TimestampHeap               
#60   ID3D12CommandSignature       GCA_DrawSignature               
#62   ID3D12StateObject            GCA_RTPSO                       
#67   ID3D12Resource               GCA_TLASScratch                  BUFFER 860x1 fmt=DXGI_FORMAT_UNKNOWN mips=1 heap=D3D12_HEAP_TYPE_DEFAULT
#70   ID3D12CommandAllocator       GCA_BundleAllocator
```

## wpix draws（抜粋）

```
[42] Dispatch(ThreadGroupCountX=4, ThreadGroupCountY=1, ThreadGroupCountZ=1)  queue=#10(GCA_ComputeQueue) list=#11
    PSO=#13(GCA_ExtComputePSO)  RootSig=#14(GCA_ExtComputeRootSig)
    root[0] UAV       vis=ALL u0 = #15(GCA_AsyncUav)+256
    root[1] constants vis=ALL b0 = 0000803f0000003f0000803e0000803f
[198] Dispatch(ThreadGroupCountX=7, ThreadGroupCountY=3, ThreadGroupCountZ=1)  queue=#1(GCA_DirectQueue) list=#20
    PSO=#41(GCA_ComputePSO)  RootSig=#42(GCA_ComputeRootSig)
    root[0] constants vis=ALL b0 = 0000dec04433221188776655ccbbaa99
    root[1] table     vis=ALL
        u0    heap[1] UAV   #23(GCA_UavBuffer)
[226] DrawInstanced(VertexCountPerInstance=3, InstanceCount=1, StartVertexLocation=0, StartInstanceLocation=0)  queue=#1(GCA_DirectQueue) list=#20
    PSO=#43(GCA_GraphicsPSO)  RootSig=#44(GCA_GraphicsRootSig)
    root[0] CBV       vis=ALL b0 = #52(GCA_ConstantBuffer)+0
    root[1] constants vis=PIXEL b1 = 0000dec04433221188776655ccbbaa99
    root[2] table     vis=PIXEL
        t0    heap[0] SRV   #26(GCA_Texture8x8)
    IA: D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST VB=[(0, #63(GCA_VertexBuffer), 36)] IB=#64(GCA_IndexBuffer)
    OM: RTV=[#22(GCA_BackBuffer0)] DSV=#27(GCA_Depth)
[227] DrawIndexedInstanced(IndexCountPerInstance=6, InstanceCount=1, StartIndexLocation=0, BaseVertexLocation=0, StartInstanceLocation=0)  queue=#1(GCA_DirectQueue) list=#20
    PSO=#43(GCA_GraphicsPSO)  RootSig=#44(GCA_GraphicsRootSig)
    root[0] CBV       vis=ALL b0 = #52(GCA_ConstantBuffer)+0
    root[1] constants vis=PIXEL b1 = 0000dec04433221188776655ccbbaa99
    root[2] table     vis=PIXEL
        t0    heap[0] SRV   #26(GCA_Texture8x8)
    IA: D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST VB=[(0, #63(GCA_VertexBuffer), 36)] IB=#64(GCA_IndexBuffer)
    OM: RTV=[#22(GCA_BackBuffer0)] DSV=#27(GCA_Depth)
[246] DispatchRays(pDesc={RayGenerationShaderRecord={StartAddress=GPUVA(#68(GCA_ShaderTable)+0), SizeInBytes=64}, MissShaderTable={StartAddress=GPUVA(#68(GCA_ShaderTable)+64), SizeInBytes=64, StrideInBytes=64}, HitGroupT …
    PSO=#62(GCA_RTPSO)  RootSig=#50(GCA_RTGlobalRootSig)
    shader tables: ['RayGen', 'Miss', 'GCA_HitGroup', 'GCA_HitGroup']
    root[0] table     vis=ALL
        u0    heap[5] UAV   #57(GCA_RTOutput)
    root[1] SRV       vis=ALL t0 = #32(GCA_TLAS)+0
    root[2] constants vis=ALL b0 = 0000803e0000003e0000003f0000803f
[252] ExecuteIndirect(pCommandSignature=#61(GCA_DispatchSignature), MaxCommandCount=1, pArgumentBuffer=#36(GCA_IndirectArgs), ArgumentBufferOffset=32, pCountBuffer=null, CountBufferOffset=0)  queue=#1(GCA_DirectQueue) li …
    PSO=#13(GCA_ExtComputePSO)  RootSig=#14(GCA_ExtComputeRootSig)
    indirect: [[{type=D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH, args={ThreadGroupCountX=2, ThreadGroupCountY=1, ThreadGroupCountZ=1}}]]
    root[0] UAV       vis=ALL u0 = #65(GCA_IndirectUav)+0
    root[1] constants vis=ALL b0 = 0000803f0000003f0000803e0000803f
[255] Dispatch(ThreadGroupCountX=5, ThreadGroupCountY=1, ThreadGroupCountZ=1)  queue=#1(GCA_DirectQueue) list=#20
    PSO=#49(GCA_StreamComputePSO)  RootSig=#14(GCA_ExtComputeRootSig)
    root[0] UAV       vis=ALL u0 = #65(GCA_IndirectUav)+0
    root[1] constants vis=ALL b0 = 0000803f0000003f0000803e0000803f
[266] DrawInstanced(VertexCountPerInstance=3, InstanceCount=1, StartVertexLocation=0, StartInstanceLocation=0)  queue=#1(GCA_DirectQueue) list=#20
    PSO=#45(GCA_StreamPSO)  RootSig=#46(GCA_ExtGraphicsRootSig)
    root[0] constants vis=ALL b0 = 0000803f0000003f0000803e0000803f
    root[1] table     vis=PIXEL
        t0    heap[3] SRV   #55(GCA_ExtTexB)
        t1    heap[4] SRV   #56(GCA_ExtTexC)
    IA: D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST VB=[(0, #63(GCA_VertexBuffer), 36)] IB=#64(GCA_IndexBuffer)
    OM: RTV=[#30(GCA_ExtRT)] DSV=#31(GCA_ExtDepth)  (render pass)
[268] ExecuteIndirect(pCommandSignature=#60(GCA_DrawSignature), MaxCommandCount=2, pArgumentBuffer=#36(GCA_IndirectArgs), ArgumentBufferOffset=0, pCountBuffer=null, CountBufferOffset=0)  queue=#1(GCA_DirectQueue) list=#2 …
    PSO=#45(GCA_StreamPSO)  RootSig=#46(GCA_ExtGraphicsRootSig)
    indirect: [[{type=D3D12_INDIRECT_ARGUMENT_TYPE_DRAW, args={VertexCountPerInstance=3, InstanceCount=1, StartVertexLocation=0, StartInstanceLocation=0}}], [{type=D3D12_INDIRECT_ARGUMENT_TYPE_DRAW, args={VertexCountPerI …
    root[0] constants vis=ALL b0 = 0000803f0000003f0000803e0000803f
    root[1] table     vis=PIXEL
        t0    heap[3] SRV   #55(GCA_ExtTexB)
        t1    heap[4] SRV   #56(GCA_ExtTexC)
    IA: D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST VB=[(0, #63(GCA_VertexBuffer), 36)] IB=#64(GCA_IndexBuffer)
    OM: RTV=[#30(GCA_ExtRT)] DSV=#31(GCA_ExtDepth)  (render pass)
[274] DrawInstanced(VertexCountPerInstance=3, InstanceCount=2, StartVertexLocation=0, StartInstanceLocation=0)  queue=#1(GCA_DirectQueue) list=#20 bundle=#69
    PSO=#45(GCA_StreamPSO)  RootSig=#46(GCA_ExtGraphicsRootSig)
    root[0] constants vis=ALL b0 = 000000000000803f000000000000803f
    root[1] table     vis=PIXEL
        t0    heap[3] SRV   #55(GCA_ExtTexB)
        t1    heap[4] SRV   #56(GCA_ExtTexC)
    IA: D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST VB=[(0, #63(GCA_VertexBuffer), 36)] IB=#64(GCA_IndexBuffer)
    OM: RTV=[#30(GCA_ExtRT)] DSV=#31(GCA_ExtDepth)  (render pass)
[278] DispatchMesh(ThreadGroupCountX=1, ThreadGroupCountY=1, ThreadGroupCountZ=1)  queue=#1(GCA_DirectQueue) list=#20
    PSO=#47(GCA_MeshPSO)  RootSig=#46(GCA_ExtGraphicsRootSig)
    root[0] constants vis=ALL b0 = 000000000000803f000000000000803f
    root[1] table     vis=PIXEL
        t0    heap[3] SRV   #55(GCA_ExtTexB)
        t1    heap[4] SRV   #56(GCA_ExtTexC)
    IA: D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST VB=[(0, #63(GCA_VertexBuffer), 36)] IB=#64(GCA_IndexBuffer)
    OM: RTV=[#30(GCA_ExtRT)] DSV=#31(GCA_ExtDepth)  (render pass)
[280] DispatchMesh(ThreadGroupCountX=2, ThreadGroupCountY=1, ThreadGroupCountZ=1)  queue=#1(GCA_DirectQueue) list=#20
    PSO=#48(GCA_AmpMeshPSO)  RootSig=#46(GCA_ExtGraphicsRootSig)
    root[0] constants vis=ALL b0 = 000000000000803f000000000000803f
    root[1] table     vis=PIXEL
        t0    heap[3] SRV   #55(GCA_ExtTexB)
        t1    heap[4] SRV   #56(GCA_ExtTexC)
    IA: D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST VB=[(0, #63(GCA_VertexBuffer), 36)] IB=#64(GCA_IndexBuffer)
    OM: RTV=[#30(GCA_ExtRT)] DSV=#31(GCA_ExtDepth)  (render pass)
```

`--json` の 1 件（DispatchRays）:

```json
{
 "seq": 246,
 "queue": "#1(GCA_DirectQueue)",
 "commandList": "#20",
 "bundle": null,
 "call": "DispatchRays",
 "args": {
  "pDesc": {
   "RayGenerationShaderRecord": {
    "StartAddress": {
     "resource": 68,
     "offset": 0
    },
    "SizeInBytes": 64
   },
   "MissShaderTable": {
    "StartAddress": {
     "resource": 68,
     "offset": 64
    },
    "SizeInBytes": 64,
    "StrideInBytes": 64
   },
   "HitGroupTable": {
    "StartAddress": {
     "resource": 68,
     "offset": 128
    },
    "SizeInBytes": 64,
    "StrideInBytes": 64
   },
   "CallableShaderTable": {
    "StartAddress": {
     "resource": 0,
     "offset": 0
    },
    "SizeInBytes": 0,
    "StrideInBytes": 0
   },
   "Width": 16,
   "Height": 8,
   "Depth": 1
  }
 },
 "pso": "#62(GCA_RTPSO)",
 "rootSignature": "#50(GCA_RTGlobalRootSig)",
 "bindings": [
  {
   "rootParam": 0,
   "type": "table",
   "visibility": "ALL",
   "descriptors": [
    {
     "register": "u0",
     "space": 0,
     "heapSlot": 5,
     "kind": "UAV",
     "resource": "#57(GCA_RTOutput)",
     "view": null
    }
   ]
  },
  {
   "rootParam": 1,
   "type": "SRV",
   "visibility": "ALL",
   "register": "t0",
   "gpuva": {
    "resource": "#32(GCA_TLAS)",
    "offset": 0
   }
  },
  {
   "rootParam": 2,
   "type": "constants",
   "visibility": "ALL",
   "register": "b0",
   "values": "0000803e0000003e0000003f0000803f"
  }
 ],
 "shaderTableExports": [
  "RayGen",
  "Miss",
  "GCA_HitGroup",
  "GCA_HitGroup"
 ]
}
```

## wpix extract

```
extracted 31 resources, 18 shader blobs, 1 blobs -> out
```

```
blob_0_7c29896a.png
extract.json
resources/0007_GCA_ExtUpload.bin
resources/0008_GCA_CopyQueueDst.bin
resources/0015_GCA_AsyncUav.bin
resources/0018_GCA_ReservedBuffer.bin
resources/0022_GCA_BackBuffer0.bin
resources/0022_GCA_BackBuffer0.png
resources/0023_GCA_UavBuffer.bin
resources/0024_GCA_CopySource.bin
resources/0025_GCA_CopyDest.bin
resources/0026_GCA_Texture8x8.bin
resources/0026_GCA_Texture8x8.png
resources/0027_GCA_Depth.bin
resources/0028_GCA_OffscreenRT.bin
resources/0029_GCA_TextureCopy.bin
resources/0029_GCA_TextureCopy.png
resources/0030_GCA_ExtRT.bin
resources/0030_GCA_ExtRT.png
resources/0031_GCA_ExtDepth.bin
resources/0034_GCA_EBTexture.bin
resources/0034_GCA_EBTexture.png
resources/0035_GCA_EBTexture2.bin
resources/0035_GCA_EBTexture2.png
resources/0036_GCA_IndirectArgs.bin
resources/0038_GCA_PlacedBuffer.bin
resources/0039_GCA_EBBuffer.bin
resources/0040_GCA_QueryReadback.bin
resources/0052_GCA_ConstantBuffer.bin
resources/0054_GCA_ExtTexA.bin
resources/0054_GCA_ExtTexA.png
resources/0055_GCA_ExtTexB.bin
resources/0055_GCA_ExtTexB.png
resources/0056_GCA_ExtTexC.bin
resources/0056_GCA_ExtTexC.png
resources/0057_GCA_RTOutput.bin
resources/0057_GCA_RTOutput.png
resources/0063_GCA_VertexBuffer.bin
resources/0064_GCA_IndexBuffer.bin
resources/0065_GCA_IndirectUav.bin
resources/0066_GCA_RTInstances.bin
resources/0067_GCA_TLASScratch.bin
resources/0068_GCA_ShaderTable.bin
shaders/0013_GCA_ExtComputePSO_CS.dxil
shaders/0014_GCA_ExtComputeRootSig.rootsig
shaders/0041_GCA_ComputePSO_CS.dxil
shaders/0042_GCA_ComputeRootSig.rootsig
shaders/0043_GCA_GraphicsPSO_PS.dxil
shaders/0043_GCA_GraphicsPSO_VS.dxil
shaders/0044_GCA_GraphicsRootSig.rootsig
shaders/0045_GCA_StreamPSO_PS.dxil
shaders/0045_GCA_StreamPSO_VS.dxil
shaders/0046_GCA_ExtGraphicsRootSig.rootsig
shaders/0047_GCA_MeshPSO_MS.dxil
shaders/0047_GCA_MeshPSO_PS.dxil
shaders/0048_GCA_AmpMeshPSO_AS.dxil
shaders/0048_GCA_AmpMeshPSO_MS.dxil
shaders/0048_GCA_AmpMeshPSO_PS.dxil
shaders/0049_GCA_StreamComputePSO_CS.dxil
shaders/0050_GCA_RTGlobalRootSig.rootsig
shaders/0062_GCA_RTPSO_LIB0.dxil
```

## wpix shaders

```
shaders: 10/10 resolved  (dxcompiler: C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\dxcompiler.dll)
  000_CS_GCA_ExtComputePSO.dxil            3fff508cce445d7f99d19f2de52d373c.pdb     #13(GCA_ExtComputePSO):CS,#49(GCA_StreamComputePSO):CS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\3fff508cce445d7f99d19f2de52d373c.pdb
  001_CS_GCA_ComputePSO.dxil               f226539fe71dafaddfd26f796e7621f2.pdb     #41(GCA_ComputePSO):CS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\f226539fe71dafaddfd26f796e7621f2.pdb
  002_VS_GCA_GraphicsPSO.dxil              712e67559fa08526e62ef0252ce542bf.pdb     #43(GCA_GraphicsPSO):VS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\712e67559fa08526e62ef0252ce542bf.pdb
  003_PS_GCA_GraphicsPSO.dxil              a568276b7d26c0f76281c683107e72c0.pdb     #43(GCA_GraphicsPSO):PS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\a568276b7d26c0f76281c683107e72c0.pdb
  004_VS_GCA_StreamPSO.dxil                a2b0e03e9c8aeb41241ccb3f43cc50e6.pdb     #45(GCA_StreamPSO):VS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\a2b0e03e9c8aeb41241ccb3f43cc50e6.pdb
  005_PS_GCA_StreamPSO.dxil                a0bcc1a68e0176ba22015e44f347045e.pdb     #45(GCA_StreamPSO):PS,#47(GCA_MeshPSO):PS,#48(GCA_AmpMeshPSO):PS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\a0bcc1a68e0176ba22015e44f347045e.pdb
  006_MS_GCA_MeshPSO.dxil                  598f632863a1d55f64d1810f920cad28.pdb     #47(GCA_MeshPSO):MS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\598f632863a1d55f64d1810f920cad28.pdb
  007_AS_GCA_AmpMeshPSO.dxil               b7b2bdb97aad03dc72659def05e5498f.pdb     #48(GCA_AmpMeshPSO):AS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\b7b2bdb97aad03dc72659def05e5498f.pdb
  008_MS_GCA_AmpMeshPSO.dxil               be9f13401c3f2077437f06a0db9b4afa.pdb     #48(GCA_AmpMeshPSO):MS
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\be9f13401c3f2077437f06a0db9b4afa.pdb
  009_LIB_GCA_RTPSO.dxil                   4b4d770dd11ab5b39eedd1e95a535bca.pdb     #62(GCA_RTPSO):LIB
      PDB folder [hash(pdbutils)] <repo>\bin\shaders\pdb\4b4d770dd11ab5b39eedd1e95a535bca.pdb
  report: shaders.json  (files examined: 10)
```

```
000_CS_GCA_ExtComputePSO.dxil
000_CS_GCA_ExtComputePSO.pdb
000_CS_GCA_ExtComputePSO.src/ext.hlsl
001_CS_GCA_ComputePSO.dxil
001_CS_GCA_ComputePSO.pdb
001_CS_GCA_ComputePSO.src/compute.hlsl
002_VS_GCA_GraphicsPSO.dxil
002_VS_GCA_GraphicsPSO.pdb
002_VS_GCA_GraphicsPSO.src/graphics.hlsl
003_PS_GCA_GraphicsPSO.dxil
003_PS_GCA_GraphicsPSO.pdb
003_PS_GCA_GraphicsPSO.src/graphics.hlsl
004_VS_GCA_StreamPSO.dxil
004_VS_GCA_StreamPSO.pdb
004_VS_GCA_StreamPSO.src/ext.hlsl
005_PS_GCA_StreamPSO.dxil
005_PS_GCA_StreamPSO.pdb
005_PS_GCA_StreamPSO.src/ext.hlsl
006_MS_GCA_MeshPSO.dxil
006_MS_GCA_MeshPSO.pdb
006_MS_GCA_MeshPSO.src/ext.hlsl
007_AS_GCA_AmpMeshPSO.dxil
007_AS_GCA_AmpMeshPSO.pdb
007_AS_GCA_AmpMeshPSO.src/ext.hlsl
008_MS_GCA_AmpMeshPSO.dxil
008_MS_GCA_AmpMeshPSO.pdb
008_MS_GCA_AmpMeshPSO.src/ext.hlsl
009_LIB_GCA_RTPSO.dxil
009_LIB_GCA_RTPSO.pdb
009_LIB_GCA_RTPSO.src/rt.hlsl
shaders.json
```

`shaders.json` の 1 件:

```json
{
 "file": "000_CS_GCA_ExtComputePSO.dxil",
 "kind": "dxil",
 "hash": "3fff508cce445d7f99d19f2de52d373c",
 "debugName": "3fff508cce445d7f99d19f2de52d373c.pdb",
 "uses": [
  {
   "pso": "#13(GCA_ExtComputePSO)",
   "stage": "CS"
  },
  {
   "pso": "#49(GCA_StreamComputePSO)",
   "stage": "CS"
  }
 ],
 "resolved": true,
 "foundBy": "folder",
 "matchedBy": "hash(pdbutils)",
 "pdbSource": "<repo>\\bin\\shaders\\pdb\\3fff508cce445d7f99d19f2de52d373c.pdb",
 "pdb": "000_CS_GCA_ExtComputePSO.pdb",
 "entryPoint": "CSMain",
 "targetProfile": "cs_6_0",
 "mainFile": "<repo>\\samples\\DX12CaptureTarget\\shaders\\ext.hlsl",
 "compileArgs": [
  "-E",
  "CSMain",
  "-T",
  "cs_6_0",
  "-Zi",
  "-Qstrip_debug",
  "…"
 ],
 "sources": [
  {
   "name": "<repo>\\samples\\DX12CaptureTarget\\shaders\\ext.hlsl",
   "file": "000_CS_GCA_ExtComputePSO.src/ext.hlsl"
  }
 ]
}
```
