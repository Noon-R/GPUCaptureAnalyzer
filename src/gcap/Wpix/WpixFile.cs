using System.Buffers.Binary;
using System.Text;
using Gcap.Util;
using Microsoft.Win32.SafeHandles;

namespace Gcap.Wpix;

// PIX GPU capture (.wpix) コンテナ層。仕様は docs/formats/wpix.md §1。
//
//   +0x000      ファイルヘッダ (0x400): "GFXA" u32 0xFFFFFFFF "GFXB" u32 u32 ...
//   +0x400      セクション "<CAP" ... "CAP>" (DIR1 エントリが指す)
//   ...         DIR1 テーブル + フッタ32B(u8[16], u64 tableBytes, u32 count, "DIR0")
//   end-0x400   ファイルトレーラ (0x400, 末尾 "GFXBGFXA")
//   セクション内: ブロック列 / DIR3 テーブル + フッタ(DIR2) / トレーラ 0x40 (末尾 "CAP>")
//   BLOKCOMP: "BLOKCOMP" u64 size "CBLKDATA" u64 rawSize + XPRESS(RAW) / BLOKDATA: "BLOKDATA" u64 size + raw
//
// 実ゲームのキャプチャではストリーム (リソース初期データ等) が展開後 2GB を超えるため、
// ファイルもストリームも丸ごとメモリに置かず、ブロック単位で必要な範囲だけ展開する。

public sealed class WpixFormatException(string message) : Exception(message);

public sealed record DirEntry(string Tag, uint Id, ulong Offset, ulong Size, ulong Reserved);

public sealed record Block(long Offset, ulong CompSize, ulong RawSize, string Kind, long DataOffset, long DataSize);

public sealed class WpixStream
{
    public required uint Id { get; init; }
    public required DirEntry Entry { get; init; }
    public required WpixFile File { get; init; }
    public List<Block> Blocks { get; } = [];
    private long[] _starts = [];          // 各ブロックの展開後の開始オフセット
    private int _cacheIdx = -1;           // 直近に展開したブロック (レコードは順に読まれるので 1 個で十分)
    private byte[] _cache = [];

    /// <summary>展開後のバイト数。</summary>
    public long Length { get; private set; }

    internal void Seal()
    {
        _starts = new long[Blocks.Count];
        long acc = 0;
        for (int i = 0; i < Blocks.Count; i++) { _starts[i] = acc; acc += (long)Blocks[i].RawSize; }
        Length = acc;
    }

    /// <summary>展開後のストリーム中の [offset, offset+length) を返す (ブロックをまたいでよい)。</summary>
    public byte[] Read(long offset, long length)
    {
        if (offset < 0 || length < 0 || offset + length > Length)
            throw new WpixFormatException($"stream {Id}: read 0x{offset:x}+0x{length:x} past end 0x{Length:x}");
        if (length > Array.MaxLength) throw new WpixFormatException($"stream {Id}: record too large (0x{length:x})");
        var dst = new byte[length];
        long done = 0;
        int bi = Array.BinarySearch(_starts, offset);
        if (bi < 0) bi = ~bi - 1;
        while (done < length)
        {
            var blk = BlockData(bi);
            long inBlk = offset + done - _starts[bi];
            long n = Math.Min(blk.Length - inBlk, length - done);
            Buffer.BlockCopy(blk, (int)inBlk, dst, (int)done, (int)n);
            done += n;
            bi++;
        }
        return dst;
    }

    private byte[] BlockData(int i)
    {
        if (i != _cacheIdx) { _cache = File.DecompressBlock(Blocks[i]); _cacheIdx = i; }
        return _cache;
    }
}

public sealed class Section
{
    public required string Tag { get; init; }
    public required DirEntry Entry { get; init; }
    public required uint Version { get; init; }
    public List<WpixStream> Streams { get; } = [];
}

public sealed class WpixFile : IDisposable
{
    public string Path { get; }
    public long Length { get; }
    public uint HeaderU32_0x0C { get; }
    public uint HeaderU32_0x10 { get; }
    public (uint, uint) HeaderU32_0x40 { get; }
    public List<DirEntry> TopEntries { get; }
    public List<Section> Sections { get; } = [];
    private readonly SafeFileHandle _h;

    public IEnumerable<WpixStream> Streams => Sections.SelectMany(s => s.Streams);

    public WpixFile(string path)
    {
        Path = path;
        _h = System.IO.File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        Length = RandomAccess.GetLength(_h);
        if (Length < 0x800 || Tag(0) != "GFXA" || Tag(8) != "GFXB")
            throw new WpixFormatException("not a wpix file (missing GFXA/GFXB header)");
        if (Tag(Length - 8, 8) != "GFXBGFXA")
            throw new WpixFormatException("missing GFXBGFXA trailer");
        HeaderU32_0x0C = U32(0x0C);
        HeaderU32_0x10 = U32(0x10);
        HeaderU32_0x40 = (U32(0x40), U32(0x44));
        TopEntries = ReadDirTable(Length - 0x400, "DIR0");
        foreach (var e in TopEntries) Sections.Add(ParseSection(e));
    }

    public void Dispose() => _h.Dispose();

    private byte[] ReadAt(long off, int len)
    {
        var b = new byte[len];
        int got = 0;
        while (got < len)
        {
            int n = RandomAccess.Read(_h, b.AsSpan(got), off + got);
            if (n <= 0) throw new WpixFormatException($"unexpected end of file at 0x{off + got:x}");
            got += n;
        }
        return b;
    }

