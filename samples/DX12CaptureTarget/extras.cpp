// --extras の実装。詳細は extras.h。
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "extras.h"

#include <windows.h>
#include <d3dcompiler.h>

#include <cstdio>
#include <cstring>
#include <stdexcept>

using Microsoft::WRL::ComPtr;

#define XCHECK(x)                                                                 \
    do {                                                                          \
        HRESULT hr__ = (x);                                                       \
        if (FAILED(hr__)) {                                                       \
            char buf__[512];                                                      \
            sprintf_s(buf__, "%s failed: hr=0x%08X (%s:%d)", #x, (unsigned)hr__,  \
                      __FILE__, __LINE__);                                        \
            throw std::runtime_error(buf__);                                      \
        }                                                                         \
    } while (0)

namespace {

constexpr UINT kExtRtSize = 64;
constexpr UINT kHeapSlots = 8;
// ext heap のスロット
constexpr UINT kSlotTex0 = 3;   // 初期: SRV texA -> フレーム内で CreateShaderResourceView(texB)
constexpr UINT kSlotTex1 = 4;   // 初期: SRV texA -> フレーム内で CopyDescriptorsSimple(cpuHeap[0] = SRV texC)
constexpr UINT kSlotRtOut = 5;  // UAV rtOutput

std::vector<char> ReadAll(const std::wstring& path)
{
    std::vector<char> d;
    FILE* f = nullptr;
    if (_wfopen_s(&f, path.c_str(), L"rb") != 0 || !f) throw std::runtime_error("cannot open shader file");
    fseek(f, 0, SEEK_END);
    d.resize((size_t)ftell(f));
    fseek(f, 0, SEEK_SET);
    fread(d.data(), 1, d.size(), f);
    fclose(f);
    return d;
}

D3D12_HEAP_PROPERTIES HP(D3D12_HEAP_TYPE t)
{
    D3D12_HEAP_PROPERTIES p{};
    p.Type = t;
    return p;
}

D3D12_RESOURCE_DESC BufDesc(uint64_t size, D3D12_RESOURCE_FLAGS flags = D3D12_RESOURCE_FLAG_NONE)
{
    D3D12_RESOURCE_DESC d{};
    d.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    d.Width = size;
    d.Height = 1;
    d.DepthOrArraySize = 1;
    d.MipLevels = 1;
    d.SampleDesc.Count = 1;
    d.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    d.Flags = flags;
    return d;
}

D3D12_RESOURCE_DESC TexDesc(UINT w, UINT h, DXGI_FORMAT fmt, D3D12_RESOURCE_FLAGS flags = D3D12_RESOURCE_FLAG_NONE)
{
    D3D12_RESOURCE_DESC d{};
    d.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    d.Width = w;
    d.Height = h;
    d.DepthOrArraySize = 1;
    d.MipLevels = 1;
    d.Format = fmt;
    d.SampleDesc.Count = 1;
    d.Flags = flags;
    return d;
}

D3D12_RESOURCE_BARRIER Uav(ID3D12Resource* r)
{
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
    b.UAV.pResource = r;
    return b;
}

D3D12_RESOURCE_BARRIER Trans(ID3D12Resource* r, D3D12_RESOURCE_STATES a, D3D12_RESOURCE_STATES b)
{
    D3D12_RESOURCE_BARRIER br{};
    br.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    br.Transition.pResource = r;
    br.Transition.StateBefore = a;
    br.Transition.StateAfter = b;
    br.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    return br;
}

// ---- PIX3 形式のイベント (WinPixEventRuntime の PIXBeginEvent(commandList, color, "...") 相当) ----
//   UINT64[]: EncodeEventInfo(0, type) | color | stringInfo | 文字列 (8 バイト単位, 終端込み)
//   type: BeginEvent_NoArgs = 0x2, SetMarker_NoArgs = 0x8 (bits 10-19)
//   stringInfo: alignment(60-63)=0, copyChunkSize(55-59)=8, isANSI(54), isShortcut(53)=0
//   Metadata = WINPIX_EVENT_PIX3BLOB_VERSION (2)
void Pix3(ID3D12GraphicsCommandList* cl, bool begin, uint64_t color, const void* str, size_t bytesWithNul, bool ansi)
{
    uint64_t buf[64] = {};
    uint64_t* d = buf;
    const uint64_t type = begin ? 0x2 : 0x8;
    *d++ = (type & 0x3FF) << 10;
    *d++ = color;
    *d++ = (8ull << 55) | ((ansi ? 1ull : 0ull) << 54);
    memcpy(d, str, bytesWithNul);
    d += (bytesWithNul + 7) / 8;
    *d = 0x00000000000FFF80ull;  // block end marker (サイズには含めない)
    const UINT size = (UINT)((d - buf) * sizeof(uint64_t));
    if (begin) cl->BeginEvent(2, buf, size);
    else cl->SetMarker(2, buf, size);
}

template <D3D12_PIPELINE_STATE_SUBOBJECT_TYPE T, class V>
struct alignas(void*) Sub {
    D3D12_PIPELINE_STATE_SUBOBJECT_TYPE type = T;
    V value{};
};

}  // namespace

ComPtr<ID3D12Resource> Extras::Buffer(uint64_t size, D3D12_HEAP_TYPE heap, D3D12_RESOURCE_STATES st,
                                      D3D12_RESOURCE_FLAGS flags, const wchar_t* name)
{
    auto hp = HP(heap);
    auto d = BufDesc(size, flags);
    ComPtr<ID3D12Resource> r;
    XCHECK(dev_->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &d, st, nullptr, IID_PPV_ARGS(&r)));
    r->SetName(name);
    return r;
}

D3D12_CPU_DESCRIPTOR_HANDLE Extras::Cpu(ID3D12DescriptorHeap* h, UINT i) const
{
    auto c = h->GetCPUDescriptorHandleForHeapStart();
    UINT stride = dev_->GetDescriptorHandleIncrementSize(h->GetDesc().Type);
    c.ptr += (SIZE_T)i * stride;
    return c;
}

D3D12_GPU_DESCRIPTOR_HANDLE Extras::Gpu(ID3D12DescriptorHeap* h, UINT i) const
{
    auto g = h->GetGPUDescriptorHandleForHeapStart();
    g.ptr += (UINT64)i * srvStride_;
    return g;
}

