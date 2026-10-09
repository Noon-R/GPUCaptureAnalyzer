// DX12CaptureTarget
// GPUキャプチャファイル(.wpix 等)のバイナリ構造を解析するための「既知の値」を埋め込んだ DX12 アプリ。
// - 全ての数値・文字列は variant 引数で決定的に変化する。異なる variant のキャプチャを diff することで
//   フィールド位置を特定する (known-plaintext / differential analysis)。
// - 埋め込んだ値は --manifest で JSON に出力し、解析ツール側の「正解データ」として使う。
//
// Usage:
//   DX12CaptureTarget.exe [--variant N] [--pix-capture out.wpix] [--capture-frame F] [--frames N]
//                         [--manifest out.json] [--no-pix] [--warp] [--dxil] [--extras] [--capture-count N]

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shellapi.h>
#include <d3d12.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>
#include <wrl/client.h>

#include "extras.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cmath>
#include <string>
#include <vector>
#include <memory>
#include <stdexcept>

using Microsoft::WRL::ComPtr;

#define CHECK(x)                                                                  \
    do {                                                                          \
        HRESULT hr__ = (x);                                                       \
        if (FAILED(hr__)) {                                                       \
            char buf__[512];                                                      \
            sprintf_s(buf__, "%s failed: hr=0x%08X (%s:%d)", #x, (unsigned)hr__,  \
                      __FILE__, __LINE__);                                        \
            throw std::runtime_error(buf__);                                      \
        }                                                                         \
    } while (0)

// ---------------------------------------------------------------------------------------------
// 設定
// ---------------------------------------------------------------------------------------------
struct Options {
    uint32_t variant = 0;
    std::wstring pixCapturePath;   // 空ならキャプチャしない
    std::wstring manifestPath;
    uint32_t captureFrame = 5;     // このフレームの直前に CaptureNextFrame を呼ぶ
    uint32_t totalFrames = 0;      // 0 = captureFrame + 10
    bool loadPix = true;
    bool warp = false;
    bool dxil = false;             // dxc でビルドした DXIL (+分離 PDB) を使う
    bool extras = false;           // 追加機能群 (extras.h) を有効にする
    uint32_t captureCount = 1;     // CaptureNextFrame でキャプチャするフレーム数
};

static constexpr UINT kWidth = 320;
static constexpr UINT kHeight = 240;
static constexpr UINT kFrameCount = 2;
static constexpr UINT kComputeElements = 64;

// ---------------------------------------------------------------------------------------------
// Variant 依存の「既知の値」
// ---------------------------------------------------------------------------------------------
struct KnownValues {
    float clearColor[4];
    uint32_t rootConstants[4];     // root 32bit constants (b1)
    uint32_t cbMagic[4];           // constant buffer 先頭の magic
    float cbFloats[4];
    uint32_t drawVertexCount;
    uint32_t drawInstanceCount;
    uint32_t indexCount;
    uint32_t dispatchX, dispatchY, dispatchZ;
    uint32_t copyBytes;
    float viewport[4];             // x, y, w, h
    D3D12_RECT scissor;
    uint32_t textureMagic;         // テクスチャの texel パターンの基準値
    uint32_t uploadMagic;          // copy 元 buffer に詰める magic
    wchar_t markerFrame[64];
    wchar_t markerDraw[64];
    wchar_t markerCompute[64];
    wchar_t markerSetMarker[64];
    // 拡張シナリオ (ディスクリプタ/深度/ClearValue/ミップ)
    uint32_t srvHeapSize;          // CBV_SRV_UAV ヒープのディスクリプタ数
    float depthClear;              // DSV クリア値 / optimized clear value
    uint8_t stencilClear;
    float rtClearColor[4];         // オフスクリーン RT の optimized clear value & クリア色
    uint32_t uavClear[4];          // ClearUnorderedAccessViewUint の値
    uint32_t stencilRef;
    float blendFactor[4];
};

static KnownValues MakeKnownValues(uint32_t v)
{
    KnownValues k{};
    k.clearColor[0] = 0.125f + 0.0625f * (v % 4);
    k.clearColor[1] = 0.25f;
    k.clearColor[2] = 0.375f;
    k.clearColor[3] = 1.0f;
    k.rootConstants[0] = 0xC0DE0000u | v;
    k.rootConstants[1] = 0x11223344u;
    k.rootConstants[2] = 0x55667788u + v;
    k.rootConstants[3] = 0x99AABBCCu;
    k.cbMagic[0] = 0xDEADBEEFu;
    k.cbMagic[1] = 0xCAFEBABEu;
    k.cbMagic[2] = 0x13579BDFu + v;
    k.cbMagic[3] = 0x2468ACE0u;
    k.cbFloats[0] = 1.2345f;
    k.cbFloats[1] = 6.789f + (float)v;
    k.cbFloats[2] = -3.5f;
    k.cbFloats[3] = 1024.0f;
    k.drawVertexCount = 3 + 3 * (v % 3);    // 3, 6, 9
    k.drawInstanceCount = 1 + (v % 2);
    k.indexCount = 6;
    k.dispatchX = 7 + v;
    k.dispatchY = 3;
    k.dispatchZ = 1;
    k.copyBytes = 256 + 16 * v;
    k.viewport[0] = 0.0f;
    k.viewport[1] = 0.0f;
    k.viewport[2] = (float)kWidth - (float)v;
    k.viewport[3] = (float)kHeight;
    k.scissor = {1, 2, (LONG)kWidth - 3, (LONG)kHeight - 4 - (LONG)v};
    k.textureMagic = 0xA5000000u | (v << 8);
    k.uploadMagic = 0xFEED0000u | v;
    swprintf_s(k.markerFrame, L"GCA_Frame_v%u", v);
    swprintf_s(k.markerDraw, L"GCA_DrawPass_v%u", v);
    swprintf_s(k.markerCompute, L"GCA_ComputePass_v%u", v);
    swprintf_s(k.markerSetMarker, L"GCA_SetMarker_v%u", v);
    k.srvHeapSize = 8 + v;
    k.depthClear = 0.75f + 0.0625f * (float)v;
    k.stencilClear = (uint8_t)(3 + v);
    k.rtClearColor[0] = 0.5f;
    k.rtClearColor[1] = 0.25f + 0.125f * (float)v;
    k.rtClearColor[2] = 0.125f;
    k.rtClearColor[3] = 1.0f;
    k.uavClear[0] = 0xAB000000u | v;
    k.uavClear[1] = 0x00CD0000u;
    k.uavClear[2] = 0x0000EF00u;
    k.uavClear[3] = 0x00000012u;
    k.stencilRef = 0x40 + v;
    k.blendFactor[0] = 0.1f;
    k.blendFactor[1] = 0.2f;
    k.blendFactor[2] = 0.3f + 0.1f * (float)v;
    k.blendFactor[3] = 0.4f;
    return k;
}

