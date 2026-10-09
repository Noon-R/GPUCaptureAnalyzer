#pragma once
// --extras: キャプチャ形式解析のカバレッジ拡大用の追加機能群。
//   Placed / Reserved (tiled) リソース, Query, ExecuteIndirect (draw/dispatch), RenderPass, Bundle,
//   PipelineStateStream (CreatePipelineState), Mesh shader, Enhanced Barriers, DXR (BLAS/TLAS/StateObject/DispatchRays),
//   非同期 Copy / Compute キュー + Fence 同期, PIX3 形式マーカー, フレーム内のディスクリプタ書き換え
// 値は variant で決定的に変化し、manifest の "extras" に出力される。
#include <d3d12.h>
#include <wrl/client.h>

#include <cstdint>
#include <string>
#include <vector>

class Extras {
public:
    struct Supported {
        bool enhancedBarriers = false;
        bool meshShader = false;
        bool raytracing = false;
        bool tiledResources = false;
    };

    // initList は Reset 済み (開いた) コマンドリスト。初期化用 GPU 作業 (アップロード, BLAS ビルド) を記録する。
    // 呼び出し側が実行・完了待ちをする。
    void Init(ID3D12Device* device, ID3D12CommandQueue* directQueue, ID3D12GraphicsCommandList* initList,
              uint32_t variant, const std::wstring& shaderDir);
    // メインのコマンドリスト実行前に呼ぶ: Copy / Compute キューに投入し、direct キューに Wait を入れる
    void SubmitAsync(ID3D12CommandQueue* directQueue);
    // メインのコマンドリストに記録 (フレーム内のディスクリプタ書き換えもここで行う)
    void Record(ID3D12GraphicsCommandList* cl);
    // manifest の "extras" オブジェクト (JSON 文字列) を返す
    std::string ManifestJson() const;

private:
    template <class T> using ComPtr = Microsoft::WRL::ComPtr<T>;
    void CreateHeapsAndTextures(ID3D12GraphicsCommandList* initList);
    void CreatePipelines();
    void CreateIndirectAndQueries();
    void CreateTiledAndPlaced();
    void CreateEnhancedBarrierResources();
    void CreateRaytracing(ID3D12GraphicsCommandList* initList);
    void CreateAsyncQueues();
    void CreateBundle();
    ComPtr<ID3D12Resource> Buffer(uint64_t size, D3D12_HEAP_TYPE heap, D3D12_RESOURCE_STATES st,
                                  D3D12_RESOURCE_FLAGS flags, const wchar_t* name);
    D3D12_CPU_DESCRIPTOR_HANDLE Cpu(ID3D12DescriptorHeap* h, UINT i) const;
    D3D12_GPU_DESCRIPTOR_HANDLE Gpu(ID3D12DescriptorHeap* h, UINT i) const;

    uint32_t v_ = 0;
    std::wstring shaderDir_;
    Supported sup_;
    ComPtr<ID3D12Device> dev_;
    ComPtr<ID3D12Device2> dev2_;
    ComPtr<ID3D12Device5> dev5_;
    ComPtr<ID3D12Device10> dev10_;
    UINT srvStride_ = 0;

    // descriptors
    ComPtr<ID3D12DescriptorHeap> heap_, cpuHeap_, rtvHeap_, dsvHeap_;
    // resources
    ComPtr<ID3D12Resource> texA_, texB_, texC_, texUpload_, extRT_, extDepth_, upload_;
    ComPtr<ID3D12Resource> placedHeapRes_, placed_, reserved_, readback_, indirectArgs_;
    ComPtr<ID3D12Resource> asyncUav_, indirectUav_, copyQueueDst_, ebBuffer_, ebTexture_, ebTexture2_;
    ComPtr<ID3D12Heap> placedHeap_, tileHeap_;
    // pipelines
    ComPtr<ID3D12RootSignature> gfxRS_, csRS_, rtRS_;
    ComPtr<ID3D12PipelineState> streamPso_, meshPso_, ampPso_, csPso_, streamCsPso_;
    ComPtr<ID3D12CommandSignature> drawSig_, dispatchSig_;
    ComPtr<ID3D12QueryHeap> tsHeap_, occHeap_;
    // bundle
    ComPtr<ID3D12CommandAllocator> bundleAlloc_;
    ComPtr<ID3D12GraphicsCommandList> bundle_;
    // async queues
    ComPtr<ID3D12CommandQueue> copyQueue_, computeQueue_;
    ComPtr<ID3D12CommandAllocator> copyAlloc_, computeAlloc_;
    ComPtr<ID3D12GraphicsCommandList> copyList_, computeList_;
    ComPtr<ID3D12Fence> copyFence_, computeFence_;
    uint64_t asyncValue_ = 0;
    ComPtr<ID3D12CommandQueue> directQueue_;
    HANDLE asyncEvent_ = nullptr;
    // raytracing
    ComPtr<ID3D12StateObject> rtpso_;
    ComPtr<ID3D12Resource> rtVerts_, blas_, blasScratch_, tlas_, tlasScratch_, instances_, shaderTable_, rtOutput_;

    // 既知の値
    float extClear_[4]{};
    float extDepthClear_ = 0;
    float drawColor_[4]{};
    uint32_t placedCopyBytes_ = 0;
    uint32_t uploadMagic_ = 0;
    uint32_t indirectDraw_[2][4]{};
    uint32_t indirectDispatch_[3]{};
    uint32_t bundleInstances_ = 0;
    uint32_t meshGroups_ = 0;
    uint32_t ampGroups_ = 0;
    uint32_t streamCsGroups_ = 0;
    uint32_t raysW_ = 0, raysH_ = 0;
    uint32_t tlasInstanceId_ = 0;
    float missColor_[4]{};
    uint32_t asyncDispatchX_ = 0;
    std::string pix3Begin_, pix3Marker_;
    std::wstring pix3MarkerW_;
};