void Extras::Init(ID3D12Device* device, ID3D12CommandQueue* directQueue, ID3D12GraphicsCommandList* initList,
                  uint32_t variant, const std::wstring& shaderDir)
{
    directQueue_ = directQueue;
    v_ = variant;
    shaderDir_ = shaderDir;
    dev_ = device;
    dev_.As(&dev2_);
    dev_.As(&dev5_);
    dev_.As(&dev10_);
    srvStride_ = dev_->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);

    // ---- 既知の値 ----
    extClear_[0] = 0.0625f * (float)(v_ + 1); extClear_[1] = 0.5f; extClear_[2] = 0.75f; extClear_[3] = 1.0f;
    extDepthClear_ = 0.5f + 0.125f * (float)(v_ % 4);
    drawColor_[0] = 1.0f; drawColor_[1] = 0.5f + 0.0625f * (float)v_; drawColor_[2] = 0.25f; drawColor_[3] = 1.0f;
    placedCopyBytes_ = 512 + 16 * v_;
    uploadMagic_ = 0x91AC0000u | v_;
    indirectDraw_[0][0] = 3; indirectDraw_[0][1] = 1 + v_ % 2; indirectDraw_[0][2] = 0; indirectDraw_[0][3] = 0;
    indirectDraw_[1][0] = 3; indirectDraw_[1][1] = 3; indirectDraw_[1][2] = 0; indirectDraw_[1][3] = 1;
    indirectDispatch_[0] = 2 + v_; indirectDispatch_[1] = 1; indirectDispatch_[2] = 1;
    bundleInstances_ = 2 + v_;
    meshGroups_ = 1 + v_ % 2;
    ampGroups_ = 2 + v_;
    streamCsGroups_ = 5 + v_;
    raysW_ = 16 + v_;
    raysH_ = 8;
    tlasInstanceId_ = 7 + v_;
    missColor_[0] = 0.25f; missColor_[1] = 0.125f * (float)(v_ + 1); missColor_[2] = 0.5f; missColor_[3] = 1.0f;
    asyncDispatchX_ = 4 + v_;
    char b[64];
    sprintf_s(b, "GCA_Pix3Begin_v%u", v_);
    pix3Begin_ = b;
    sprintf_s(b, "GCA_Pix3Marker_v%u", v_);
    pix3Marker_ = b;
    for (char c : pix3Marker_) pix3MarkerW_ += (wchar_t)c;

    // ---- 機能サポート ----
    D3D12_FEATURE_DATA_D3D12_OPTIONS o0{};
    if (SUCCEEDED(dev_->CheckFeatureSupport(D3D12_FEATURE_D3D12_OPTIONS, &o0, sizeof(o0))))
        sup_.tiledResources = o0.TiledResourcesTier >= D3D12_TILED_RESOURCES_TIER_1;
    D3D12_FEATURE_DATA_D3D12_OPTIONS5 o5{};
    if (dev5_ && SUCCEEDED(dev_->CheckFeatureSupport(D3D12_FEATURE_D3D12_OPTIONS5, &o5, sizeof(o5))))
        sup_.raytracing = o5.RaytracingTier >= D3D12_RAYTRACING_TIER_1_0;
    D3D12_FEATURE_DATA_D3D12_OPTIONS7 o7{};
    if (SUCCEEDED(dev_->CheckFeatureSupport(D3D12_FEATURE_D3D12_OPTIONS7, &o7, sizeof(o7))))
        sup_.meshShader = o7.MeshShaderTier >= D3D12_MESH_SHADER_TIER_1;
    D3D12_FEATURE_DATA_D3D12_OPTIONS12 o12{};
    if (dev10_ && SUCCEEDED(dev_->CheckFeatureSupport(D3D12_FEATURE_D3D12_OPTIONS12, &o12, sizeof(o12))))
        sup_.enhancedBarriers = o12.EnhancedBarriersSupported != FALSE;
    printf("[extras] enhancedBarriers=%d mesh=%d raytracing=%d tiled=%d\n", sup_.enhancedBarriers, sup_.meshShader,
           sup_.raytracing, sup_.tiledResources);

    CreateHeapsAndTextures(initList);
    CreatePipelines();
    CreateIndirectAndQueries();
    CreateTiledAndPlaced();
    CreateEnhancedBarrierResources();
    CreateRaytracing(initList);
    CreateAsyncQueues();
    CreateBundle();
}

void Extras::CreateHeapsAndTextures(ID3D12GraphicsCommandList* initList)
{
    D3D12_DESCRIPTOR_HEAP_DESC hd{};
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
    hd.NumDescriptors = kHeapSlots;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    XCHECK(dev_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&heap_)));
    heap_->SetName(L"GCA_ExtHeap");
    hd.NumDescriptors = 2;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_NONE;
    XCHECK(dev_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&cpuHeap_)));
    cpuHeap_->SetName(L"GCA_ExtCpuHeap");
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    hd.NumDescriptors = 1;
    XCHECK(dev_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&rtvHeap_)));
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_DSV;
    XCHECK(dev_->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&dsvHeap_)));

    // 4x4 テクスチャ A/B/C (texel = tag<<24 | y<<4 | x)
    auto hpDef = HP(D3D12_HEAP_TYPE_DEFAULT);
    auto td = TexDesc(4, 4, DXGI_FORMAT_R8G8B8A8_UNORM);
    ComPtr<ID3D12Resource>* texs[3] = {&texA_, &texB_, &texC_};
    const wchar_t* names[3] = {L"GCA_ExtTexA", L"GCA_ExtTexB", L"GCA_ExtTexC"};
    const uint32_t tags[3] = {0xA1u, 0xB2u, 0xC3u};
    texUpload_ = Buffer(3 * 1024, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE,
                        L"GCA_ExtTexUpload");
    uint8_t* p = nullptr;
    XCHECK(texUpload_->Map(0, nullptr, (void**)&p));
    for (int t = 0; t < 3; ++t) {
        XCHECK(dev_->CreateCommittedResource(&hpDef, D3D12_HEAP_FLAG_NONE, &td, D3D12_RESOURCE_STATE_COPY_DEST, nullptr,
                                             IID_PPV_ARGS(texs[t]->GetAddressOf())));
        (*texs[t])->SetName(names[t]);
        for (UINT y = 0; y < 4; ++y)
            for (UINT x = 0; x < 4; ++x)
                ((uint32_t*)(p + t * 1024 + y * 256))[x] = (tags[t] << 24) | (v_ << 8) | (y << 4) | x;
        D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
        dst.pResource = texs[t]->Get();
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        src.pResource = texUpload_.Get();
        src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        src.PlacedFootprint.Offset = (UINT64)t * 1024;
        src.PlacedFootprint.Footprint = {DXGI_FORMAT_R8G8B8A8_UNORM, 4, 4, 1, 256};
        initList->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
        auto b = Trans(texs[t]->Get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_ALL_SHADER_RESOURCE);
        initList->ResourceBarrier(1, &b);
    }
    texUpload_->Unmap(0, nullptr);

    // 初期ディスクリプタ: 全スロット texA。CPU ヒープ[0] = texC
    for (UINT i = 0; i < kHeapSlots; ++i)
        if (i != kSlotRtOut) dev_->CreateShaderResourceView(texA_.Get(), nullptr, Cpu(heap_.Get(), i));
    dev_->CreateShaderResourceView(texC_.Get(), nullptr, Cpu(cpuHeap_.Get(), 0));

    // ext RT / depth (optimized clear value)
    D3D12_CLEAR_VALUE cv{};
    cv.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    memcpy(cv.Color, extClear_, sizeof(extClear_));
    auto rd = TexDesc(kExtRtSize, kExtRtSize, DXGI_FORMAT_R8G8B8A8_UNORM, D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET);
    XCHECK(dev_->CreateCommittedResource(&hpDef, D3D12_HEAP_FLAG_NONE, &rd, D3D12_RESOURCE_STATE_RENDER_TARGET, &cv,
                                         IID_PPV_ARGS(&extRT_)));
    extRT_->SetName(L"GCA_ExtRT");
    dev_->CreateRenderTargetView(extRT_.Get(), nullptr, Cpu(rtvHeap_.Get(), 0));
    D3D12_CLEAR_VALUE dv{};
    dv.Format = DXGI_FORMAT_D32_FLOAT;
    dv.DepthStencil.Depth = extDepthClear_;
    auto dd = TexDesc(kExtRtSize, kExtRtSize, DXGI_FORMAT_D32_FLOAT, D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL);
    XCHECK(dev_->CreateCommittedResource(&hpDef, D3D12_HEAP_FLAG_NONE, &dd, D3D12_RESOURCE_STATE_DEPTH_WRITE, &dv,
                                         IID_PPV_ARGS(&extDepth_)));
    extDepth_->SetName(L"GCA_ExtDepth");
    dev_->CreateDepthStencilView(extDepth_.Get(), nullptr, Cpu(dsvHeap_.Get(), 0));

    // 汎用アップロード: [0..4096) magic+i, [4096..8192) 16x16 RGBA8 テクスチャ (pitch 256)
    upload_ = Buffer(8192, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE,
                     L"GCA_ExtUpload");
    XCHECK(upload_->Map(0, nullptr, (void**)&p));
    for (UINT i = 0; i < 1024; ++i) ((uint32_t*)p)[i] = uploadMagic_ + i;
    for (UINT y = 0; y < 16; ++y)
        for (UINT x = 0; x < 16; ++x) ((uint32_t*)(p + 4096 + y * 256))[x] = 0xEB000000u | (y << 8) | x;
    upload_->Unmap(0, nullptr);
}

