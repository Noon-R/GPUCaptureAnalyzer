using System.Buffers.Binary;

namespace Gcap.Shaders;

/// <summary>
/// シェーダバイトコード (DXBC / DXIL コンテナ) から「実際に使うリソースバインディング」を取り出す (tools/gcap/shaderrefl.py と同じ)。
///   DXIL: PSV0 (u32 runtimeInfoSize, PSVRuntimeInfo, u32 resourceCount, u32 bindInfoSize, PSVResourceBindInfo[])
///   DXBC: RDEF (u32 cbCount, cbOffset, bindCount, bindOffset, u8 minor, u8 major, ... / bind 32B (5.1 以降 40B))
/// 結果は (クラス 't'/'u'/'b'/'s', space, lower, upper(含む))。upper = 0xFFFFFFFF は上限なし。
/// </summary>
public static class ShaderReflection
{
    public const long Unbounded = 0xFFFFFFFF;

    public readonly record struct Binding(char Class, long Space, long Lower, long Upper);

    // PSVResourceType
    private static readonly Dictionary<uint, char> PsvClass = new()
    {
        [1] = 's', [2] = 'b', [3] = 't', [4] = 't', [5] = 't', [6] = 'u', [7] = 'u', [8] = 'u', [9] = 'u',
    };

    // D3D_SHADER_INPUT_TYPE
    private static readonly Dictionary<uint, char> RdefClass = new()
    {
        [0] = 'b', [1] = 't', [2] = 't', [3] = 's', [4] = 'u', [5] = 't', [6] = 'u', [7] = 't', [8] = 'u', [9] = 'u',
        [10] = 'u', [11] = 'u', [12] = 't', [13] = 'u',
    };

    /// <summary>シェーダが使うバインディング。判定できなければ null。</summary>
    public static List<Binding>? UsedBindings(byte[]? blob)
    {
        if (blob is null || blob.Length < 32 || blob[0] != 'D' || blob[1] != 'X' || blob[2] != 'B' || blob[3] != 'C') return null;
        var c = new DxContainer(blob);
        if (c.Parts.ContainsKey("PSV0")) return Psv0(c.Part("PSV0"));
        if (c.Parts.ContainsKey("RDEF")) return Rdef(c.Part("RDEF"));
        return null;
    }

    private static uint U32(ReadOnlySpan<byte> p, int off) => BinaryPrimitives.ReadUInt32LittleEndian(p[off..]);

    private static List<Binding>? Psv0(ReadOnlySpan<byte> p)
    {
        if (p.Length < 4) return null;
        long off = 4 + (long)U32(p, 0);
        if (off + 4 > p.Length) return null;
        uint n = U32(p, (int)off);
        off += 4;
        var outp = new List<Binding>();
        if (n == 0) return outp;
        uint stride = U32(p, (int)off);
        off += 4;
        for (uint i = 0; i < n; i++)
        {
            long o = off + i * stride;
            if (o + 16 > p.Length) return null;
            uint typ = U32(p, (int)o);
            if (PsvClass.TryGetValue(typ, out var cls))
                outp.Add(new Binding(cls, U32(p, (int)o + 4), U32(p, (int)o + 8), U32(p, (int)o + 12)));
        }
        return outp;
    }

    private static List<Binding>? Rdef(ReadOnlySpan<byte> p)
    {
        if (p.Length < 28) return null;
        uint n = U32(p, 8), boff = U32(p, 12);
        byte minor = p[16], major = p[17];
        int stride = major > 5 || (major == 5 && minor >= 1) ? 40 : 32;
        var outp = new List<Binding>();
        for (uint i = 0; i < n; i++)
        {
            long o = boff + i * stride;
            if (o + stride > p.Length) return null;
            uint typ = U32(p, (int)o + 4), point = U32(p, (int)o + 20), count = U32(p, (int)o + 24);
            long space = stride == 40 ? U32(p, (int)o + 32) : 0;
            if (RdefClass.TryGetValue(typ, out var cls))
                outp.Add(new Binding(cls, space, point, count == 0 ? Unbounded : (long)point + count - 1));
        }
        return outp;
    }

    /// <summary>register ("t3" 等) をシェーダが使うか。bindings / register が不明なら null。</summary>
    public static bool? IsUsed(List<Binding>? bindings, string? register, long? space)
    {
        if (bindings is null || string.IsNullOrEmpty(register) || "tubs".IndexOf(register[0]) < 0) return null;
        if (!long.TryParse(register.AsSpan(1), out var reg)) return null;
        char cls = register[0];
        long sp = space ?? 0;
        return bindings.Any(b => b.Class == cls && b.Space == sp && b.Lower <= reg && reg <= b.Upper);
    }
}
