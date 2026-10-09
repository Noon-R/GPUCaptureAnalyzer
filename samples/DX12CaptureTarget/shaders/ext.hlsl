// GCA_EXT_SHADER_SOURCE_MARKER
// --extras 用: PipelineStateStream / Mesh shader / Bundle / ExecuteIndirect / 非同期 compute で使う
cbuffer ExtConsts : register(b0)
{
    float4 gColor;
};
Texture2D    gTex0 : register(t0);
Texture2D    gTex1 : register(t1);
SamplerState gSamp : register(s0);

struct VOut
{
    float4 pos : SV_Position;
    float2 uv  : TEXCOORD0;
};

VOut VSMain(uint id : SV_VertexID)
{
    VOut o;
    float2 p = float2((id << 1) & 2, id & 2);
    o.pos = float4(p * 2.0 - 1.0, 0.5, 1.0);
    o.uv = p;
    return o;
}

float4 PSMain(VOut i) : SV_Target
{
    return gColor * gTex0.Sample(gSamp, i.uv) + gTex1.Sample(gSamp, i.uv) * 0.25;
}

[outputtopology("triangle")]
[numthreads(1, 1, 1)]
void MSMain(out vertices VOut verts[3], out indices uint3 tris[1])
{
    SetMeshOutputCounts(3, 1);
    for (uint i = 0; i < 3; ++i)
    {
        float2 p = float2((i << 1) & 2, i & 2);
        verts[i].pos = float4(p - 0.5, 0.5, 1.0);
        verts[i].uv = p;
    }
    tris[0] = uint3(0, 1, 2);
}

RWByteAddressBuffer gOut : register(u0);

[numthreads(8, 1, 1)]
void CSMain(uint3 id : SV_DispatchThreadID)
{
    gOut.Store(id.x * 4, asuint(gColor.x) + id.x);
}

// ---- Amplification shader 付きメッシュパイプライン ----
struct AmpPayload
{
    uint value;
};
groupshared AmpPayload sPayload;

[numthreads(1, 1, 1)]
void ASMain(uint gid : SV_GroupID)
{
    sPayload.value = gid;
    DispatchMesh(1, 1, 1, sPayload);
}

[outputtopology("triangle")]
[numthreads(1, 1, 1)]
void MSMainAmp(in payload AmpPayload p, out vertices VOut verts[3], out indices uint3 tris[1])
{
    SetMeshOutputCounts(3, 1);
    for (uint i = 0; i < 3; ++i)
    {
        float2 q = float2((i << 1) & 2, i & 2);
        verts[i].pos = float4(q * 0.5 - 0.25 + p.value * 0.01, 0.5, 1.0);
        verts[i].uv = q;
    }
    tris[0] = uint3(0, 1, 2);
}