void Extras::CreatePipelines()
{
    // graphics RS: [0] 32bit constants b0 x4, [1] table SRV t0-t1, static sampler s0
    D3D12_DESCRIPTOR_RANGE range{D3D12_DESCRIPTOR_RANGE_TYPE_SRV, 2, 0, 0, 0};
    D3D12_ROOT_PARAMETER gp[2]{};
    gp[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    gp[0].Constants = {0, 0, 4};
    gp[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    gp[1].DescriptorTable = {1, &range};
    gp[1].ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    D3D12_STATIC_SAMPLER_DESC ss{};
    ss.Filter = D3D12_FILTER_MIN_MAG_MIP_POINT;
    ss.AddressU = ss.AddressV = ss.AddressW = D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
    ss.MaxLOD = D3D12_FLOAT32_MAX;
    ss.ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    D3D12_ROOT_SIGNATURE_DESC rsd{2, gp, 1, &ss, D3D12_ROOT_SIGNATURE_FLAG_NONE};
    ComPtr<ID3DBlob> blob, err;
    XCHECK(D3D12SerializeRootSignature(&rsd, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &err));
    XCHECK(dev_->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&gfxRS_)));
    gfxRS_->SetName(L"GCA_ExtGraphicsRootSig");

    // compute RS: [0] root UAV u0, [1] 32bit constants b0 x4
    D3D12_ROOT_PARAMETER cp[2]{};
    cp[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_UAV;
    cp[0].Descriptor = {0, 0};
    cp[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    cp[1].Constants = {0, 0, 4};
    D3D12_ROOT_SIGNATURE_DESC crs{2, cp, 0, nullptr, D3D12_ROOT_SIGNATURE_FLAG_NONE};
    XCHECK(D3D12SerializeRootSignature(&crs, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &err));
    XCHECK(dev_->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&csRS_)));
    csRS_->SetName(L"GCA_ExtComputeRootSig");

    auto vs = ReadAll(shaderDir_ + L"ext_vs.cso");
    auto ps = ReadAll(shaderDir_ + L"ext_ps.cso");
    auto cs = ReadAll(shaderDir_ + L"ext_cs.cso");

    // ---- PipelineStateStream (ID3D12Device2::CreatePipelineState) ----
    struct GfxStream {
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE, ID3D12RootSignature*> rs;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_VS, D3D12_SHADER_BYTECODE> vs;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PS, D3D12_SHADER_BYTECODE> ps;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RASTERIZER, D3D12_RASTERIZER_DESC> rast;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL, D3D12_DEPTH_STENCIL_DESC> ds;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PRIMITIVE_TOPOLOGY, D3D12_PRIMITIVE_TOPOLOGY_TYPE> topo;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RENDER_TARGET_FORMATS, D3D12_RT_FORMAT_ARRAY> rtv;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL_FORMAT, DXGI_FORMAT> dsv;
    } gs{};
    gs.rs.value = gfxRS_.Get();
    gs.vs.value = {vs.data(), vs.size()};
    gs.ps.value = {ps.data(), ps.size()};
    gs.rast.value.FillMode = D3D12_FILL_MODE_SOLID;
    gs.rast.value.CullMode = D3D12_CULL_MODE_NONE;
    gs.rast.value.DepthClipEnable = TRUE;
    gs.ds.value.DepthEnable = TRUE;
    gs.ds.value.DepthWriteMask = D3D12_DEPTH_WRITE_MASK_ALL;
    gs.ds.value.DepthFunc = D3D12_COMPARISON_FUNC_LESS_EQUAL;
    gs.topo.value = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    gs.rtv.value.NumRenderTargets = 1;
    gs.rtv.value.RTFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
    gs.dsv.value = DXGI_FORMAT_D32_FLOAT;
    D3D12_PIPELINE_STATE_STREAM_DESC sd{sizeof(gs), &gs};
    XCHECK(dev2_->CreatePipelineState(&sd, IID_PPV_ARGS(&streamPso_)));
    streamPso_->SetName(L"GCA_StreamPSO");

    if (sup_.meshShader) {
        auto ms = ReadAll(shaderDir_ + L"ext_ms.cso");
        struct MeshStream {
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE, ID3D12RootSignature*> rs;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_MS, D3D12_SHADER_BYTECODE> ms;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PS, D3D12_SHADER_BYTECODE> ps;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RASTERIZER, D3D12_RASTERIZER_DESC> rast;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RENDER_TARGET_FORMATS, D3D12_RT_FORMAT_ARRAY> rtv;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL_FORMAT, DXGI_FORMAT> dsv;
        } m{};
        m.rs.value = gfxRS_.Get();
        m.ms.value = {ms.data(), ms.size()};
        m.ps.value = {ps.data(), ps.size()};
        m.rast.value.FillMode = D3D12_FILL_MODE_SOLID;
        m.rast.value.CullMode = D3D12_CULL_MODE_NONE;
        m.rast.value.DepthClipEnable = TRUE;
        m.rtv.value.NumRenderTargets = 1;
        m.rtv.value.RTFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
        m.dsv.value = DXGI_FORMAT_D32_FLOAT;
        D3D12_PIPELINE_STATE_STREAM_DESC msd{sizeof(m), &m};
        XCHECK(dev2_->CreatePipelineState(&msd, IID_PPV_ARGS(&meshPso_)));
        meshPso_->SetName(L"GCA_MeshPSO");

        auto as = ReadAll(shaderDir_ + L"ext_as.cso");
        auto msp = ReadAll(shaderDir_ + L"ext_ms_amp.cso");
        struct AmpStream {
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE, ID3D12RootSignature*> rs;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_AS, D3D12_SHADER_BYTECODE> as;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_MS, D3D12_SHADER_BYTECODE> ms;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_PS, D3D12_SHADER_BYTECODE> ps;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RASTERIZER, D3D12_RASTERIZER_DESC> rast;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_RENDER_TARGET_FORMATS, D3D12_RT_FORMAT_ARRAY> rtv;
            Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_DEPTH_STENCIL_FORMAT, DXGI_FORMAT> dsv;
        } a{};
        a.rs.value = gfxRS_.Get();
        a.as.value = {as.data(), as.size()};
        a.ms.value = {msp.data(), msp.size()};
        a.ps.value = {ps.data(), ps.size()};
        a.rast.value.FillMode = D3D12_FILL_MODE_SOLID;
        a.rast.value.CullMode = D3D12_CULL_MODE_NONE;
        a.rast.value.DepthClipEnable = TRUE;
        a.rtv.value.NumRenderTargets = 1;
        a.rtv.value.RTFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
        a.dsv.value = DXGI_FORMAT_D32_FLOAT;
        D3D12_PIPELINE_STATE_STREAM_DESC asd{sizeof(a), &a};
        XCHECK(dev2_->CreatePipelineState(&asd, IID_PPV_ARGS(&ampPso_)));
        ampPso_->SetName(L"GCA_AmpMeshPSO");
    }

    // compute も PipelineStateStream で作る
    struct CsStream {
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_ROOT_SIGNATURE, ID3D12RootSignature*> rs;
        Sub<D3D12_PIPELINE_STATE_SUBOBJECT_TYPE_CS, D3D12_SHADER_BYTECODE> cs;
    } cst{};
    cst.rs.value = csRS_.Get();
    cst.cs.value = {cs.data(), cs.size()};
    D3D12_PIPELINE_STATE_STREAM_DESC csd{sizeof(cst), &cst};
    XCHECK(dev2_->CreatePipelineState(&csd, IID_PPV_ARGS(&streamCsPso_)));
    streamCsPso_->SetName(L"GCA_StreamComputePSO");

    D3D12_COMPUTE_PIPELINE_STATE_DESC cd{};
    cd.pRootSignature = csRS_.Get();
    cd.CS = {cs.data(), cs.size()};
    XCHECK(dev_->CreateComputePipelineState(&cd, IID_PPV_ARGS(&csPso_)));
    csPso_->SetName(L"GCA_ExtComputePSO");
}

