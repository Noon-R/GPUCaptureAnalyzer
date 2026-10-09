// GCA_COMPUTE_SHADER_SOURCE_MARKER
RWStructuredBuffer<uint> gOut : register(u0);
cbuffer RootConsts : register(b0) { uint4 gRootConsts; };

[numthreads(8, 1, 1)]
void CSMain(uint3 id : SV_DispatchThreadID)
{
    if (id.x < 64)
        gOut[id.x] = gRootConsts.x ^ (id.x * 0x01010101u) ^ id.y;
}
