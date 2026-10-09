using System.Buffers.Binary;
using Gcap.Util;

namespace Gcap;

/// <summary>
/// シリアライズ済みルートシグネチャ (DXBC コンテナ内の RTS0 チャンク) のパーサ。
/// RTS0: u32 Version, NumParameters, ParametersOffset, NumStaticSamplers, StaticSamplersOffset, Flags
///       Parameter: u32 Type, Visibility, PayloadOffset (オフセットは RTS0 先頭相対)
/// </summary>
public static class RootSig
{
    private static readonly string[] ParamTypes = ["DescriptorTable", "32BitConstants", "CBV", "SRV", "UAV"];
    private static readonly string[] RangeTypes = ["SRV", "UAV", "CBV", "Sampler"];
    private static readonly string[] Visibility = ["ALL", "VERTEX", "HULL", "DOMAIN", "GEOMETRY", "PIXEL", "AMPLIFICATION", "MESH"];
    private const uint Append = 0xFFFFFFFF;

    private static object Name(string[] names, uint v) => v < names.Length ? names[v] : (long)v;

    public static byte[]? FindChunk(byte[] blob, string tag)
    {
        if (blob.Length < 32 || System.Text.Encoding.Latin1.GetString(blob, 0, 4) != "DXBC") return null;
        uint count = BinaryPrimitives.ReadUInt32LittleEndian(blob.AsSpan(28));
        for (int i = 0; i < count; i++)
        {
            int off = (int)BinaryPrimitives.ReadUInt32LittleEndian(blob.AsSpan(32 + 4 * i));
            if (System.Text.Encoding.Latin1.GetString(blob, off, 4) == tag)
            {
                int size = (int)BinaryPrimitives.ReadUInt32LittleEndian(blob.AsSpan(off + 4));
                return blob.AsSpan(off + 8, size).ToArray();
            }
        }
        return null;
    }

    public static Obj Parse(byte[] blob)
    {
        var rts = FindChunk(blob, "RTS0") ?? throw new FormatException("RTS0 chunk not found");
        uint U(int o) => BinaryPrimitives.ReadUInt32LittleEndian(rts.AsSpan(o));
        uint ver = U(0), nparam = U(4), poff = U(8), nsamp = U(12), soff = U(16), flags = U(20);
        var pars = new List<object?>();
        for (int i = 0; i < nparam; i++)
        {
            int b = (int)poff + 12 * i;
            uint ptype = U(b), vis = U(b + 4), payload = U(b + 8);
            var p = new Obj { ["type"] = Name(ParamTypes, ptype), ["visibility"] = Name(Visibility, vis) };
            if (ptype == 0)
            {
                uint nr = U((int)payload), roff = U((int)payload + 4);
                int rsize = ver == 1 ? 20 : 24;
                var ranges = new List<object?>();
                long cursor = 0;
                for (int j = 0; j < nr; j++)
                {
                    int ro = (int)roff + rsize * j;
                    uint rt = U(ro), num = U(ro + 4), bas = U(ro + 8), space = U(ro + 12);
                    uint offset = ver == 1 ? U(ro + 16) : U(ro + 20);
                    long off = offset == Append ? cursor : offset;
                    cursor = off + (num != 0xFFFFFFFF ? num : 0);
                    var rg = new Obj
                    {
                        ["type"] = Name(RangeTypes, rt), ["num"] = (long)num, ["baseRegister"] = (long)bas,
                        ["space"] = (long)space, ["offset"] = off,
                    };
                    if (ver != 1) rg["flags"] = (long)U(ro + 16);
                    ranges.Add(rg);
                }
                p["ranges"] = ranges;
            }
            else if (ptype == 1)
            {
                p["register"] = (long)U((int)payload); p["space"] = (long)U((int)payload + 4); p["num32BitValues"] = (long)U((int)payload + 8);
            }
            else
            {
                p["register"] = (long)U((int)payload); p["space"] = (long)U((int)payload + 4);
            }
            pars.Add(p);
        }
        var samplers = new List<object?>();
        for (int i = 0; i < nsamp; i++)
        {
            int s = (int)soff + 52 * i;
            samplers.Add(new Obj
            {
                ["filter"] = (long)U(s), ["address"] = new List<object?> { (long)U(s + 4), (long)U(s + 8), (long)U(s + 12) },
                ["register"] = (long)U(s + 40), ["space"] = (long)U(s + 44), ["visibility"] = Name(Visibility, U(s + 48)),
            });
        }
        return new Obj { ["version"] = (long)ver, ["flags"] = (long)flags, ["parameters"] = pars, ["staticSamplers"] = samplers };
    }
}