void Extras::CreateIndirectAndQueries()
{
    indirectArgs_ = Buffer(64, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE,
                           L"GCA_IndirectArgs");
    uint32_t* p = nullptr;
    XCHECK(indirectArgs_->Map(0, nullptr, (void**)&p));
    memcpy(p, indirectDraw_, sizeof(indirectDraw_));          // [0, 32)  D3D12_DRAW_ARGUMENTS x2
    memcpy(p + 8, indirectDispatch_, sizeof(indirectDispatch_));  // [32, 44) D3D12_DISPATCH_ARGUMENTS
    indirectArgs_->Unmap(0, nullptr);

    D3D12_INDIRECT_ARGUMENT_DESC a{};
    a.Type = D3D12_INDIRECT_ARGUMENT_TYPE_DRAW;
    D3D12_COMMAND_SIGNATURE_DESC sd{sizeof(D3D12_DRAW_ARGUMENTS), 1, &a, 0};
    XCHECK(dev_->CreateCommandSignature(&sd, nullptr, IID_PPV_ARGS(&drawSig_)));
    drawSig_->SetName(L"GCA_DrawSignature");
    a.Type = D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH;
    sd.ByteStride = sizeof(D3D12_DISPATCH_ARGUMENTS);
    XCHECK(dev_->CreateCommandSignature(&sd, nullptr, IID_PPV_ARGS(&dispatchSig_)));
    dispatchSig_->SetName(L"GCA_DispatchSignature");
    indirectUav_ = Buffer(4096, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                          D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, L"GCA_IndirectUav");

    D3D12_QUERY_HEAP_DESC qd{D3D12_QUERY_HEAP_TYPE_TIMESTAMP, 2, 0};
    XCHECK(dev_->CreateQueryHeap(&qd, IID_PPV_ARGS(&tsHeap_)));
    tsHeap_->SetName(L"GCA_TimestampHeap");
    qd = {D3D12_QUERY_HEAP_TYPE_OCCLUSION, 1, 0};
    XCHECK(dev_->CreateQueryHeap(&qd, IID_PPV_ARGS(&occHeap_)));
    occHeap_->SetName(L"GCA_OcclusionHeap");
    readback_ = Buffer(512, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_FLAG_NONE,
                       L"GCA_QueryReadback");
}

void Extras::CreateTiledAndPlaced()
{
    D3D12_HEAP_DESC hd{};
    hd.SizeInBytes = 256 * 1024;
    hd.Properties = HP(D3D12_HEAP_TYPE_DEFAULT);
    hd.Alignment = D3D12_DEFAULT_RESOURCE_PLACEMENT_ALIGNMENT;
    hd.Flags = D3D12_HEAP_FLAG_ALLOW_ONLY_BUFFERS;
    XCHECK(dev_->CreateHeap(&hd, IID_PPV_ARGS(&placedHeap_)));
    placedHeap_->SetName(L"GCA_PlacedHeap");
    auto bd = BufDesc(4096);
    XCHECK(dev_->CreatePlacedResource(placedHeap_.Get(), 65536, &bd, D3D12_RESOURCE_STATE_COMMON, nullptr,
                                      IID_PPV_ARGS(&placed_)));
    placed_->SetName(L"GCA_PlacedBuffer");

    if (!sup_.tiledResources) return;
    hd.SizeInBytes = 2 * 65536;
    XCHECK(dev_->CreateHeap(&hd, IID_PPV_ARGS(&tileHeap_)));
    tileHeap_->SetName(L"GCA_TileHeap");
    auto rd = BufDesc(2 * 65536);
    XCHECK(dev_->CreateReservedResource(&rd, D3D12_RESOURCE_STATE_COMMON, nullptr, IID_PPV_ARGS(&reserved_)));
    reserved_->SetName(L"GCA_ReservedBuffer");
    // 初期化時: タイル 0 -> ヒープのタイル 0
    D3D12_TILED_RESOURCE_COORDINATE coord{0, 0, 0, 0};
    D3D12_TILE_REGION_SIZE size{1, FALSE, 0, 0, 0};
    UINT start = 0, count = 1;
    directQueue_->UpdateTileMappings(reserved_.Get(), 1, &coord, &size, tileHeap_.Get(), 1, nullptr, &start, &count,
                                     D3D12_TILE_MAPPING_FLAG_NONE);
}

void Extras::CreateEnhancedBarrierResources()
{
    if (!sup_.enhancedBarriers) return;
    auto hp = HP(D3D12_HEAP_TYPE_DEFAULT);
    D3D12_RESOURCE_DESC1 bd{};
    bd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    bd.Width = 4096;
    bd.Height = 1;
    bd.DepthOrArraySize = 1;
    bd.MipLevels = 1;
    bd.SampleDesc.Count = 1;
    bd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    XCHECK(dev10_->CreateCommittedResource3(&hp, D3D12_HEAP_FLAG_NONE, &bd, D3D12_BARRIER_LAYOUT_UNDEFINED, nullptr,
                                            nullptr, 0, nullptr, IID_PPV_ARGS(&ebBuffer_)));
    ebBuffer_->SetName(L"GCA_EBBuffer");
    D3D12_RESOURCE_DESC1 td{};
    td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    td.Width = 16;
    td.Height = 16;
    td.DepthOrArraySize = 1;
    td.MipLevels = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    XCHECK(dev10_->CreateCommittedResource3(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_BARRIER_LAYOUT_COMMON, nullptr,
                                            nullptr, 0, nullptr, IID_PPV_ARGS(&ebTexture_)));
    ebTexture_->SetName(L"GCA_EBTexture");
    td.MipLevels = 2;
    td.Width = 8;
    td.Height = 8;
    XCHECK(dev10_->CreateCommittedResource3(&hp, D3D12_HEAP_FLAG_NONE, &td, D3D12_BARRIER_LAYOUT_SHADER_RESOURCE, nullptr,
                                            nullptr, 0, nullptr, IID_PPV_ARGS(&ebTexture2_)));
    ebTexture2_->SetName(L"GCA_EBTexture2");
}

