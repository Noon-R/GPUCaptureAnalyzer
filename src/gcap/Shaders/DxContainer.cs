using System.Buffers.Binary;
using System.Text;

namespace Gcap.Shaders;

/// <summary>
/// DXBC/DXIL シェーダコンテナ ("DXBC" ヘッダ + パート列) の読み取り。
///   header: "DXBC" u8[16] checksum u32 version u32 size u32 partCount u32 partOffsets[]
///   part  : char[4] fourcc u32 size data
/// シンボル照合に使うパート:
///   HASH: u32 flags + u8[16] digest   (dxc のシェーダハッシュ。-Fd dir\ の PDB 名 = digest の hex)
///   ILDN: u16 flags + u16 nameLength + char name[] (PDB のデバッグ名)
///   DXIL / SHEX / SHDR: 本体 (DXIL = SM6, SHEX/SHDR = SM5 以前の DXBC)
/// </summary>
public sealed class DxContainer
{
    public byte[] Bytes { get; }
    public Dictionary<string, (int offset, int size)> Parts { get; } = [];
    public bool Valid { get; }

    public DxContainer(byte[] bytes)
    {
        Bytes = bytes;
        if (bytes.Length < 32 || Encoding.ASCII.GetString(bytes, 0, 4) != "DXBC") return;
        uint count = BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(28));
        if (32 + 4L * count > bytes.Length) return;
        for (int i = 0; i < count; i++)
        {
            int off = (int)BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(32 + 4 * i));
            if (off + 8 > bytes.Length) return;
            var tag = Encoding.ASCII.GetString(bytes, off, 4);
            int size = (int)BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(off + 4));
            if (off + 8 + (long)size > bytes.Length) return;
            Parts.TryAdd(tag, (off + 8, size));
        }
        Valid = true;
    }

    public ReadOnlySpan<byte> Part(string tag) => Parts.TryGetValue(tag, out var p) ? Bytes.AsSpan(p.offset, p.size) : default;

    public bool IsDxil => Parts.ContainsKey("DXIL") || Parts.ContainsKey("ILDB");
    public string Kind => IsDxil ? "dxil" : "dxbc";

    /// <summary>HASH パートの digest (16 bytes, hex 小文字)。無ければ null。</summary>
    public string? HashHex => Part("HASH") is { Length: >= 20 } h ? Convert.ToHexStringLower(h.Slice(4, 16)) : null;
    public byte[]? HashDigest => Part("HASH") is { Length: >= 20 } h ? h.Slice(4, 16).ToArray() : null;

    /// <summary>ILDN パートのデバッグ名 (PDB ファイル名)。</summary>
    public string? DebugName
    {
        get
        {
            var p = Part("ILDN");
            if (p.Length < 4) return null;
            int len = BinaryPrimitives.ReadUInt16LittleEndian(p[2..]);
            return len > 0 && 4 + len <= p.Length ? Encoding.UTF8.GetString(p.Slice(4, len)) : null;
        }
    }

    /// <summary>デバッグ情報が埋め込まれているか (ILDB = DXIL debug / SDBG/SPDB = DXBC debug)。</summary>
    public bool HasEmbeddedDebug => Parts.ContainsKey("ILDB") || Parts.ContainsKey("SDBG") || Parts.ContainsKey("SPDB");
}