// ---------------------------------------------------------------------------------------------
// PIX 連携 (WinPixEventRuntime なしで実装)
// ---------------------------------------------------------------------------------------------
using PFN_CaptureNextFrame = HRESULT(WINAPI*)(PCWSTR fileName, UINT32 numFrames);
static HMODULE g_pixModule = nullptr;
static PFN_CaptureNextFrame g_captureNextFrame = nullptr;

static std::wstring FindLatestPixDir()
{
    std::wstring root = L"C:\\Program Files\\Microsoft PIX\\";
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW((root + L"*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return {};
    std::wstring best;
    do {
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && fd.cFileName[0] != L'.') {
            if (best.empty() || wcscmp(fd.cFileName, best.c_str()) > 0) best = fd.cFileName;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return best.empty() ? std::wstring() : root + best + L"\\";
}

static void LoadPixCapturer()
{
    // PIX から起動されている場合は既にロード済み
    g_pixModule = GetModuleHandleW(L"WinPixGpuCapturer.dll");
    if (!g_pixModule) {
        std::wstring dir = FindLatestPixDir();
        if (!dir.empty()) g_pixModule = LoadLibraryW((dir + L"WinPixGpuCapturer.dll").c_str());
    }
    if (g_pixModule) {
        g_captureNextFrame =
            reinterpret_cast<PFN_CaptureNextFrame>(GetProcAddress(g_pixModule, "CaptureNextFrame"));
    }
    wprintf(L"[pix] capturer %s\n", g_pixModule ? L"loaded" : L"NOT loaded");
}

// PIX イベント (WINPIX_EVENT_UNICODE_VERSION = 0 : data は null 終端 wchar_t 文字列)
static void PixBegin(ID3D12GraphicsCommandList* cl, const wchar_t* s)
{
    cl->BeginEvent(0, s, (UINT)((wcslen(s) + 1) * sizeof(wchar_t)));
}
static void PixEnd(ID3D12GraphicsCommandList* cl) { cl->EndEvent(); }
static void PixMarker(ID3D12GraphicsCommandList* cl, const wchar_t* s)
{
    cl->SetMarker(0, s, (UINT)((wcslen(s) + 1) * sizeof(wchar_t)));
}

// ---------------------------------------------------------------------------------------------
// シェーダ
// ---------------------------------------------------------------------------------------------


// シェーダは <exe>/shaders/ から読む (CMake がビルド時に配置)
//   既定 (DXBC): *.hlsl を実行時に D3DCompile (SM5.1)
//   --dxil     : dxc でビルド済みの *.cso (SM6.0, -Zi -Qstrip_debug, PDB は shaders/pdb/ に分離)
static std::wstring ShaderDir()
{
    wchar_t path[MAX_PATH];
    GetModuleFileNameW(nullptr, path, MAX_PATH);
    std::wstring s = path;
    return s.substr(0, s.find_last_of(L"\\/") + 1) + L"shaders\\";
}

static std::vector<char> ReadFileBytes(const std::wstring& path)
{
    std::vector<char> data;
    FILE* f = nullptr;
    if (_wfopen_s(&f, path.c_str(), L"rb") != 0 || !f) throw std::runtime_error("cannot open shader file");
    fseek(f, 0, SEEK_END);
    data.resize((size_t)ftell(f));
    fseek(f, 0, SEEK_SET);
    fread(data.data(), 1, data.size(), f);
    fclose(f);
    return data;
}

static ComPtr<ID3DBlob> LoadCso(const wchar_t* name)
{
    auto bytes = ReadFileBytes(ShaderDir() + name);
    ComPtr<ID3DBlob> blob;
    CHECK(D3DCreateBlob(bytes.size(), &blob));
    memcpy(blob->GetBufferPointer(), bytes.data(), bytes.size());
    return blob;
}

static ComPtr<ID3DBlob> Compile(const wchar_t* file, const char* entry, const char* target)
{
    auto src = ReadFileBytes(ShaderDir() + file);
    ComPtr<ID3DBlob> code, err;
    HRESULT hr = D3DCompile(src.data(), src.size(), "gca_shader.hlsl", nullptr, nullptr, entry, target,
                            D3DCOMPILE_ENABLE_STRICTNESS, 0, &code, &err);
    if (FAILED(hr)) {
        if (err) printf("%s\n", (const char*)err->GetBufferPointer());
        CHECK(hr);
    }
    return code;
}

// ---------------------------------------------------------------------------------------------
// アプリ本体
// ---------------------------------------------------------------------------------------------
struct Vertex {
    float pos[3];
    float col[4];
    float uv[2];
};

class App {
public:
    App(const Options& o) : opt_(o), kv_(MakeKnownValues(o.variant)) {}
    void Init(HWND hwnd);
    void RenderFrame(uint32_t frameIndex);
    void WaitIdle();
    void WriteManifest() const;

private:
    void CreateDevice();
    void CreateResources();
    void CreatePipelines();
    ComPtr<ID3D12Resource> CreateBuffer(UINT64 size, D3D12_HEAP_TYPE heap, D3D12_RESOURCE_STATES st,
                                        D3D12_RESOURCE_FLAGS flags, const wchar_t* name);
    void ExecuteAndWait();

    Options opt_;
    KnownValues kv_;
    HWND hwnd_ = nullptr;

    ComPtr<IDXGIFactory6> factory_;
    ComPtr<IDXGIAdapter1> adapter_;
    ComPtr<ID3D12Device> device_;
    ComPtr<ID3D12CommandQueue> queue_;
    ComPtr<IDXGISwapChain3> swapchain_;
    ComPtr<ID3D12DescriptorHeap> rtvHeap_, srvHeap_, dsvHeap_, samplerHeap_, cpuUavHeap_;
    UINT rtvStride_ = 0, srvStride_ = 0;
    ComPtr<ID3D12Resource> backBuffers_[kFrameCount];
    ComPtr<ID3D12CommandAllocator> allocators_[kFrameCount];
    ComPtr<ID3D12GraphicsCommandList> cmdList_;
    ComPtr<ID3D12Fence> fence_;
    UINT64 fenceValue_ = 0;
    HANDLE fenceEvent_ = nullptr;

    ComPtr<ID3D12RootSignature> gfxRootSig_, csRootSig_;
    ComPtr<ID3D12PipelineState> gfxPso_, csPso_;

    ComPtr<ID3D12Resource> vertexBuffer_, indexBuffer_, constantBuffer_, texture_;
    ComPtr<ID3D12Resource> uavBuffer_, copySrc_, copyDst_;
    ComPtr<ID3D12Resource> uploadScratch_;
    ComPtr<ID3D12Resource> depth_, offscreenRT_, textureCopy_;
    std::unique_ptr<Extras> extras_;
    D3D12_VERTEX_BUFFER_VIEW vbv_{};
    D3D12_INDEX_BUFFER_VIEW ibv_{};
};

static D3D12_HEAP_PROPERTIES HeapProps(D3D12_HEAP_TYPE t)
{
    D3D12_HEAP_PROPERTIES p{};
    p.Type = t;
    return p;
}

static D3D12_RESOURCE_BARRIER Transition(ID3D12Resource* r, D3D12_RESOURCE_STATES a, D3D12_RESOURCE_STATES b)
{
    D3D12_RESOURCE_BARRIER br{};
    br.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    br.Transition.pResource = r;
    br.Transition.StateBefore = a;
    br.Transition.StateAfter = b;
    br.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    return br;
}

void App::CreateDevice()
{
    UINT flags = 0;
    CHECK(CreateDXGIFactory2(flags, IID_PPV_ARGS(&factory_)));
    if (opt_.warp) {
        CHECK(factory_->EnumWarpAdapter(IID_PPV_ARGS(&adapter_)));
    } else {
        CHECK(factory_->EnumAdapterByGpuPreference(0, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE,
                                                   IID_PPV_ARGS(&adapter_)));
    }
    DXGI_ADAPTER_DESC1 ad{};
    adapter_->GetDesc1(&ad);
    wprintf(L"[d3d] adapter: %s\n", ad.Description);
    CHECK(D3D12CreateDevice(adapter_.Get(), D3D_FEATURE_LEVEL_12_0, IID_PPV_ARGS(&device_)));
    device_->SetName(L"GCA_Device");

    D3D12_COMMAND_QUEUE_DESC qd{};
    qd.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    CHECK(device_->CreateCommandQueue(&qd, IID_PPV_ARGS(&queue_)));
    queue_->SetName(L"GCA_DirectQueue");

    DXGI_SWAP_CHAIN_DESC1 sd{};
    sd.Width = kWidth;
    sd.Height = kHeight;
    sd.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = kFrameCount;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    ComPtr<IDXGISwapChain1> sc1;
    CHECK(factory_->CreateSwapChainForHwnd(queue_.Get(), hwnd_, &sd, nullptr, nullptr, &sc1));
    CHECK(sc1.As(&swapchain_));

    D3D12_DESCRIPTOR_HEAP_DESC hd{};
    hd.NumDescriptors = kFrameCount + 1;   // [kFrameCount] = オフスクリーン RT (mip1)
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    CHECK(device_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&rtvHeap_)));
    rtvHeap_->SetName(L"GCA_RtvHeap");
    rtvStride_ = device_->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV);

    hd.NumDescriptors = kv_.srvHeapSize;
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    CHECK(device_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&srvHeap_)));
    srvHeap_->SetName(L"GCA_SrvUavHeap");

    hd.NumDescriptors = 2;
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    CHECK(device_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&samplerHeap_)));
    samplerHeap_->SetName(L"GCA_SamplerHeap");

    hd.NumDescriptors = 1;
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_DSV;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_NONE;
    CHECK(device_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&dsvHeap_)));
    dsvHeap_->SetName(L"GCA_DsvHeap");

    hd.NumDescriptors = 1;
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_NONE;   // ClearUAV 用 CPU ヒープ
    CHECK(device_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&cpuUavHeap_)));
    cpuUavHeap_->SetName(L"GCA_CpuUavHeap");
    srvStride_ = device_->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);

    for (UINT i = 0; i < kFrameCount; ++i) {
        CHECK(swapchain_->GetBuffer(i, IID_PPV_ARGS(&backBuffers_[i])));
        wchar_t name[64];
        swprintf_s(name, L"GCA_BackBuffer%u", i);
        backBuffers_[i]->SetName(name);
        D3D12_CPU_DESCRIPTOR_HANDLE h = rtvHeap_->GetCPUDescriptorHandleForHeapStart();
        h.ptr += (SIZE_T)i * rtvStride_;
        device_->CreateRenderTargetView(backBuffers_[i].Get(), nullptr, h);
        CHECK(device_->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocators_[i])));
        swprintf_s(name, L"GCA_Allocator%u", i);
        allocators_[i]->SetName(name);
    }
    CHECK(device_->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocators_[0].Get(), nullptr,
                                     IID_PPV_ARGS(&cmdList_)));
    cmdList_->SetName(L"GCA_MainCommandList");
    CHECK(device_->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence_)));
    fence_->SetName(L"GCA_Fence");
    fenceEvent_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
}

