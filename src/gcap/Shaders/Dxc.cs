using System.Runtime.InteropServices;
using System.Text;

namespace Gcap.Shaders;

/// <summary>
/// dxcompiler.dll (DXC) を実行時ロードして使う: DXIL の逆アセンブル、PDB 情報 (ハッシュ/ソース/引数) の取得。
/// COM は vtable を関数ポインタで直接呼ぶ (スロット番号は Windows SDK の dxcapi.h の宣言順)。
/// DXBC (SM5 以前) の逆アセンブルは d3dcompiler_47.dll の D3DDisassemble を使う。
/// </summary>
public sealed unsafe partial class Dxc : IDisposable
{
    private static readonly Guid CLSID_DxcCompiler = new("73e22d93-e6ce-47f3-b5bf-f0664f39c1b0");
    private static readonly Guid CLSID_DxcLibrary = new("6245d6af-66e0-48fd-80b4-4d271796748c");
    private static readonly Guid CLSID_DxcPdbUtils = new("54621dfb-f2ce-457e-ae8c-ec355faeec7c");
    private static readonly Guid IID_IDxcCompiler = new("8c210bf3-011f-4422-8d70-6f9acb8db617");
    private static readonly Guid IID_IDxcLibrary = new("e5204dc7-d18c-4c3c-bdfb-851673980fe7");
    private static readonly Guid IID_IDxcPdbUtils = new("E6C9647E-9D6A-4C3B-B94C-524B5A6C343D");

    private readonly nint _module;
    private readonly delegate* unmanaged[Stdcall]<Guid*, Guid*, nint*, int> _create;
    private nint _library, _compiler, _pdbUtils;
    public string Path { get; }

    private Dxc(nint module, string path)
    {
        _module = module;
        Path = path;
        _create = (delegate* unmanaged[Stdcall]<Guid*, Guid*, nint*, int>)NativeLibrary.GetExport(module, "DxcCreateInstance");
    }

    /// <summary>dxcompiler.dll を探してロードする (GCAP_DXCOMPILER → exe と同じ場所 → Windows SDK → PIX)。</summary>
    public static Dxc? TryLoad()
    {
        foreach (var p in Candidates())
        {
            if (!File.Exists(p)) continue;
            try { return new Dxc(NativeLibrary.Load(p), p); }
            catch (Exception e) when (e is DllNotFoundException or BadImageFormatException or EntryPointNotFoundException) { }
        }
        return null;
    }

    private static IEnumerable<string> Candidates()
    {
        var env = Environment.GetEnvironmentVariable("GCAP_DXCOMPILER");
        if (!string.IsNullOrEmpty(env)) yield return env;
        yield return System.IO.Path.Combine(AppContext.BaseDirectory, "dxcompiler.dll");
        var kits = @"C:\Program Files (x86)\Windows Kits\10\bin";
        if (Directory.Exists(kits))
            foreach (var d in Directory.GetDirectories(kits, "10.*").OrderByDescending(x => x, StringComparer.Ordinal))
                yield return System.IO.Path.Combine(d, "x64", "dxcompiler.dll");
        var pix = @"C:\Program Files\Microsoft PIX";
        if (Directory.Exists(pix))
            foreach (var d in Directory.GetDirectories(pix).OrderByDescending(x => x, StringComparer.Ordinal))
                yield return System.IO.Path.Combine(d, "dxcompiler.dll");
    }

    // ---- COM helpers ----------------------------------------------------------------------------
    private static void* Slot(nint obj, int i) => (*(void***)obj)[i];
    private static void Release(nint obj) { if (obj != 0) ((delegate* unmanaged[Stdcall]<nint, uint>)Slot(obj, 2))(obj); }
    private static void Check(int hr, string what) { if (hr < 0) throw new COMException($"{what} failed (0x{hr:x8})", hr); }

    private nint Create(Guid clsid, Guid iid)
    {
        nint p;
        Check(_create(&clsid, &iid, &p), "DxcCreateInstance");
        return p;
    }

    private static byte[] BlobBytes(nint blob)
    {
        var ptr = ((delegate* unmanaged[Stdcall]<nint, nint>)Slot(blob, 3))(blob);
        var size = ((delegate* unmanaged[Stdcall]<nint, nuint>)Slot(blob, 4))(blob);
        var b = new byte[(int)size];
        Marshal.Copy(ptr, b, 0, b.Length);
        return b;
    }

    private nint MakeBlob(byte[] data)
    {
        if (_library == 0) _library = Create(CLSID_DxcLibrary, IID_IDxcLibrary);
        nint blob;
        fixed (byte* p = data)
            // IDxcLibrary::CreateBlobWithEncodingOnHeapCopy (slot 7): データはコピーされる
            Check(((delegate* unmanaged[Stdcall]<nint, void*, uint, uint, nint*, int>)Slot(_library, 7))(_library, p, (uint)data.Length, 0, &blob),
                  "CreateBlobWithEncodingOnHeapCopy");
        return blob;
    }