void Extras::CreateRaytracing(ID3D12GraphicsCommandList* initList)
{
    if (!sup_.raytracing) return;
    ComPtr<ID3D12GraphicsCommandList4> list4;
    XCHECK(initList->QueryInterface(IID_PPV_ARGS(&list4)));

    // global RS: [0] table UAV u0, [1] root SRV t0 (TLAS), [2] 32bit constants b0 x4
    D3D12_DESCRIPTOR_RANGE r{D3D12_DESCRIPTOR_RANGE_TYPE_UAV, 1, 0, 0, 0};
    D3D12_ROOT_PARAMETER p[3]{};
    p[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    p[0].DescriptorTable = {1, &r};
    p[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_SRV;
    p[1].Descriptor = {0, 0};
    p[2].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    p[2].Constants = {0, 0, 4};
    D3D12_ROOT_SIGNATURE_DESC rsd{3, p, 0, nullptr, D3D12_ROOT_SIGNATURE_FLAG_NONE};
    ComPtr<ID3DBlob> blob, err;
    XCHECK(D3D12SerializeRootSignature(&rsd, D3D_ROOT_SIGNATURE_VERSION_1, &blob, &err));
    XCHECK(dev_->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&rtRS_)));
    rtRS_->SetName(L"GCA_RTGlobalRootSig");

    // ---- state object ----
    auto lib = ReadAll(shaderDir_ + L"rt_lib.cso");
    D3D12_DXIL_LIBRARY_DESC libDesc{{lib.data(), lib.size()}, 0, nullptr};
    D3D12_HIT_GROUP_DESC hit{L"GCA_HitGroup", D3D12_HIT_GROUP_TYPE_TRIANGLES, nullptr, L"ClosestHit", nullptr};
    D3D12_RAYTRACING_SHADER_CONFIG sc{16, 8};
    D3D12_GLOBAL_ROOT_SIGNATURE grs{rtRS_.Get()};
    D3D12_RAYTRACING_PIPELINE_CONFIG pc{1};
    D3D12_STATE_SUBOBJECT subs[5] = {
        {D3D12_STATE_SUBOBJECT_TYPE_DXIL_LIBRARY, &libDesc},
        {D3D12_STATE_SUBOBJECT_TYPE_HIT_GROUP, &hit},
        {D3D12_STATE_SUBOBJECT_TYPE_RAYTRACING_SHADER_CONFIG, &sc},
        {D3D12_STATE_SUBOBJECT_TYPE_GLOBAL_ROOT_SIGNATURE, &grs},
        {D3D12_STATE_SUBOBJECT_TYPE_RAYTRACING_PIPELINE_CONFIG, &pc},
    };
    D3D12_STATE_OBJECT_DESC sod{D3D12_STATE_OBJECT_TYPE_RAYTRACING_PIPELINE, 5, subs};
    XCHECK(dev5_->CreateStateObject(&sod, IID_PPV_ARGS(&rtpso_)));
    rtpso_->SetName(L"GCA_RTPSO");

    // ---- BLAS (初期化時にビルド) ----
    const float verts[9] = {-0.5f, -0.5f, 0.5f, 0.5f, -0.5f, 0.5f, 0.0f, 0.5f + 0.0625f * (float)v_, 0.5f};
    rtVerts_ = Buffer(sizeof(verts), D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE,
                      L"GCA_RTVertices");
    void* m = nullptr;
    XCHECK(rtVerts_->Map(0, nullptr, &m));
    memcpy(m, verts, sizeof(verts));
    rtVerts_->Unmap(0, nullptr);
    D3D12_RAYTRACING_GEOMETRY_DESC geo{};
    geo.Type = D3D12_RAYTRACING_GEOMETRY_TYPE_TRIANGLES;
    geo.Flags = D3D12_RAYTRACING_GEOMETRY_FLAG_OPAQUE;
    geo.Triangles.VertexBuffer = {rtVerts_->GetGPUVirtualAddress(), 12};
    geo.Triangles.VertexFormat = DXGI_FORMAT_R32G32B32_FLOAT;
    geo.Triangles.VertexCount = 3;
    D3D12_BUILD_RAYTRACING_ACCELERATION_STRUCTURE_INPUTS bin{};
    bin.Type = D3D12_RAYTRACING_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL;
    bin.Flags = D3D12_RAYTRACING_ACCELERATION_STRUCTURE_BUILD_FLAG_PREFER_FAST_TRACE;
    bin.NumDescs = 1;
    bin.DescsLayout = D3D12_ELEMENTS_LAYOUT_ARRAY;
    bin.pGeometryDescs = &geo;
    D3D12_RAYTRACING_ACCELERATION_STRUCTURE_PREBUILD_INFO pre{};
    dev5_->GetRaytracingAccelerationStructurePrebuildInfo(&bin, &pre);
    blas_ = Buffer(pre.ResultDataMaxSizeInBytes, D3D12_HEAP_TYPE_DEFAULT,
                   D3D12_RESOURCE_STATE_RAYTRACING_ACCELERATION_STRUCTURE, D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                   L"GCA_BLAS");
    blasScratch_ = Buffer(pre.ScratchDataSizeInBytes, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                          D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, L"GCA_BLASScratch");
    D3D12_BUILD_RAYTRACING_ACCELERATION_STRUCTURE_DESC bd{};
    bd.DestAccelerationStructureData = blas_->GetGPUVirtualAddress();
    bd.Inputs = bin;
    bd.ScratchAccelerationStructureData = blasScratch_->GetGPUVirtualAddress();
    list4->BuildRaytracingAccelerationStructure(&bd, 0, nullptr);
    auto ub = Uav(blas_.Get());
    initList->ResourceBarrier(1, &ub);

    // ---- TLAS 用 (フレーム内でビルド) ----
    D3D12_RAYTRACING_INSTANCE_DESC inst{};
    inst.Transform[0][0] = inst.Transform[1][1] = inst.Transform[2][2] = 1.0f;
    inst.InstanceID = tlasInstanceId_;
    inst.InstanceMask = 0xFF;
    inst.AccelerationStructure = blas_->GetGPUVirtualAddress();
    instances_ = Buffer(sizeof(inst), D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE,
                        L"GCA_RTInstances");
    XCHECK(instances_->Map(0, nullptr, &m));
    memcpy(m, &inst, sizeof(inst));
    instances_->Unmap(0, nullptr);
    D3D12_BUILD_RAYTRACING_ACCELERATION_STRUCTURE_INPUTS tin{};
    tin.Type = D3D12_RAYTRACING_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL;
    tin.NumDescs = 1;
    tin.DescsLayout = D3D12_ELEMENTS_LAYOUT_ARRAY;
    tin.InstanceDescs = instances_->GetGPUVirtualAddress();
    dev5_->GetRaytracingAccelerationStructurePrebuildInfo(&tin, &pre);
    tlas_ = Buffer(pre.ResultDataMaxSizeInBytes, D3D12_HEAP_TYPE_DEFAULT,
                   D3D12_RESOURCE_STATE_RAYTRACING_ACCELERATION_STRUCTURE, D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS,
                   L"GCA_TLAS");
    tlasScratch_ = Buffer(pre.ScratchDataSizeInBytes, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                          D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, L"GCA_TLASScratch");

    // ---- shader table: raygen @0, miss @64, hitgroup @128 (各 64B) ----
    ComPtr<ID3D12StateObjectProperties> props;
    XCHECK(rtpso_.As(&props));
    shaderTable_ = Buffer(256, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ, D3D12_RESOURCE_FLAG_NONE,
                          L"GCA_ShaderTable");
    uint8_t* t = nullptr;
    XCHECK(shaderTable_->Map(0, nullptr, (void**)&t));
    memset(t, 0, 256);
    memcpy(t + 0, props->GetShaderIdentifier(L"RayGen"), D3D12_SHADER_IDENTIFIER_SIZE_IN_BYTES);
    memcpy(t + 64, props->GetShaderIdentifier(L"Miss"), D3D12_SHADER_IDENTIFIER_SIZE_IN_BYTES);
    memcpy(t + 128, props->GetShaderIdentifier(L"GCA_HitGroup"), D3D12_SHADER_IDENTIFIER_SIZE_IN_BYTES);
    shaderTable_->Unmap(0, nullptr);

    // 出力テクスチャ (UAV) -> ext heap[kSlotRtOut]
    auto hp = HP(D3D12_HEAP_TYPE_DEFAULT);
    auto od = TexDesc(32, 8, DXGI_FORMAT_R8G8B8A8_UNORM, D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS);
    XCHECK(dev_->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &od, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, nullptr,
                                         IID_PPV_ARGS(&rtOutput_)));
    rtOutput_->SetName(L"GCA_RTOutput");
    dev_->CreateUnorderedAccessView(rtOutput_.Get(), nullptr, nullptr, Cpu(heap_.Get(), kSlotRtOut));
}