ComPtr<ID3D12Resource> App::CreateBuffer(UINT64 size, D3D12_HEAP_TYPE heap, D3D12_RESOURCE_STATES st,
                                         D3D12_RESOURCE_FLAGS flags, const wchar_t* name)
{
    D3D12_RESOURCE_DESC rd{};
    rd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    rd.Width = size;
    rd.Height = 1;
    rd.DepthOrArraySize = 1;
    rd.MipLevels = 1;
    rd.SampleDesc.Count = 1;
    rd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    rd.Flags = flags;
    auto hp = HeapProps(heap);
    ComPtr<ID3D12Resource> r;
    CHECK(device_->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, st, nullptr, IID_PPV_ARGS(&r)));
    r->SetName(name);
    return r;
}

void App::ExecuteAndWait()
{
    CHECK(cmdList_->Close());
    ID3D12CommandList* lists[] = {cmdList_.Get()};
    queue_->ExecuteCommandLists(1, lists);
    WaitIdle();
}

void App::WaitIdle()
{
    ++fenceValue_;
    CHECK(queue_->Signal(fence_.Get(), fenceValue_));
    if (fence_->GetCompletedValue() < fenceValue_) {
        CHECK(fence_->SetEventOnCompletion(fenceValue_, fenceEvent_));
        WaitForSingleObject(fenceEvent_, INFINITE);
    }
}

