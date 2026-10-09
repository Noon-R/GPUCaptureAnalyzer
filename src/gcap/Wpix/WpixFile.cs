using System.Buffers.Binary;
using System.Text;
using Gcap.Util;

namespace Gcap.Wpix;

// PIX GPU capture (.wpix) コンテナ層。仕様は docs/formats/wpix.md §1。
//
//   +0x000      ファイルヘッダ (0x400): "GFXA" u32 0xFFFFFFFF "GFXB" u32 u32 ...
//   +0x400      セクション "<CAP" ... "CAP>" (DIR1 エントリが指す)
//   ...         DIR1 テーブル + フッタ32B(u8[16], u64 tableBytes, u32 count, "DIR0")
//   end-0x400   ファイルトレーラ (0x400, 末尾 "GFXBGFXA")
//   セクション内: ブロック列 / DIR3 テーブル + フッタ(DIR2) / トレーラ 0x40 (末尾 "CAP>")
//   BLOKCOMP: "BLOKCOMP" u64 size "CBLKDATA" u64 rawSize + XPRESS(RAW) / BLOKDATA: "BLOKDATA" u64 size + raw

public sealed class WpixFormatException(string message) : Exception(message);

public sealed record DirEntry(string Tag, uint Id, ulong Offset, ulong Size, ulong Reserved);

public sealed record Block(long Offset, ulong CompSize, ulong RawSize, string Kind, long DataOffset, long DataSize);

public sealed class WpixStream
{
    public required uint Id { get; init; }
    public required DirEntry Entry { get; init; }
    public List<Block> Blocks { get; } = [];
    public byte[] Data { get; set; } = [];
}

public sealed class Section
{
    public required string Tag { get; init; }
    public required DirEntry Entry { get; init; }
    public required uint Version { get; init; }
    public List<WpixStream> Streams { get; } = [];
}

public sealed class WpixFile
{
    public string Path { get; }
    public byte[] Buf { get; }
    public uint HeaderU32_0x0C { get; }
    public uint HeaderU32_0x10 { get; }
    public (uint, uint) HeaderU32_0x40 { get; }
    public List<DirEntry> TopEntries { get; }
    public List<Section> Sections { get; } = [];

    public IEnumerable<WpixStream> Streams => Sections.SelectMany(s => s.Streams);

    public WpixFile(string path)
    {
        Path = path;
        Buf = File.ReadAllBytes(path);
        var b = Buf;
        if (b.Length < 0x800 || Tag(0) != "GFXA" || Tag(8) != "GFXB")
            throw new WpixFormatException("not a wpix file (missing GFXA/GFXB header)");
        if (Encoding.Latin1.GetString(b, b.Length - 8, 8) != "GFXBGFXA")
            throw new WpixFormatException("missing GFXBGFXA trailer");
        HeaderU32_0x0C = U32(0x0C);
        HeaderU32_0x10 = U32(0x10);
        HeaderU32_0x40 = (U32(0x40), U32(0x44));
        TopEntries = ReadDirTable(b.Length - 0x400, "DIR0");
        foreach (var e in TopEntries) Sections.Add(ParseSection(e));
    }

    private string Tag(long off, int len = 4) => Encoding.Latin1.GetString(Buf, (int)off, len);
    private uint U32(long off) => BinaryPrimitives.ReadUInt32LittleEndian(Buf.AsSpan((int)off));
    private ulong U64(long off) => BinaryPrimitives.ReadUInt64LittleEndian(Buf.AsSpan((int)off));

    /// <summary>footerEnd (フッタ直後) から逆に読む。フッタ 32B: u8[16], u64 tableBytes, u32 count, tag</summary>
    private List<DirEntry> ReadDirTable(long footerEnd, string expectTag)
    {
        var tag = Tag(footerEnd - 4);
        if (tag != expectTag) throw new WpixFormatException($"expected footer {expectTag} at 0x{footerEnd - 4:x}, got {tag}");
        ulong tableBytes = U64(footerEnd - 16);
        uint count = U32(footerEnd - 8);
        if (tableBytes != count * 32UL) throw new WpixFormatException($"table size mismatch 0x{tableBytes:x} != {count}*32");
        long start = footerEnd - 32 - (long)tableBytes;
        var list = new List<DirEntry>();
        for (int i = 0; i < count; i++)
        {
            long o = start + i * 32;
            list.Add(new DirEntry(Tag(o), U32(o + 4), U64(o + 8), U64(o + 16), U64(o + 24)));
        }
        return list;
    }