void Extras::CreateAsyncQueues()
{
    D3D12_COMMAND_QUEUE_DESC qd{};
    qd.Type = D3D12_COMMAND_LIST_TYPE_COPY;
    XCHECK(dev_->CreateCommandQueue(&qd, IID_PPV_ARGS(&copyQueue_)));
    copyQueue_->SetName(L"GCA_CopyQueue");
    qd.Type = D3D12_COMMAND_LIST_TYPE_COMPUTE;
    XCHECK(dev_->CreateCommandQueue(&qd, IID_PPV_ARGS(&computeQueue_)));
    computeQueue_->SetName(L"GCA_ComputeQueue");
    XCHECK(dev_->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_COPY, IID_PPV_ARGS(&copyAlloc_)));
    XCHECK(dev_->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_COMPUTE, IID_PPV_ARGS(&computeAlloc_)));
    XCHECK(dev_->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_COPY, copyAlloc_.Get(), nullptr, IID_PPV_ARGS(&copyList_)));
    copyList_->SetName(L"GCA_CopyList");
    XCHECK(copyList_->Close());
    XCHECK(dev_->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_COMPUTE, computeAlloc_.Get(), nullptr,
                                   IID_PPV_ARGS(&computeList_)));
    computeList_->SetName(L"GCA_ComputeList");
    XCHECK(computeList_->Close());
    XCHECK(dev_->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&copyFence_)));
    copyFence_->SetName(L"GCA_CopyFence");
    XCHECK(dev_->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&computeFence_)));
    computeFence_->SetName(L"GCA_ComputeFence");
    asyncEvent_ = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    copyQueueDst_ = Buffer(4096, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_FLAG_NONE,
                           L"GCA_CopyQueueDst");
    asyncUav_ = Buffer(4096, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COMMON,
                       D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS, L"GCA_AsyncUav");
}

void Extras::CreateBundle()
{
    XCHECK(dev_->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_BUNDLE, IID_PPV_ARGS(&bundleAlloc_)));
    bundleAlloc_->SetName(L"GCA_BundleAllocator");
    XCHECK(dev_->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_BUNDLE, bundleAlloc_.Get(), streamPso_.Get(),
                                   IID_PPV_ARGS(&bundle_)));
    bundle_->SetName(L"GCA_Bundle");
    // ルートシグネチャは呼び出し元から継承 (バンドル内では変更しない)
    bundle_->SetPipelineState(streamPso_.Get());
    bundle_->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    float c[4] = {0.0f, 1.0f, 0.0f, 1.0f};
    bundle_->SetGraphicsRoot32BitConstants(0, 4, c, 0);
    bundle_->DrawInstanced(3, bundleInstances_, 0, 0);
    XCHECK(bundle_->Close());
}

void Extras::SubmitAsync(ID3D12CommandQueue* directQueue)
{
    // 前フレームの非同期作業の完了を CPU で待ってからアロケータを再利用
    if (computeFence_->GetCompletedValue() < asyncValue_) {
        XCHECK(computeFence_->SetEventOnCompletion(asyncValue_, asyncEvent_));
        WaitForSingleObject(asyncEvent_, INFINITE);
    }
    ++asyncValue_;
    // copy queue: upload -> CopyQueueDst
    XCHECK(copyAlloc_->Reset());
    XCHECK(copyList_->Reset(copyAlloc_.Get(), nullptr));
    copyList_->CopyBufferRegion(copyQueueDst_.Get(), 0, upload_.Get(), 0, 1024);
    XCHECK(copyList_->Close());
    ID3D12CommandList* cl[] = {copyList_.Get()};
    copyQueue_->ExecuteCommandLists(1, cl);
    XCHECK(copyQueue_->Signal(copyFence_.Get(), asyncValue_));
    // compute queue: copy 完了を待って root UAV に書き込む dispatch
    XCHECK(computeQueue_->Wait(copyFence_.Get(), asyncValue_));
    XCHECK(computeAlloc_->Reset());
    XCHECK(computeList_->Reset(computeAlloc_.Get(), csPso_.Get()));
    computeList_->SetComputeRootSignature(csRS_.Get());
    computeList_->SetComputeRootUnorderedAccessView(0, asyncUav_->GetGPUVirtualAddress() + 256);
    computeList_->SetComputeRoot32BitConstants(1, 4, drawColor_, 0);
    computeList_->Dispatch(asyncDispatchX_, 1, 1);
    XCHECK(computeList_->Close());
    cl[0] = computeList_.Get();
    computeQueue_->ExecuteCommandLists(1, cl);
    XCHECK(computeQueue_->Signal(computeFence_.Get(), asyncValue_));
    // direct queue は compute 完了を待つ
    XCHECK(directQueue->Wait(computeFence_.Get(), asyncValue_));
    // フレーム内: reserved のタイル 1 -> ヒープのタイル 1
    if (reserved_) {
        D3D12_TILED_RESOURCE_COORDINATE coord{1, 0, 0, 0};
        D3D12_TILE_REGION_SIZE size{1, FALSE, 0, 0, 0};
        UINT start = 1, count = 1;
        directQueue->UpdateTileMappings(reserved_.Get(), 1, &coord, &size, tileHeap_.Get(), 1, nullptr, &start, &count,
                                        D3D12_TILE_MAPPING_FLAG_NONE);
    }
}