void App::CreateResources()
{
    // --- 頂点バッファ (upload heap) : 9 頂点。座標/色は既知の値 ---
    std::vector<Vertex> verts;
    for (int i = 0; i < 9; ++i) {
        float a = 6.2831853f * (float)i / 9.0f;
        Vertex vtx{};
        vtx.pos[0] = 0.5f * cosf(a);
        vtx.pos[1] = 0.5f * sinf(a);
        vtx.pos[2] = 0.25f + 0.01f * (float)opt_.variant;
        vtx.col[0] = (i % 3 == 0) ? 1.0f : 0.0f;
        vtx.col[1] = (i % 3 == 1) ? 1.0f : 0.0f;
        vtx.col[2] = (i % 3 == 2) ? 1.0f : 0.0f;
        vtx.col[3] = 1.0f;
        vtx.uv[0] = (float)i / 8.0f;
        vtx.uv[1] = 1.0f - (float)i / 8.0f;
        verts.push_back(vtx);
    }
    UINT vbSize = (UINT)(verts.size() * sizeof(Vertex));
    vertexBuffer_ = CreateBuffer(vbSize, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ,
                                 D3D12_RESOURCE_FLAG_NONE, L"GCA_VertexBuffer");
    void* p = nullptr;
    CHECK(vertexBuffer_->Map(0, nullptr, &p));
    memcpy(p, verts.data(), vbSize);
    vertexBuffer_->Unmap(0, nullptr);
    vbv_.BufferLocation = vertexBuffer_->GetGPUVirtualAddress();
    vbv_.SizeInBytes = vbSize;
    vbv_.StrideInBytes = sizeof(Vertex);

    // --- インデックスバッファ ---
    const uint16_t indices[6] = {0, 1, 2, 0, 2, (uint16_t)(3 + opt_.variant % 5)};
    indexBuffer_ = CreateBuffer(sizeof(indices), D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ,
                                D3D12_RESOURCE_FLAG_NONE, L"GCA_IndexBuffer");
    CHECK(indexBuffer_->Map(0, nullptr, &p));
    memcpy(p, indices, sizeof(indices));
    indexBuffer_->Unmap(0, nullptr);
    ibv_.BufferLocation = indexBuffer_->GetGPUVirtualAddress();
    ibv_.SizeInBytes = sizeof(indices);
    ibv_.Format = DXGI_FORMAT_R16_UINT;

    // --- 定数バッファ (256B) ---
    constantBuffer_ = CreateBuffer(256, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ,
                                   D3D12_RESOURCE_FLAG_NONE, L"GCA_ConstantBuffer");
    CHECK(constantBuffer_->Map(0, nullptr, &p));
    uint8_t cb[256] = {};
    memcpy(cb, kv_.cbMagic, 16);
    memcpy(cb + 16, kv_.cbFloats, 16);
    for (int i = 32; i < 256; ++i) cb[i] = (uint8_t)(i ^ 0x5A);
    memcpy(p, cb, sizeof(cb));
    constantBuffer_->Unmap(0, nullptr);

    // --- テクスチャ 8x8 RGBA8 (default heap, upload 経由) ---
    D3D12_RESOURCE_DESC td{};
    td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    td.Width = 8;
    td.Height = 8;
    td.DepthOrArraySize = 1;
    td.MipLevels = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    auto hpDefault = HeapProps(D3D12_HEAP_TYPE_DEFAULT);
    CHECK(device_->CreateCommittedResource(&hpDefault, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_COPY_DEST,
                                           nullptr, IID_PPV_ARGS(&texture_)));
    texture_->SetName(L"GCA_Texture8x8");

    D3D12_PLACED_SUBRESOURCE_FOOTPRINT fp{};
    UINT64 total = 0;
    device_->GetCopyableFootprints(&td, 0, 1, 0, &fp, nullptr, nullptr, &total);
    uploadScratch_ = CreateBuffer(total, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ,
                                  D3D12_RESOURCE_FLAG_NONE, L"GCA_TextureUpload");
    CHECK(uploadScratch_->Map(0, nullptr, &p));
    for (UINT y = 0; y < 8; ++y) {
        uint32_t* row = (uint32_t*)((uint8_t*)p + fp.Offset + (UINT64)y * fp.Footprint.RowPitch);
        for (UINT x = 0; x < 8; ++x) row[x] = kv_.textureMagic | (y << 4) | x;
    }
    uploadScratch_->Unmap(0, nullptr);

    // --- UAV / copy 用バッファ ---
    uavBuffer_ = CreateBuffer(kComputeElements * 4, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COMMON,
                              D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, L"GCA_UavBuffer");
    copySrc_ = CreateBuffer(4096, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ,
                            D3D12_RESOURCE_FLAG_NONE, L"GCA_CopySource");
    CHECK(copySrc_->Map(0, nullptr, &p));
    for (UINT i = 0; i < 1024; ++i) ((uint32_t*)p)[i] = kv_.uploadMagic + i;
    copySrc_->Unmap(0, nullptr);
    copyDst_ = CreateBuffer(4096, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COPY_DEST,
                            D3D12_RESOURCE_FLAG_NONE, L"GCA_CopyDest");

    // --- descriptors : [0]=SRV(texture) [1]=UAV(uavBuffer) ---
    D3D12_CPU_DESCRIPTOR_HANDLE h = srvHeap_->GetCPUDescriptorHandleForHeapStart();
    device_->CreateShaderResourceView(texture_.Get(), nullptr, h);
    h.ptr += srvStride_;
    D3D12_UNORDERED_ACCESS_VIEW_DESC ud{};
    ud.Format = DXGI_FORMAT_UNKNOWN;
    ud.ViewDimension = D3D12_UAV_DIMENSION_BUFFER;
    ud.Buffer.NumElements = kComputeElements;
    ud.Buffer.StructureByteStride = 4;
    device_->CreateUnorderedAccessView(uavBuffer_.Get(), nullptr, &ud, h);

    // [2] CBV
    h.ptr += srvStride_;
    D3D12_CONSTANT_BUFFER_VIEW_DESC cbvd{constantBuffer_->GetGPUVirtualAddress(), 256};
    device_->CreateConstantBufferView(&cbvd, h);
    // [3] SRV: structured buffer (明示 desc)
    h.ptr += srvStride_;
    D3D12_SHADER_RESOURCE_VIEW_DESC sd{};
    sd.Format = DXGI_FORMAT_UNKNOWN;
    sd.ViewDimension = D3D12_SRV_DIMENSION_BUFFER;
    sd.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    sd.Buffer.FirstElement = 2;
    sd.Buffer.NumElements = 60;
    sd.Buffer.StructureByteStride = 4;
    device_->CreateShaderResourceView(uavBuffer_.Get(), &sd, h);
    // [4] SRV: texture (明示 desc)
    h.ptr += srvStride_;
    sd = {};
    sd.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    sd.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    sd.Texture2D.MipLevels = 1;
    device_->CreateShaderResourceView(texture_.Get(), &sd, h);
    // [5] UAV: typed R32_UINT (ClearUnorderedAccessViewUint 用 GPU ハンドル) + CPU ヒープにも同じもの
    h.ptr += srvStride_;
    D3D12_UNORDERED_ACCESS_VIEW_DESC tud{};
    tud.Format = DXGI_FORMAT_R32_UINT;
    tud.ViewDimension = D3D12_UAV_DIMENSION_BUFFER;
    tud.Buffer.NumElements = kComputeElements;
    device_->CreateUnorderedAccessView(uavBuffer_.Get(), nullptr, &tud, h);
    device_->CreateUnorderedAccessView(uavBuffer_.Get(), nullptr, &tud,
                                       cpuUavHeap_->GetCPUDescriptorHandleForHeapStart());
    // [srvHeapSize-1] 末尾スロットに SRV (間に未初期化スロットの「穴」を作る)
    {
        D3D12_CPU_DESCRIPTOR_HANDLE last = srvHeap_->GetCPUDescriptorHandleForHeapStart();
        last.ptr += (SIZE_T)(kv_.srvHeapSize - 1) * srvStride_;
        device_->CreateShaderResourceView(texture_.Get(), nullptr, last);
    }

    // --- samplers ---
    D3D12_SAMPLER_DESC smp{};
    smp.Filter = D3D12_FILTER_MIN_MAG_MIP_LINEAR;
    smp.AddressU = smp.AddressV = smp.AddressW = D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
    smp.MaxLOD = D3D12_FLOAT32_MAX;
    D3D12_CPU_DESCRIPTOR_HANDLE sh = samplerHeap_->GetCPUDescriptorHandleForHeapStart();
    device_->CreateSampler(&smp, sh);
    smp.Filter = D3D12_FILTER_ANISOTROPIC;
    smp.AddressU = smp.AddressV = smp.AddressW = D3D12_TEXTURE_ADDRESS_MODE_BORDER;
    smp.MaxAnisotropy = 8;
    smp.MipLODBias = 0.5f;
    smp.BorderColor[0] = 0.25f; smp.BorderColor[1] = 0.5f; smp.BorderColor[2] = 0.75f; smp.BorderColor[3] = 1.0f;
    smp.MinLOD = 1.0f;
    smp.MaxLOD = 7.0f;
    sh.ptr += device_->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER);
    device_->CreateSampler(&smp, sh);

    // --- depth (D24S8, optimized clear value) ---
    {
        D3D12_RESOURCE_DESC dd{};
        dd.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        dd.Width = kWidth;
        dd.Height = kHeight;
        dd.DepthOrArraySize = 1;
        dd.MipLevels = 1;
        dd.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        dd.SampleDesc.Count = 1;
        dd.Flags = D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL;
        D3D12_CLEAR_VALUE cv{};
        cv.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        cv.DepthStencil.Depth = kv_.depthClear;
        cv.DepthStencil.Stencil = kv_.stencilClear;
        CHECK(device_->CreateCommittedResource(&hpDefault, D3D12_HEAP_FLAG_NONE, &dd, D3D12_RESOURCE_STATE_DEPTH_WRITE,
                                               &cv, IID_PPV_ARGS(&depth_)));
        depth_->SetName(L"GCA_Depth");
        D3D12_DEPTH_STENCIL_VIEW_DESC dsvd{};
        dsvd.Format = DXGI_FORMAT_D24_UNORM_S8_UINT;
        dsvd.ViewDimension = D3D12_DSV_DIMENSION_TEXTURE2D;
        device_->CreateDepthStencilView(depth_.Get(), &dsvd, dsvHeap_->GetCPUDescriptorHandleForHeapStart());
    }
    // --- offscreen RT (RGBA16F 64x32, 3 mips, optimized clear value) ---
    {
        D3D12_RESOURCE_DESC rd{};
        rd.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        rd.Width = 64;
        rd.Height = 32;
        rd.DepthOrArraySize = 1;
        rd.MipLevels = 3;
        rd.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
        rd.SampleDesc.Count = 1;
        rd.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
        D3D12_CLEAR_VALUE cv{};
        cv.Format = rd.Format;
        memcpy(cv.Color, kv_.rtClearColor, sizeof(cv.Color));
        CHECK(device_->CreateCommittedResource(&hpDefault, D3D12_HEAP_FLAG_NONE, &rd,
                                               D3D12_RESOURCE_STATE_RENDER_TARGET, &cv, IID_PPV_ARGS(&offscreenRT_)));
        offscreenRT_->SetName(L"GCA_OffscreenRT");
        D3D12_RENDER_TARGET_VIEW_DESC rtvd{};
        rtvd.Format = rd.Format;
        rtvd.ViewDimension = D3D12_RTV_DIMENSION_TEXTURE2D;
        rtvd.Texture2D.MipSlice = 1;
        D3D12_CPU_DESCRIPTOR_HANDLE rh = rtvHeap_->GetCPUDescriptorHandleForHeapStart();
        rh.ptr += (SIZE_T)kFrameCount * rtvStride_;
        device_->CreateRenderTargetView(offscreenRT_.Get(), &rtvd, rh);
    }
    // --- CopyTextureRegion の宛先 ---
    CHECK(device_->CreateCommittedResource(&hpDefault, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_COPY_DEST,
                                           nullptr, IID_PPV_ARGS(&textureCopy_)));
    textureCopy_->SetName(L"GCA_TextureCopy");

    // テクスチャのアップロード (キャプチャ前に完了させる)
    D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
    dst.pResource = texture_.Get();
    dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    src.pResource = uploadScratch_.Get();
    src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    src.PlacedFootprint = fp;
    cmdList_->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    auto b = Transition(texture_.Get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    cmdList_->ResourceBarrier(1, &b);
    ExecuteAndWait();
}