    private Section ParseSection(DirEntry e)
    {
        long start = (long)e.Offset, end = (long)(e.Offset + e.Size);
        var head = Tag(start);
        if (head[0] != '<') throw new WpixFormatException($"section at 0x{start:x} has unexpected tag {head}");
        var name = head[1..];
        if (Tag(end - 4) != name + ">") throw new WpixFormatException($"section {name} end tag mismatch at 0x{end - 4:x}");
        var sec = new Section { Tag = name, Entry = e, Version = U32(start + 4) };
        foreach (var de in ReadDirTable(end - 0x40, "DIR2"))
        {
            var st = new WpixStream { Id = de.Id, Entry = de };
            ParseBlocks(st, (long)de.Offset, (long)(de.Offset + de.Size));
            using var ms = new MemoryStream();
            foreach (var blk in st.Blocks) ms.Write(DecompressBlock(blk));
            st.Data = ms.ToArray();
            sec.Streams.Add(st);
        }
        return sec;
    }

    private void ParseBlocks(WpixStream st, long start, long end)
    {
        long pos = start;
        while (pos < end)
        {
            var magic = Tag(pos, 8);
            ulong size = U64(pos + 8);
            if (magic == "BLOKCOMP")
                st.Blocks.Add(new Block(pos, size, U64(pos + 24), Tag(pos + 16, 8), pos + 32, (long)size - 16));
            else if (magic == "BLOKDATA") // 非圧縮 (PNG スクリーンショット等)
                st.Blocks.Add(new Block(pos, size, size, "RAW", pos + 16, (long)size));
            else
                throw new WpixFormatException($"expected BLOKCOMP/BLOKDATA at 0x{pos:x}, got {magic}");
            pos += 16 + (long)size;
        }
        if (pos != end) throw new WpixFormatException($"block chain overran stream end (0x{pos:x} != 0x{end:x})");
    }

    private byte[] DecompressBlock(Block blk)
    {
        var payload = Buf.AsSpan((int)blk.DataOffset, (int)blk.DataSize);
        if (blk.Kind == "RAW") return payload.ToArray();
        if (blk.Kind != "CBLKDATA") throw new WpixFormatException($"unknown block kind {blk.Kind}");
        var outp = WinCompress.DecompressXpressRaw(payload, (int)blk.RawSize);
        if ((ulong)outp.Length != blk.RawSize) throw new WpixFormatException($"decompressed size mismatch at 0x{blk.Offset:x}");
        return outp;
    }
}

// <EVT レコード: "<EVT" u32 type, u64 seq, u64 payloadSize, u8[40] reserved, payload ("META" u32[5] + body)
public sealed record EvtRecord(uint StreamId, int Offset, uint Type, ulong Seq, byte[] Payload)
{
    public const int HeaderSize = 0x40;
    public const int MetaSize = 24;

    public static IEnumerable<EvtRecord> Iterate(uint streamId, byte[] data)
    {
        int pos = 0;
        while (pos < data.Length)
        {
            if (Encoding.Latin1.GetString(data, pos, 4) != "<EVT")
                throw new WpixFormatException($"stream {streamId}: expected <EVT at 0x{pos:x}");
            uint type = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(pos + 4));
            ulong seq = BinaryPrimitives.ReadUInt64LittleEndian(data.AsSpan(pos + 8));
            ulong psize = BinaryPrimitives.ReadUInt64LittleEndian(data.AsSpan(pos + 16));
            var payload = data.AsSpan(pos + HeaderSize, (int)psize).ToArray();
            yield return new EvtRecord(streamId, pos, type, seq, payload);
            pos += HeaderSize + (int)psize;
        }
    }

    public static List<EvtRecord> All(WpixFile f) =>
        f.Streams.SelectMany(s => Iterate(s.Id, s.Data)).OrderBy(r => r.Seq).ToList(); // 安定ソート
}