void Extras::Record(ID3D12GraphicsCommandList* cl, bool inFrameRewrite)
{
    ComPtr<ID3D12GraphicsCommandList4> cl4;
    ComPtr<ID3D12GraphicsCommandList6> cl6;
    ComPtr<ID3D12GraphicsCommandList7> cl7;
    cl->QueryInterface(IID_PPV_ARGS(&cl4));
    cl->QueryInterface(IID_PPV_ARGS(&cl6));
    cl->QueryInterface(IID_PPV_ARGS(&cl7));

    Pix3(cl, true, 0xFF00FF00ull, pix3Begin_.c_str(), pix3Begin_.size() + 1, true);

    // ---- フレーム内のディスクリプタ書き換え (キャプチャ開始時のスナップショットと異なる内容になる) ----
    if (inFrameRewrite) {
        dev_->CreateShaderResourceView(texB_.Get(), nullptr, Cpu(heap_.Get(), kSlotTex0));
        dev_->CopyDescriptorsSimple(1, Cpu(heap_.Get(), kSlotTex1), Cpu(cpuHeap_.Get(), 0),
                                    D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    } else {
        // 前フレームは WaitIdle 済みなので GPU が参照中のディスクリプタを書き換えることはない
        dev_->CreateShaderResourceView(texA_.Get(), nullptr, Cpu(heap_.Get(), kSlotTex0));
        dev_->CreateShaderResourceView(texA_.Get(), nullptr, Cpu(heap_.Get(), kSlotTex1));
    }
    ID3D12DescriptorHeap* heaps[] = {heap_.Get()};
    cl->SetDescriptorHeaps(1, heaps);

    // ---- Placed / Reserved ----
    cl->CopyBufferRegion(placed_.Get(), 0, upload_.Get(), 0, placedCopyBytes_);
    if (reserved_) cl->CopyBufferRegion(reserved_.Get(), 0, upload_.Get(), 1024, 256);

    // ---- Enhanced Barriers ----
    if (sup_.enhancedBarriers && cl7) {
        D3D12_GLOBAL_BARRIER gb{D3D12_BARRIER_SYNC_ALL, D3D12_BARRIER_SYNC_ALL, D3D12_BARRIER_ACCESS_COMMON,
                                D3D12_BARRIER_ACCESS_COMMON};
        D3D12_BUFFER_BARRIER bb{D3D12_BARRIER_SYNC_NONE, D3D12_BARRIER_SYNC_COPY, D3D12_BARRIER_ACCESS_NO_ACCESS,
                                D3D12_BARRIER_ACCESS_COPY_DEST, ebBuffer_.Get(), 0, UINT64_MAX};
        D3D12_TEXTURE_BARRIER tb{};
        tb.SyncBefore = D3D12_BARRIER_SYNC_NONE;
        tb.SyncAfter = D3D12_BARRIER_SYNC_COPY;
        tb.AccessBefore = D3D12_BARRIER_ACCESS_NO_ACCESS;
        tb.AccessAfter = D3D12_BARRIER_ACCESS_COPY_DEST;
        tb.LayoutBefore = D3D12_BARRIER_LAYOUT_COMMON;
        tb.LayoutAfter = D3D12_BARRIER_LAYOUT_COPY_DEST;
        tb.pResource = ebTexture_.Get();
        tb.Subresources = {0xFFFFFFFF, 0, 0, 0, 0, 0};
        D3D12_TEXTURE_BARRIER tb2{};
        tb2.SyncBefore = D3D12_BARRIER_SYNC_NONE;
        tb2.SyncAfter = D3D12_BARRIER_SYNC_COPY;
        tb2.AccessBefore = D3D12_BARRIER_ACCESS_NO_ACCESS;
        tb2.AccessAfter = D3D12_BARRIER_ACCESS_COPY_SOURCE;
        tb2.LayoutBefore = D3D12_BARRIER_LAYOUT_SHADER_RESOURCE;
        tb2.LayoutAfter = D3D12_BARRIER_LAYOUT_COPY_SOURCE;
        tb2.pResource = ebTexture2_.Get();
        tb2.Subresources = {1, 1, 0, 1, 0, 1};  // mip1 のみ
        D3D12_TEXTURE_BARRIER tbs[2] = {};
        D3D12_BARRIER_GROUP groups[3]{};
        groups[0].Type = D3D12_BARRIER_TYPE_GLOBAL;
        groups[0].NumBarriers = 1;
        groups[0].pGlobalBarriers = &gb;
        groups[1].Type = D3D12_BARRIER_TYPE_BUFFER;
        groups[1].NumBarriers = 1;
        groups[1].pBufferBarriers = &bb;
        tbs[0] = tb;
        tbs[1] = tb2;
        groups[2].Type = D3D12_BARRIER_TYPE_TEXTURE;
        groups[2].NumBarriers = 2;
        groups[2].pTextureBarriers = tbs;
        cl7->Barrier(3, groups);
        cl->CopyBufferRegion(ebBuffer_.Get(), 0, upload_.Get(), 0, 2048);
        D3D12_TEXTURE_COPY_LOCATION dst{}, src{};
        dst.pResource = ebTexture_.Get();
        dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        src.pResource = upload_.Get();
        src.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        src.PlacedFootprint.Offset = 4096;
        src.PlacedFootprint.Footprint = {DXGI_FORMAT_R8G8B8A8_UNORM, 16, 16, 1, 256};
        cl->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
        bb = {D3D12_BARRIER_SYNC_COPY, D3D12_BARRIER_SYNC_NONE, D3D12_BARRIER_ACCESS_COPY_DEST,
              D3D12_BARRIER_ACCESS_NO_ACCESS, ebBuffer_.Get(), 0, UINT64_MAX};
        tb.SyncBefore = D3D12_BARRIER_SYNC_COPY;
        tb.SyncAfter = D3D12_BARRIER_SYNC_NONE;
        tb.AccessBefore = D3D12_BARRIER_ACCESS_COPY_DEST;
        tb.AccessAfter = D3D12_BARRIER_ACCESS_NO_ACCESS;
        tb.LayoutBefore = D3D12_BARRIER_LAYOUT_COPY_DEST;
        tb.LayoutAfter = D3D12_BARRIER_LAYOUT_COMMON;
        tb2.SyncBefore = D3D12_BARRIER_SYNC_COPY;
        tb2.SyncAfter = D3D12_BARRIER_SYNC_NONE;
        tb2.AccessBefore = D3D12_BARRIER_ACCESS_COPY_SOURCE;
        tb2.AccessAfter = D3D12_BARRIER_ACCESS_NO_ACCESS;
        tb2.LayoutBefore = D3D12_BARRIER_LAYOUT_COPY_SOURCE;
        tb2.LayoutAfter = D3D12_BARRIER_LAYOUT_SHADER_RESOURCE;
        tbs[0] = tb;
        tbs[1] = tb2;
        cl7->Barrier(2, groups + 1);
    }

    // ---- DXR: TLAS ビルド -> DispatchRays ----
    if (sup_.raytracing && cl4) {
        D3D12_BUILD_RAYTRACING_ACCELERATION_STRUCTURE_DESC td{};
        td.DestAccelerationStructureData = tlas_->GetGPUVirtualAddress();
        td.Inputs.Type = D3D12_RAYTRACING_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL;
        td.Inputs.NumDescs = 1;
        td.Inputs.DescsLayout = D3D12_ELEMENTS_LAYOUT_ARRAY;
        td.Inputs.InstanceDescs = instances_->GetGPUVirtualAddress();
        td.ScratchAccelerationStructureData = tlasScratch_->GetGPUVirtualAddress();
        cl4->BuildRaytracingAccelerationStructure(&td, 0, nullptr);
        auto ub = Uav(tlas_.Get());
        cl->ResourceBarrier(1, &ub);
        cl4->SetPipelineState1(rtpso_.Get());
        cl->SetComputeRootSignature(rtRS_.Get());
        cl->SetComputeRootDescriptorTable(0, Gpu(heap_.Get(), kSlotRtOut));
        cl->SetComputeRootShaderResourceView(1, tlas_->GetGPUVirtualAddress());
        cl->SetComputeRoot32BitConstants(2, 4, missColor_, 0);
        D3D12_DISPATCH_RAYS_DESC rd{};
        const auto base = shaderTable_->GetGPUVirtualAddress();
        rd.RayGenerationShaderRecord = {base, 64};
        rd.MissShaderTable = {base + 64, 64, 64};
        rd.HitGroupTable = {base + 128, 64, 64};
        rd.Width = raysW_;
        rd.Height = raysH_;
        rd.Depth = 1;
        cl4->DispatchRays(&rd);
    }

    // ---- ExecuteIndirect (dispatch) ----
    cl->SetComputeRootSignature(csRS_.Get());
    cl->SetPipelineState(csPso_.Get());
    cl->SetComputeRootUnorderedAccessView(0, indirectUav_->GetGPUVirtualAddress());
    cl->SetComputeRoot32BitConstants(1, 4, drawColor_, 0);
    cl->ExecuteIndirect(dispatchSig_.Get(), 1, indirectArgs_.Get(), 32, nullptr, 0);
    cl->SetPipelineState(streamCsPso_.Get());
    cl->Dispatch(streamCsGroups_, 1, 1);

    // ---- RenderPass + Query + ExecuteIndirect(draw) + Bundle + Mesh ----
    cl->EndQuery(tsHeap_.Get(), D3D12_QUERY_TYPE_TIMESTAMP, 0);
    if (cl4) {
        D3D12_RENDER_PASS_RENDER_TARGET_DESC rt{};
        rt.cpuDescriptor = Cpu(rtvHeap_.Get(), 0);
        rt.BeginningAccess.Type = D3D12_RENDER_PASS_BEGINNING_ACCESS_TYPE_CLEAR;
        rt.BeginningAccess.Clear.ClearValue.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
        memcpy(rt.BeginningAccess.Clear.ClearValue.Color, extClear_, sizeof(extClear_));
        rt.EndingAccess.Type = D3D12_RENDER_PASS_ENDING_ACCESS_TYPE_PRESERVE;
        D3D12_RENDER_PASS_DEPTH_STENCIL_DESC ds{};
        ds.cpuDescriptor = Cpu(dsvHeap_.Get(), 0);
        ds.DepthBeginningAccess.Type = D3D12_RENDER_PASS_BEGINNING_ACCESS_TYPE_CLEAR;
        ds.DepthBeginningAccess.Clear.ClearValue.Format = DXGI_FORMAT_D32_FLOAT;
        ds.DepthBeginningAccess.Clear.ClearValue.DepthStencil.Depth = extDepthClear_;
        ds.DepthEndingAccess.Type = D3D12_RENDER_PASS_ENDING_ACCESS_TYPE_PRESERVE;
        ds.StencilBeginningAccess.Type = D3D12_RENDER_PASS_BEGINNING_ACCESS_TYPE_NO_ACCESS;
        ds.StencilEndingAccess.Type = D3D12_RENDER_PASS_ENDING_ACCESS_TYPE_NO_ACCESS;
        cl4->BeginRenderPass(1, &rt, &ds, D3D12_RENDER_PASS_FLAG_NONE);

        D3D12_VIEWPORT vp{0, 0, (float)kExtRtSize, (float)kExtRtSize, 0, 1};
        D3D12_RECT sc{0, 0, (LONG)kExtRtSize, (LONG)kExtRtSize};
        cl->RSSetViewports(1, &vp);
        cl->RSSetScissorRects(1, &sc);
        cl->SetGraphicsRootSignature(gfxRS_.Get());
        cl->SetPipelineState(streamPso_.Get());
        cl->SetGraphicsRoot32BitConstants(0, 4, drawColor_, 0);
        cl->SetGraphicsRootDescriptorTable(1, Gpu(heap_.Get(), kSlotTex0));
        cl->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        cl->BeginQuery(occHeap_.Get(), D3D12_QUERY_TYPE_OCCLUSION, 0);
        cl->DrawInstanced(3, 1, 0, 0);
        cl->EndQuery(occHeap_.Get(), D3D12_QUERY_TYPE_OCCLUSION, 0);
        cl->ExecuteIndirect(drawSig_.Get(), 2, indirectArgs_.Get(), 0, nullptr, 0);
        cl->ExecuteBundle(bundle_.Get());
        if (sup_.meshShader && cl6) {
            cl->SetPipelineState(meshPso_.Get());
            cl6->DispatchMesh(meshGroups_, 1, 1);
            cl->SetPipelineState(ampPso_.Get());
            cl6->DispatchMesh(ampGroups_, 1, 1);
        }
        cl4->EndRenderPass();
    }
    cl->EndQuery(tsHeap_.Get(), D3D12_QUERY_TYPE_TIMESTAMP, 1);
    cl->ResolveQueryData(tsHeap_.Get(), D3D12_QUERY_TYPE_TIMESTAMP, 0, 2, readback_.Get(), 0);
    cl->ResolveQueryData(occHeap_.Get(), D3D12_QUERY_TYPE_OCCLUSION, 0, 1, readback_.Get(), 256);

    Pix3(cl, false, 0xFFFF0000ull, pix3MarkerW_.c_str(), (pix3MarkerW_.size() + 1) * sizeof(wchar_t), false);
    cl->EndEvent();
}

static std::string F4(const float* f)
{
    char b[128];
    sprintf_s(b, "[%.9g, %.9g, %.9g, %.9g]", f[0], f[1], f[2], f[3]);
    return b;
}

std::string Extras::ManifestJson() const
{
    char b[2048];
    sprintf_s(b,
              "{\"supported\": {\"enhancedBarriers\": %s, \"meshShader\": %s, \"raytracing\": %s, \"tiledResources\": %s}, "
              "\"extClearColor\": %s, \"extDepthClear\": %.9g, \"drawColor\": %s, \"placedCopyBytes\": %u, "
              "\"uploadMagic\": %u, \"indirectDraw\": [[%u, %u, %u, %u], [%u, %u, %u, %u]], "
              "\"indirectDispatch\": [%u, %u, %u], \"bundleInstances\": %u, \"meshGroups\": %u, "
              "\"ampGroups\": %u, \"streamCsGroups\": %u, \"rays\": [%u, %u, 1], \"tlasInstanceId\": %u, \"missColor\": %s, \"asyncDispatchX\": %u, "
              "\"pix3Begin\": \"%s\", \"pix3Marker\": \"%s\", \"texTags\": [161, 178, 195]}",
              sup_.enhancedBarriers ? "true" : "false", sup_.meshShader ? "true" : "false",
              sup_.raytracing ? "true" : "false", sup_.tiledResources ? "true" : "false", F4(extClear_).c_str(),
              extDepthClear_, F4(drawColor_).c_str(), placedCopyBytes_, uploadMagic_, indirectDraw_[0][0],
              indirectDraw_[0][1], indirectDraw_[0][2], indirectDraw_[0][3], indirectDraw_[1][0], indirectDraw_[1][1],
              indirectDraw_[1][2], indirectDraw_[1][3], indirectDispatch_[0], indirectDispatch_[1], indirectDispatch_[2],
              bundleInstances_, meshGroups_, ampGroups_, streamCsGroups_, raysW_, raysH_, tlasInstanceId_, F4(missColor_).c_str(), asyncDispatchX_,
              pix3Begin_.c_str(), pix3Marker_.c_str());
    return b;
}