void App::CreatePipelines()
{
    // --- Graphics root signature ---
    // [0] root CBV b0, [1] root constants b1 (4 x 32bit), [2] table: SRV t0 ; static sampler s0
    D3D12_DESCRIPTOR_RANGE srvRange{};
    srvRange.RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_SRV;
    srvRange.NumDescriptors = 1;
    srvRange.BaseShaderRegister = 0;
    srvRange.OffsetInDescriptorsFromTableStart = 0;

    D3D12_ROOT_PARAMETER params[3]{};
    params[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_CBV;
    params[0].Descriptor.ShaderRegister = 0;
    params[0].ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
    params[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    params[1].Constants.ShaderRegister = 1;
    params[1].Constants.Num32BitValues = 4;
    params[1].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    params[2].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[2].DescriptorTable.NumDescriptorRanges = 1;
    params[2].DescriptorTable.pDescriptorRanges = &srvRange;
    params[2].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;

    D3D12_STATIC_SAMPLER_DESC ss{};
    ss.Filter = D3D12_FILTER_MIN_MAG_MIP_POINT;
    ss.AddressU = ss.AddressV = ss.AddressW = D3D12_TEXTURE_ADDRESS_MODE_WRAP;
    ss.MaxLOD = D3D12_FLOAT32_MAX;
    ss.ShaderRegister = 0;
    ss.ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;

    D3D12_ROOT_SIGNATURE_DESC rsd{};
    rsd.NumParameters = 3;
    rsd.pParameters = params;
    rsd.NumStaticSamplers = 1;
    rsd.pStaticSamplers = &ss;
    rsd.Flags = D3D12_ROOT_SIGNATURE_FLAG_ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT;
    ComPtr<ID3DBlob> blob, err;
    CHECK(D3D12SerializeRootSignature(&rsd, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &err));
    CHECK(device_->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&gfxRootSig_)));
    gfxRootSig_->SetName(L"GCA_GraphicsRootSig");

    // --- Compute root signature: [0] root constants b0, [1] table UAV u0 ---
    D3D12_DESCRIPTOR_RANGE uavRange{};
    uavRange.RangeType = D3D12_DESCRIPTOR_RANGE_TYPE_UAV;
    uavRange.NumDescriptors = 1;
    D3D12_ROOT_PARAMETER cparams[2]{};
    cparams[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    cparams[0].Constants.ShaderRegister = 0;
    cparams[0].Constants.Num32BitValues = 4;
    cparams[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    cparams[1].DescriptorTable.NumDescriptorRanges = 1;
    cparams[1].DescriptorTable.pDescriptorRanges = &uavRange;
    D3D12_ROOT_SIGNATURE_DESC crsd{};
    crsd.NumParameters = 2;
    crsd.pParameters = cparams;
    CHECK(D3D12SerializeRootSignature(&crsd, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &err));
    CHECK(device_->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&csRootSig_)));
    csRootSig_->SetName(L"GCA_ComputeRootSig");

    // --- PSOs ---
    ComPtr<ID3DBlob> vs, ps, cs;
    if (opt_.dxil) {
        vs = LoadCso(L"vs.cso");
        ps = LoadCso(L"ps.cso");
        cs = LoadCso(L"cs.cso");
    } else {
        vs = Compile(L"graphics.hlsl", "VSMain", "vs_5_1");
        ps = Compile(L"graphics.hlsl", "PSMain", "ps_5_1");
        cs = Compile(L"compute.hlsl", "CSMain", "cs_5_1");
    }

    D3D12_INPUT_ELEMENT_DESC layout[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32B32_FLOAT, 0, 0, D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA, 0},
        {"COLOR", 0, DXGI_FORMAT_R32G32B32A32_FLOAT, 0, 12, D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 28, D3D12_INPUT_CLASSIFICATION_PER_VERTEX_DATA, 0},
    };
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd{};
    pd.pRootSignature = gfxRootSig_.Get();
    pd.VS = {vs->GetBufferPointer(), vs->GetBufferSize()};
    pd.PS = {ps->GetBufferPointer(), ps->GetBufferSize()};
    pd.BlendState.RenderTarget[0].RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
    pd.SampleMask = UINT_MAX;
    pd.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    pd.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    pd.RasterizerState.DepthClipEnable = TRUE;
    pd.InputLayout = {layout, _countof(layout)};
    pd.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pd.NumRenderTargets = 1;
    pd.RTVFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
    pd.DSVFormat = DXGI_FORMAT_D24_UNORM_S8_UINT;   // depth test は無効 (DepthStencilState = 0)
    pd.SampleDesc.Count = 1;
    CHECK(device_->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&gfxPso_)));
    gfxPso_->SetName(L"GCA_GraphicsPSO");

    D3D12_COMPUTE_PIPELINE_STATE_DESC cd{};
    cd.pRootSignature = csRootSig_.Get();
    cd.CS = {cs->GetBufferPointer(), cs->GetBufferSize()};
    CHECK(device_->CreateComputePipelineState(&cd, IID_PPV_ARGS(&csPso_)));
    csPso_->SetName(L"GCA_ComputePSO");
}

