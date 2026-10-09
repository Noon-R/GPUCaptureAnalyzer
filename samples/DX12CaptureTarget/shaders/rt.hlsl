// GCA_RT_SHADER_SOURCE_MARKER
// --extras 用 DXR ライブラリ (lib_6_3)
RaytracingAccelerationStructure gScene  : register(t0);
RWTexture2D<float4>             gOutput : register(u0);
cbuffer RtConsts : register(b0)
{
    float4 gMissColor;
};

struct Payload
{
    float4 color;
};

[shader("raygeneration")]
void RayGen()
{
    uint2 idx = DispatchRaysIndex().xy;
    uint2 dim = DispatchRaysDimensions().xy;
    float2 uv = (float2(idx) + 0.5) / float2(dim);
    RayDesc ray;
    ray.Origin = float3(uv * 2.0 - 1.0, -1.0);
    ray.Direction = float3(0.0, 0.0, 1.0);
    ray.TMin = 0.0;
    ray.TMax = 100.0;
    Payload p;
    p.color = float4(0, 0, 0, 0);
    TraceRay(gScene, RAY_FLAG_NONE, 0xFF, 0, 1, 0, ray, p);
    gOutput[idx] = p.color;
}

[shader("miss")]
void Miss(inout Payload p)
{
    p.color = gMissColor;
}

[shader("closesthit")]
void ClosestHit(inout Payload p, BuiltInTriangleIntersectionAttributes attr)
{
    p.color = float4(attr.barycentrics, InstanceID() / 255.0, 1.0);
}
