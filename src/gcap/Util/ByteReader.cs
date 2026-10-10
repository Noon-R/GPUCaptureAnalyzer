using System.Buffers.Binary;
using System.Text;

namespace Gcap.Util;

public sealed class DecodeException(string message) : Exception(message);

/// <summary>リトルエンディアンの逐次リーダ (PIX ペイロードはパディングなし)。</summary>
public sealed class ByteReader
{
    private readonly byte[] _data;
    private readonly int _start;
    private readonly int _end;
    private int _pos;

    public ByteReader(byte[] data, int start = 0, int length = -1)
    {
        _data = data;
        _start = start;
        _end = length < 0 ? data.Length : start + length;
        _pos = start;
    }

    public int Pos => _pos - _start;
    public int Remaining => _end - _pos;
    public int Length => _end - _start;

    private void Need(long n)
    {
        if (n < 0 || n > Remaining)
            throw new DecodeException($"read past end (pos={Pos}, n={n}, len={Length})");
    }

    public byte[] Take(ulong n) => Take(n > int.MaxValue ? -1L : (long)n);

    public void Skip(long n)
    {
        Need(n);
        _pos += (int)n;
    }

    public byte[] Take(long n)
    {
        Need(n);
        var b = new byte[n];
        Buffer.BlockCopy(_data, _pos, b, 0, (int)n);
        _pos += (int)n;
        return b;
    }

    private ReadOnlySpan<byte> Span(int n)
    {
        Need(n);
        var s = new ReadOnlySpan<byte>(_data, _pos, n);
        _pos += n;
        return s;
    }

    public byte U8() => Span(1)[0];
    public ushort U16() => BinaryPrimitives.ReadUInt16LittleEndian(Span(2));
    public uint U32() => BinaryPrimitives.ReadUInt32LittleEndian(Span(4));
    public int I32() => BinaryPrimitives.ReadInt32LittleEndian(Span(4));
    public ulong U64() => BinaryPrimitives.ReadUInt64LittleEndian(Span(8));
    public long I64() => BinaryPrimitives.ReadInt64LittleEndian(Span(8));
    public float F32() => BinaryPrimitives.ReadSingleLittleEndian(Span(4));
    public double F64() => BinaryPrimitives.ReadDoubleLittleEndian(Span(8));
    public string Guid() => new Guid(Span(16)).ToString("D");

    public bool Flag()
    {
        byte v = U8();
        if (v > 1) throw new DecodeException($"invalid presence flag 0x{v:x} at {Pos - 1}");
        return v == 1;
    }

    public string WStr(ulong n)
    {
        if (n > (ulong)Remaining / 2) Need(-1);
        return Encoding.Unicode.GetString(Span((int)n * 2));
    }

    public string AStr(ulong n)
    {
        if (n > (ulong)Remaining) Need(-1);
        return Encoding.Latin1.GetString(Span((int)n));
    }
}