void App::Init(HWND hwnd)
{
    hwnd_ = hwnd;
    CreateDevice();
    CreatePipelines();
    CreateResources();
    if (opt_.extras) {
        extras_ = std::make_unique<Extras>();
        CHECK(allocators_[0]->Reset());
        CHECK(cmdList_->Reset(allocators_[0].Get(), nullptr));
        extras_->Init(device_.Get(), queue_.Get(), cmdList_.Get(), opt_.variant, ShaderDir());
        ExecuteAndWait();
    }
}

void App::RenderFrame(uint32_t frameIndex)
{
    UINT bi = swapchain_->GetCurrentBackBufferIndex();
    CHECK(allocators_[bi]->Reset());
    CHECK(cmdList_->Reset(allocators_[bi].Get(), nullptr));
    ID3D12GraphicsCommandList* cl = cmdList_.Get();

    PixBegin(cl, kv_.markerFrame);

    // ---- Compute pass ----
    PixBegin(cl, kv_.markerCompute);
    {
        auto b = Transition(uavBuffer_.Get(), D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        cl->ResourceBarrier(1, &b);
        ID3D12DescriptorHeap* heaps[] = {srvHeap_.Get()};
        cl->SetDescriptorHeaps(1, heaps);
        cl->SetComputeRootSignature(csRootSig_.Get());
        cl->SetPipelineState(csPso_.Get());
        cl->SetComputeRoot32BitConstants(0, 4, kv_.rootConstants, 0);
        D3D12_GPU_DESCRIPTOR_HANDLE g = srvHeap_->GetGPUDescriptorHandleForHeapStart();
        g.ptr += srvStride_;
        cl->SetComputeRootDescriptorTable(1, g);
        cl->Dispatch(kv_.dispatchX, kv_.dispatchY, kv_.dispatchZ);
        D3D12_GPU_DESCRIPTOR_HANDLE gu = srvHeap_->GetGPUDescriptorHandleForHeapStart();
        gu.ptr += 5ull * srvStride_;
        D3D12_RECT uavRect{0, 0, 32, 1};
        cl->ClearUnorderedAccessViewUint(gu, cpuUavHeap_->GetCPUDescriptorHandleForHeapStart(), uavBuffer_.Get(),
                                         kv_.uavClear, 1, &uavRect);
        b = Transition(uavBuffer_.Get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_COMMON);
        cl->ResourceBarrier(1, &b);
    }
    PixEnd(cl);

    // ---- Copy ----
    {
        auto b = Transition(texture_.Get(), D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_SOURCE);
        cl->ResourceBarrier(1, &b);
        D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
        dst.pResource = textureCopy_.Get();
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        src.pResource = texture_.Get();
        src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        D3D12_BOX box{1, 1, 0, 5, 4, 1};
        cl->CopyTextureRegion(&dst, 2, 3, 0, &src, &box);
        b = Transition(texture_.Get(), D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
        cl->ResourceBarrier(1, &b);
    }
    cl->CopyBufferRegion(copyDst_.Get(), 16, copySrc_.Get(), 32, kv_.copyBytes);

    // ---- Draw pass ----
    PixBegin(cl, kv_.markerDraw);
    {
        auto b = Transition(backBuffers_[bi].Get(), D3D12_RESOURCE_STATE_PRESENT, D3D12_RESOURCE_STATE_RENDER_TARGET);
        cl->ResourceBarrier(1, &b);
        D3D12_CPU_DESCRIPTOR_HANDLE rtv = rtvHeap_->GetCPUDescriptorHandleForHeapStart();
        rtv.ptr += (SIZE_T)bi * rtvStride_;
        cl->ClearRenderTargetView(rtv, kv_.clearColor, 0, nullptr);
        // オフスクリーン RT (mip1, 明示 RTV desc) を部分クリア
        D3D12_CPU_DESCRIPTOR_HANDLE offRtv = rtvHeap_->GetCPUDescriptorHandleForHeapStart();
        offRtv.ptr += (SIZE_T)kFrameCount * rtvStride_;
        D3D12_RECT clearRect{0, 0, 16, 8};
        cl->ClearRenderTargetView(offRtv, kv_.rtClearColor, 1, &clearRect);
        D3D12_CPU_DESCRIPTOR_HANDLE dsv = dsvHeap_->GetCPUDescriptorHandleForHeapStart();
        cl->ClearDepthStencilView(dsv, D3D12_CLEAR_FLAG_DEPTH | D3D12_CLEAR_FLAG_STENCIL, kv_.depthClear,
                                  kv_.stencilClear, 0, nullptr);
        cl->OMSetRenderTargets(1, &rtv, FALSE, &dsv);
        cl->OMSetStencilRef(kv_.stencilRef);
        cl->OMSetBlendFactor(kv_.blendFactor);

        D3D12_VIEWPORT vp{kv_.viewport[0], kv_.viewport[1], kv_.viewport[2], kv_.viewport[3], 0.0f, 1.0f};
        cl->RSSetViewports(1, &vp);
        cl->RSSetScissorRects(1, &kv_.scissor);

        cl->SetGraphicsRootSignature(gfxRootSig_.Get());
        cl->SetPipelineState(gfxPso_.Get());
        ID3D12DescriptorHeap* heaps[] = {srvHeap_.Get()};
        cl->SetDescriptorHeaps(1, heaps);
        cl->SetGraphicsRootConstantBufferView(0, constantBuffer_->GetGPUVirtualAddress());
        cl->SetGraphicsRoot32BitConstants(1, 4, kv_.rootConstants, 0);
        cl->SetGraphicsRootDescriptorTable(2, srvHeap_->GetGPUDescriptorHandleForHeapStart());
        cl->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        cl->IASetVertexBuffers(0, 1, &vbv_);
        cl->IASetIndexBuffer(&ibv_);

        PixMarker(cl, kv_.markerSetMarker);
        cl->DrawInstanced(kv_.drawVertexCount, kv_.drawInstanceCount, 0, 0);
        cl->DrawIndexedInstanced(kv_.indexCount, 1, 0, 0, 0);

        b = Transition(backBuffers_[bi].Get(), D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_PRESENT);
        cl->ResourceBarrier(1, &b);
    }
    PixEnd(cl);
    // キャプチャされるフレームだけディスクリプタを書き換える。CaptureNextFrame を frame N の直前に呼ぶと
    // 実際にキャプチャされるのは N+1 から --capture-count 枚 (実験で確認)
    if (extras_) extras_->Record(cl, frameIndex >= opt_.captureFrame + 1 && frameIndex < opt_.captureFrame + 1 + opt_.captureCount);
    PixEnd(cl);

    CHECK(cl->Close());
    if (extras_) extras_->SubmitAsync(queue_.Get());
    ID3D12CommandList* lists[] = {cl};
    queue_->ExecuteCommandLists(1, lists);
    CHECK(swapchain_->Present(1, 0));
    WaitIdle();
}

static void AppendJsonU32Array(std::string& s, const char* key, const uint32_t* v, int n)
{
    char buf[64];
    s += "  \""; s += key; s += "\": [";
    for (int i = 0; i < n; ++i) {
        sprintf_s(buf, "%s%u", i ? ", " : "", v[i]);
        s += buf;
    }
    s += "],\n";
}
static void AppendJsonF32Array(std::string& s, const char* key, const float* v, int n)
{
    char buf[64];
    s += "  \""; s += key; s += "\": [";
    for (int i = 0; i < n; ++i) {
        sprintf_s(buf, "%s%.9g", i ? ", " : "", v[i]);
        s += buf;
    }
    s += "],\n";
}
static std::string Narrow(const wchar_t* w)
{
    std::string s;
    for (; *w; ++w) s += (char)*w;
    return s;
}

void App::WriteManifest() const
{
    if (opt_.manifestPath.empty()) return;
    std::string s = "{\n";
    char buf[256];
    sprintf_s(buf, "  \"variant\": %u,\n", opt_.variant); s += buf;
    AppendJsonF32Array(s, "clearColor", kv_.clearColor, 4);
    AppendJsonU32Array(s, "rootConstants", kv_.rootConstants, 4);
    AppendJsonU32Array(s, "cbMagic", kv_.cbMagic, 4);
    AppendJsonF32Array(s, "cbFloats", kv_.cbFloats, 4);
    uint32_t draw[3] = {kv_.drawVertexCount, kv_.drawInstanceCount, kv_.indexCount};
    AppendJsonU32Array(s, "drawVertexCount_InstanceCount_IndexCount", draw, 3);
    uint32_t disp[3] = {kv_.dispatchX, kv_.dispatchY, kv_.dispatchZ};
    AppendJsonU32Array(s, "dispatch", disp, 3);
    sprintf_s(buf, "  \"copyBytes\": %u,\n", kv_.copyBytes); s += buf;
    AppendJsonF32Array(s, "viewport", kv_.viewport, 4);
    uint32_t sc[4] = {(uint32_t)kv_.scissor.left, (uint32_t)kv_.scissor.top, (uint32_t)kv_.scissor.right,
                      (uint32_t)kv_.scissor.bottom};
    AppendJsonU32Array(s, "scissor", sc, 4);
    sprintf_s(buf, "  \"textureMagic\": %u,\n", kv_.textureMagic); s += buf;
    sprintf_s(buf, "  \"uploadMagic\": %u,\n", kv_.uploadMagic); s += buf;
    sprintf_s(buf, "  \"srvHeapSize\": %u,\n", kv_.srvHeapSize); s += buf;
    sprintf_s(buf, "  \"depthClear\": %.9g,\n  \"stencilClear\": %u,\n", kv_.depthClear, kv_.stencilClear); s += buf;
    AppendJsonF32Array(s, "rtClearColor", kv_.rtClearColor, 4);
    AppendJsonU32Array(s, "uavClear", kv_.uavClear, 4);
    sprintf_s(buf, "  \"stencilRef\": %u,\n", kv_.stencilRef); s += buf;
    AppendJsonF32Array(s, "blendFactor", kv_.blendFactor, 4);
    s += "  \"markers\": [\"" + Narrow(kv_.markerFrame) + "\", \"" + Narrow(kv_.markerCompute) + "\", \"" +
         Narrow(kv_.markerDraw) + "\", \"" + Narrow(kv_.markerSetMarker) + "\"],\n";
    s += "  \"objectNames\": [\"GCA_Device\", \"GCA_DirectQueue\", \"GCA_RtvHeap\", \"GCA_SrvUavHeap\", "
         "\"GCA_BackBuffer0\", \"GCA_BackBuffer1\", \"GCA_Allocator0\", \"GCA_Allocator1\", "
         "\"GCA_MainCommandList\", \"GCA_Fence\", \"GCA_VertexBuffer\", \"GCA_IndexBuffer\", "
         "\"GCA_ConstantBuffer\", \"GCA_Texture8x8\", \"GCA_TextureUpload\", \"GCA_UavBuffer\", "
         "\"GCA_CopySource\", \"GCA_CopyDest\", \"GCA_GraphicsRootSig\", \"GCA_ComputeRootSig\", "
         "\"GCA_GraphicsPSO\", \"GCA_ComputePSO\", \"GCA_SamplerHeap\", \"GCA_DsvHeap\", \"GCA_CpuUavHeap\", "
         "\"GCA_Depth\", \"GCA_OffscreenRT\", \"GCA_TextureCopy\"],\n";
    if (extras_) s += "  \"extras\": " + extras_->ManifestJson() + ",\n";
    sprintf_s(buf, "  \"captureCount\": %u,\n", opt_.captureCount); s += buf;
    sprintf_s(buf, "  \"width\": %u,\n  \"height\": %u\n", kWidth, kHeight); s += buf;
    s += "}\n";
    FILE* f = nullptr;
    if (_wfopen_s(&f, opt_.manifestPath.c_str(), L"wb") == 0 && f) {
        fwrite(s.data(), 1, s.size(), f);
        fclose(f);
    }
}

// ---------------------------------------------------------------------------------------------
static LRESULT CALLBACK WndProc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (m == WM_DESTROY) {
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static Options ParseArgs()
{
    Options o;
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (int i = 1; i < argc; ++i) {
        std::wstring a = argv[i];
        auto next = [&]() -> std::wstring { return (i + 1 < argc) ? argv[++i] : L""; };
        if (a == L"--variant") o.variant = (uint32_t)_wtoi(next().c_str());
        else if (a == L"--pix-capture") o.pixCapturePath = next();
        else if (a == L"--manifest") o.manifestPath = next();
        else if (a == L"--capture-frame") o.captureFrame = (uint32_t)_wtoi(next().c_str());
        else if (a == L"--frames") o.totalFrames = (uint32_t)_wtoi(next().c_str());
        else if (a == L"--no-pix") o.loadPix = false;
        else if (a == L"--warp") o.warp = true;
        else if (a == L"--dxil") o.dxil = true;
        else if (a == L"--extras") o.extras = true;
        else if (a == L"--capture-count") o.captureCount = (uint32_t)_wtoi(next().c_str());
    }
    LocalFree(argv);
    if (o.totalFrames == 0) o.totalFrames = o.captureFrame + 10;
    return o;
}

int wmain()
{
    try {
        Options opt = ParseArgs();
        // WinPixGpuCapturer.dll は D3D12 デバイス作成前にロードする必要がある
        if (opt.loadPix || !opt.pixCapturePath.empty()) LoadPixCapturer();

        WNDCLASSW wc{};
        wc.lpfnWndProc = WndProc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = L"GCA_DX12CaptureTarget";
        RegisterClassW(&wc);
        RECT rc{0, 0, (LONG)kWidth, (LONG)kHeight};
        AdjustWindowRect(&rc, WS_OVERLAPPEDWINDOW, FALSE);
        HWND hwnd = CreateWindowW(wc.lpszClassName, L"GCA DX12 Capture Target", WS_OVERLAPPEDWINDOW,
                                  CW_USEDEFAULT, CW_USEDEFAULT, rc.right - rc.left, rc.bottom - rc.top,
                                  nullptr, nullptr, wc.hInstance, nullptr);
        ShowWindow(hwnd, SW_SHOWNOACTIVATE);

        App app(opt);
        app.Init(hwnd);
        app.WriteManifest();

        bool captureRequested = false;
        bool quit = false;
        for (uint32_t f = 0; f < opt.totalFrames && !quit; ++f) {
            MSG msg;
            while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
                if (msg.message == WM_QUIT) quit = true;
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            if (quit) break;
            if (f == opt.captureFrame && !opt.pixCapturePath.empty()) {
                if (g_captureNextFrame) {
                    HRESULT hr = g_captureNextFrame(opt.pixCapturePath.c_str(), opt.captureCount);
                    wprintf(L"[pix] CaptureNextFrame(%s) hr=0x%08X\n", opt.pixCapturePath.c_str(), (unsigned)hr);
                    captureRequested = SUCCEEDED(hr);
                } else {
                    wprintf(L"[pix] capture requested but capturer not available\n");
                }
            }
            app.RenderFrame(f);
        }
        app.WaitIdle();
        DestroyWindow(hwnd);
        if (captureRequested) {
            // キャプチャファイルの書き出しは非同期なので少し待つ
            Sleep(1000);
        }
        wprintf(L"[app] done\n");
        return 0;
    } catch (const std::exception& e) {
        printf("[error] %s\n", e.what());
        return 1;
    }
}
