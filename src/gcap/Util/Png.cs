using System.Buffers.Binary;
using System.IO.Compression;

namespace Gcap.Util;

/// <summary>依存ライブラリなしで RGBA8 画像を PNG に書き出す。</summary>
public static class Png
{
    public static void WriteRgba(string path, int width, int height, IReadOnlyList<byte[]> rows)
    {
        using var raw = new MemoryStream();
        foreach (var row in rows)
        {
            raw.WriteByte(0);
            raw.Write(row, 0, Math.Min(row.Length, width * 4));
        }
        byte[] idat;
        using (var ms = new MemoryStream())
        {
            using (var z = new ZLibStream(ms, CompressionLevel.Optimal, leaveOpen: true)) z.Write(raw.ToArray());
            idat = ms.ToArray();
        }
        using var f = File.Create(path);
        f.Write([0x89, (byte)'P', (byte)'N', (byte)'G', 0x0D, 0x0A, 0x1A, 0x0A]);
        var ihdr = new byte[13];
        BinaryPrimitives.WriteInt32BigEndian(ihdr, width);
        BinaryPrimitives.WriteInt32BigEndian(ihdr.AsSpan(4), height);
        ihdr[8] = 8; ihdr[9] = 6; // 8bit RGBA
        Chunk(f, "IHDR", ihdr);
        Chunk(f, "IDAT", idat);
        Chunk(f, "IEND", []);
    }

    private static readonly uint[] CrcTable = Enumerable.Range(0, 256).Select(n =>
    {
        uint c = (uint)n;
        for (int k = 0; k < 8; k++) c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        return c;
    }).ToArray();

    private static uint Crc32(ReadOnlySpan<byte> data)
    {
        uint c = 0xFFFFFFFFu;
        foreach (var b in data) c = CrcTable[(c ^ b) & 0xFF] ^ (c >> 8);
        return c ^ 0xFFFFFFFFu;
    }

    private static void Chunk(Stream s, string tag, byte[] data)
    {
        Span<byte> len = stackalloc byte[4];
        BinaryPrimitives.WriteInt32BigEndian(len, data.Length);
        s.Write(len);
        var td = new byte[4 + data.Length];
        System.Text.Encoding.ASCII.GetBytes(tag, td);
        data.CopyTo(td, 4);
        s.Write(td);
        Span<byte> crc = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(crc, Crc32(td));
        s.Write(crc);
    }
}