    private string Tag(long off, int len = 4) => Encoding.Latin1.GetString(ReadAt(off, len));
    private uint U32(long off) => BinaryPrimitives.ReadUInt32LittleEndian(ReadAt(off, 4));
    private ulong U64(long off) => BinaryPrimitives.ReadUInt64LittleEndian(ReadAt(off, 8));

    /// <summary>footerEnd (フッタ直後) から逆に読む。フッタ 32B: u8[16], u64 tableBytes, u32 count, tag</summary>
    private List<DirEntry> ReadDirTable(long footerEnd, string expectTag)
    {
        var tag = Tag(footerEnd - 4);
        if (tag != expectTag) throw new WpixFormatException($"expected footer {expectTag} at 0x{footerEnd - 4:x}, got {tag}");
        ulong tableBytes = U64(footerEnd - 16);
        uint count = U32(footerEnd - 8);
        if (tableBytes != count * 32UL) throw new WpixFormatException($"table size mismatch 0x{tableBytes:x} != {count}*32");
        long start = footerEnd - 32 - (long)tableBytes;
        var t = ReadAt(start, (int)tableBytes);
        var list = new List<DirEntry>();
        for (int i = 0; i < count; i++)
        {
            var s = t.AsSpan(i * 32);
            list.Add(new DirEntry(Encoding.Latin1.GetString(s[..4]), BinaryPrimitives.ReadUInt32LittleEndian(s[4..]),
                                  BinaryPrimitives.ReadUInt64LittleEndian(s[8..]), BinaryPrimitives.ReadUInt64LittleEndian(s[16..]),
                                  BinaryPrimitives.ReadUInt64LittleEndian(s[24..])));
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
        // 同じストリーム ID のエントリが複数あることがある (大きいデータ/複数フレーム)。出現順に連結する
        // (レコードはエントリ境界をまたいで続く)
        foreach (var de in ReadDirTable(end - 0x40, "DIR2"))
        {
            var st = sec.Streams.FirstOrDefault(x => x.Id == de.Id);
            if (st is null) sec.Streams.Add(st = new WpixStream { Id = de.Id, Entry = de, File = this });
            ParseBlocks(st, (long)de.Offset, (long)(de.Offset + de.Size));
        }
        foreach (var st in sec.Streams) st.Seal();
        return sec;
    }

    private void ParseBlocks(WpixStream st, long start, long end)
    {
        long pos = start;
        while (pos < end)
        {
            var h = ReadAt(pos, 32);
            var magic = Encoding.Latin1.GetString(h, 0, 8);
            ulong size = BinaryPrimitives.ReadUInt64LittleEndian(h.AsSpan(8));
            if (magic == "BLOKCOMP")
                st.Blocks.Add(new Block(pos, size, BinaryPrimitives.ReadUInt64LittleEndian(h.AsSpan(24)),
                                        Encoding.Latin1.GetString(h, 16, 8), pos + 32, (long)size - 16));
            else if (magic == "BLOKDATA") // 非圧縮 (PNG スクリーンショット等)
                st.Blocks.Add(new Block(pos, size, size, "RAW", pos + 16, (long)size));
            else
                throw new WpixFormatException($"expected BLOKCOMP/BLOKDATA at 0x{pos:x}, got {magic}");
            pos += 16 + (long)size;
        }
        if (pos != end) throw new WpixFormatException($"block chain overran stream end (0x{pos:x} != 0x{end:x})");
    }

    internal byte[] DecompressBlock(Block blk)
    {
        var payload = ReadAt(blk.DataOffset, checked((int)blk.DataSize));
        if (blk.Kind == "RAW") return payload;
        if (blk.Kind != "CBLKDATA") throw new WpixFormatException($"unknown block kind {blk.Kind}");
        var outp = WinCompress.DecompressXpressRaw(payload, checked((int)blk.RawSize));
        if ((ulong)outp.Length != blk.RawSize) throw new WpixFormatException($"decompressed size mismatch at 0x{blk.Offset:x}");
        return outp;
    }
}

// <EVT レコード: "<EVT" u32 type, u64 seq, u64 payloadSize, u8[40] reserved, payload ("META" u32[5] + body)
// ペイロードは必要になった時に Source から読む (保持しない)
public sealed record EvtRecord(uint StreamId, long Offset, uint Type, ulong Seq, long PayloadSize, WpixStream Source)
{
    public const int HeaderSize = 0x40;
    public const int MetaSize = 24;

    public byte[] Payload => Source.Read(Offset + HeaderSize, PayloadSize);

    /// <summary>ペイロード中の body (META の後) の、ストリーム上の開始オフセット。</summary>
    public long BodyOffset => Offset + HeaderSize + MetaSize;

    public static IEnumerable<EvtRecord> Iterate(WpixStream s)
    {
        long pos = 0;
        while (pos < s.Length)
        {
            var h = s.Read(pos, HeaderSize);
            if (Encoding.Latin1.GetString(h, 0, 4) != "<EVT")
                throw new WpixFormatException($"stream {s.Id}: expected <EVT at 0x{pos:x}");
            uint type = BinaryPrimitives.ReadUInt32LittleEndian(h.AsSpan(4));
            ulong seq = BinaryPrimitives.ReadUInt64LittleEndian(h.AsSpan(8));
            long psize = (long)BinaryPrimitives.ReadUInt64LittleEndian(h.AsSpan(16));
            yield return new EvtRecord(s.Id, pos, type, seq, psize, s);
            pos += HeaderSize + psize;
        }
    }

    public static List<EvtRecord> All(WpixFile f) =>
        f.Streams.SelectMany(Iterate).OrderBy(r => r.Seq).ToList(); // 安定ソート
}
