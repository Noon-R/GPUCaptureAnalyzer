// GCA_GRAPHICS_SHADER_SOURCE_MARKER
cbuffer SceneCB : register(b0)
{
    uint4  gMagic;
    float4 gFloats;
};
cbuffer RootConsts : register(b1)
{
    uint4 gRootConsts;
};
Texture2D    gTex  : register(t0);
SamplerState gSamp : register(s0);

struct VSIn  { float3 pos : POSITION; float4 col : COLOR; float2 uv : TEXCOORD0; };
struct VSOut { float4 pos : SV_Position; float4 col : COLOR; float2 uv : TEXCOORD0; };

VSOut VSMain(VSIn i, uint iid : SV_InstanceID)
{
    VSOut o;
    o.pos = float4(i.pos.xy * (1.0 - 0.1 * iid) + gFloats.x * 0.0, i.pos.z, 1.0);
    o.col = i.col;
    o.uv  = i.uv;
    return o;
}

float4 PSMain(VSOut i) : SV_Target
{
    float4 t = gTex.Sample(gSamp, i.uv);
    float  k = (gMagic.x & 1) ? 1.0 : 0.5;
    return i.col * t * k + float4((gRootConsts.x & 0xFF) / 255.0, 0, 0, 0);
}