    private static string? Bstr(nint obj, int slot)
    {
        nint b = 0;
        int hr = ((delegate* unmanaged[Stdcall]<nint, nint*, int>)Slot(obj, slot))(obj, &b);
        if (hr < 0 || b == 0) return null;
        var s = Marshal.PtrToStringBSTR(b);
        Marshal.FreeBSTR(b);
        return s;
    }

    private static string? BstrAt(nint obj, int slot, uint index)
    {
        nint b = 0;
        int hr = ((delegate* unmanaged[Stdcall]<nint, uint, nint*, int>)Slot(obj, slot))(obj, index, &b);
        if (hr < 0 || b == 0) return null;
        var s = Marshal.PtrToStringBSTR(b);
        Marshal.FreeBSTR(b);
        return s;
    }

    private static uint Count(nint obj, int slot)
    {
        uint n = 0;
        int hr = ((delegate* unmanaged[Stdcall]<nint, uint*, int>)Slot(obj, slot))(obj, &n);
        return hr < 0 ? 0 : n;
    }

    // ---- API ------------------------------------------------------------------------------------
    /// <summary>DXIL コンテナを逆アセンブルする (IDxcCompiler::Disassemble, slot 5)。</summary>
    public string DisassembleDxil(byte[] container)
    {
        if (_compiler == 0) _compiler = Create(CLSID_DxcCompiler, IID_IDxcCompiler);
        var blob = MakeBlob(container);
        nint text = 0;
        try
        {
            Check(((delegate* unmanaged[Stdcall]<nint, nint, nint*, int>)Slot(_compiler, 5))(_compiler, blob, &text), "Disassemble");
            return Encoding.UTF8.GetString(BlobBytes(text)).TrimEnd('\0');
        }
        finally { Release(text); Release(blob); }
    }

    public sealed record PdbInfo(string? Name, string? HashHex, string? EntryPoint, string? TargetProfile, string? MainFileName,
                                 List<string> Args, List<(string name, string text)> Sources);

    /// <summary>PDB (またはデバッグ情報付き DXIL) を読み込み、ハッシュ・名前・ソース等を取り出す (IDxcPdbUtils)。</summary>
    public PdbInfo? ReadPdb(byte[] pdb, bool withSources)
    {
        if (_pdbUtils == 0) _pdbUtils = Create(CLSID_DxcPdbUtils, IID_IDxcPdbUtils);
        var u = _pdbUtils;
        var blob = MakeBlob(pdb);
        try
        {
            if (((delegate* unmanaged[Stdcall]<nint, nint, int>)Slot(u, 3))(u, blob) < 0) return null; // Load
            string? hash = null;
            nint hb = 0;
            if (((delegate* unmanaged[Stdcall]<nint, nint*, int>)Slot(u, 18))(u, &hb) >= 0 && hb != 0) // GetHash
            {
                var hbytes = BlobBytes(hb);
                Release(hb);
                // GetHash は HASH パートと同じ形式 (u32 flags + u8[16] digest) を返す
                if (hbytes.Length == 20) hash = Convert.ToHexStringLower(hbytes.AsSpan(4));
                else if (hbytes.Length > 0) hash = Convert.ToHexStringLower(hbytes);
            }
            var args = new List<string>();
            var sources = new List<(string, string)>();
            if (withSources)
            {
                for (uint i = 0, n = Count(u, 9); i < n; i++) args.Add(BstrAt(u, 10, i) ?? ""); // GetArgCount / GetArg
                for (uint i = 0, n = Count(u, 4); i < n; i++)                                  // GetSourceCount
                {
                    nint sb = 0;
                    string text = "";
                    if (((delegate* unmanaged[Stdcall]<nint, uint, nint*, int>)Slot(u, 5))(u, i, &sb) >= 0 && sb != 0) // GetSource
                    {
                        text = Encoding.UTF8.GetString(BlobBytes(sb)).TrimEnd('\0');
                        Release(sb);
                    }
                    sources.Add((BstrAt(u, 6, i) ?? $"source{i}", text));                       // GetSourceName
                }
            }
            return new PdbInfo(Bstr(u, 19), hash, Bstr(u, 16), Bstr(u, 15), Bstr(u, 17), args, sources);
        }
        finally { Release(blob); }
    }

    public void Dispose()
    {
        Release(_compiler); Release(_library); Release(_pdbUtils);
        _compiler = _library = _pdbUtils = 0;
    }

    // ---- DXBC (d3dcompiler_47) ------------------------------------------------------------------
    [LibraryImport("d3dcompiler_47.dll")]
    private static partial int D3DDisassemble(byte* src, nuint size, uint flags, byte* comments, out nint blob);

    public static string? DisassembleDxbc(byte[] container)
    {
        try
        {
            nint blob;
            int hr;
            fixed (byte* p = container) hr = D3DDisassemble(p, (nuint)container.Length, 0, null, out blob);
            if (hr < 0 || blob == 0) return null;
            var text = Encoding.UTF8.GetString(BlobBytes(blob)).TrimEnd('\0');
            Release(blob);
            return text;
        }
        catch (DllNotFoundException) { return null; }
    }
}
