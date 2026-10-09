using System.Runtime.InteropServices;

namespace Gcap.Util;

/// <summary>Windows Compression API (cabinet.dll)。PIX は XPRESS (Plain LZ77) を RAW モードで使う。</summary>
public static partial class WinCompress
{
    public const uint COMPRESS_ALGORITHM_XPRESS = 3;
    public const uint COMPRESS_RAW = 1u << 29;

    [LibraryImport("cabinet.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool CreateDecompressor(uint algorithm, nint allocationRoutines, out nint handle);

    [LibraryImport("cabinet.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static unsafe partial bool Decompress(nint handle, byte* src, nuint srcSize, byte* dst, nuint dstSize,
                                                  out nuint written);

    [ThreadStatic] private static nint _xpress;

    public static unsafe byte[] DecompressXpressRaw(ReadOnlySpan<byte> src, int rawSize)
    {
        if (_xpress == 0 && !CreateDecompressor(COMPRESS_ALGORITHM_XPRESS | COMPRESS_RAW, 0, out _xpress))
            throw new InvalidOperationException($"CreateDecompressor failed ({Marshal.GetLastPInvokeError()})");
        var dst = new byte[rawSize];
        fixed (byte* s = src)
        fixed (byte* d = dst)
        {
            if (!Decompress(_xpress, s, (nuint)src.Length, d, (nuint)rawSize, out var written))
                throw new InvalidOperationException($"Decompress failed ({Marshal.GetLastPInvokeError()})");
            if ((int)written != rawSize) Array.Resize(ref dst, (int)written);
        }
        return dst;
    }
}
