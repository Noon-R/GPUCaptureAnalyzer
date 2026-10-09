using System.Text;
using Gcap.Util;
using Gcap.Wpix;

namespace Gcap.Shaders;

public sealed class ShaderExportOptions
{
    public required string OutDir { get; init; }
    public bool SearchCaptureDir { get; init; } = true;
    public List<string> Folders { get; init; } = [];
    public List<string> SymbolServers { get; init; } = [];
    public bool IlForAll { get; init; }        // 解決済みでも IL を書き出す
    public bool Verbose { get; init; }
}

/// <summary>
/// キャプチャ中のシェーダを書き出し、PDB を探索して照合する。
///   解決 (PDB 発見)   : bytecode + PDB のコピー + (DXC で読めれば) ソースとコンパイル引数
///   未解決            : bytecode + IL (DXIL: dxcompiler の逆アセンブル / DXBC: D3DDisassemble)
/// </summary>
public static class ShaderExporter
{
    private sealed class Entry
    {
        public required byte[] Bytecode;
        public required DxContainer Container;
        public required ShaderSymbolKey Key;
        public readonly List<(long pso, string stage)> Uses = [];
    }

    private static readonly string[] Stages = ["VS", "PS", "DS", "HS", "GS", "CS", "AS", "MS"];

    public static Obj Export(Capture cap, ShaderExportOptions o)
    {
        // ---- 収集 (同一バイトコードは 1 つにまとめる) ----
        var entries = new List<Entry>();
        var byContent = new Dictionary<string, Entry>();
        foreach (var c in cap.Calls.Where(c => c.Ok && c.Method.StartsWith("Create") && c.Api.EndsWith("PipelineState")))
        {
            long pso = c.Args.ObjId("ppPipelineState") ?? 0;
            var desc = c.Args.GetObj("pDesc");
            foreach (var st in Stages)
            {
                if (desc?.GetObj(st)?.Get("pShaderBytecode") is not byte[] bc || bc.Length == 0) continue;
                var contentKey = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(bc));
                if (!byContent.TryGetValue(contentKey, out var e))
                {
                    var dc = new DxContainer(bc);
                    e = new Entry { Bytecode = bc, Container = dc, Key = new ShaderSymbolKey { HashHex = dc.HashHex, DebugName = dc.DebugName } };
                    byContent[contentKey] = e;
                    entries.Add(e);
                }
                e.Uses.Add((pso, st));
            }
        }

        foreach (var c in cap.Calls.Where(c => c.Ok && c.Api == "PIX::CreateStateObject"))
        {
            long so = c.Args.GetLong("ppStateObject") ?? 0;
            foreach (var sub in (c.Args.GetObj("pDesc")?.GetList("pSubobjects") ?? []).OfType<Obj>())
            {
                if (sub.GetObj("desc")?.GetObj("DXILLibrary")?.Get("pShaderBytecode") is not byte[] bc || bc.Length == 0) continue;
                var contentKey = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(bc));
                if (!byContent.TryGetValue(contentKey, out var e))
                {
                    var dc = new DxContainer(bc);
                    e = new Entry { Bytecode = bc, Container = dc, Key = new ShaderSymbolKey { HashHex = dc.HashHex, DebugName = dc.DebugName } };
                    byContent[contentKey] = e;
                    entries.Add(e);
                }
                e.Uses.Add((so, "LIB"));
            }
        }

        // ---- 探索 ----
        using var dxc = Dxc.TryLoad();
        var log = o.Verbose ? (Action<string>)(s => Console.Error.WriteLine(s)) : null;
        log?.Invoke($"dxcompiler: {dxc?.Path ?? "(not found: hash verification falls back to byte search)"}");
        foreach (var e in entries.Where(e => e.Container.HasEmbeddedDebug))
        {
            e.Key.FoundPath = "(embedded)";
            e.Key.FoundBy = "embedded";
            e.Key.MatchedBy = "embedded";
        }
        var resolver = new SymbolResolver(dxc, log);
        var captureDir = o.SearchCaptureDir ? System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(cap.Path)) : null;
        resolver.Resolve(entries.Select(e => e.Key).ToList(), captureDir, o.Folders, o.SymbolServers);

        // ---- 書き出し ----
        Directory.CreateDirectory(o.OutDir);
        var report = new List<object?>();
        int idx = 0;
        foreach (var e in entries)
        {
            var (pso0, st0) = e.Uses[0];
            var psoName = Sanitize(cap.Objects.GetValueOrDefault(pso0)?.GetStr("name") ?? $"pso{pso0}");
            var baseName = $"{idx++:D3}_{st0}_{psoName}";
            var kind = e.Container.Kind;
            File.WriteAllBytes(System.IO.Path.Combine(o.OutDir, $"{baseName}.{kind}"), e.Bytecode);
            var r = new Obj
            {
                ["file"] = $"{baseName}.{kind}", ["kind"] = kind, ["hash"] = e.Key.HashHex, ["debugName"] = e.Key.DebugName,
                ["uses"] = e.Uses.Select(u => (object?)new Obj { ["pso"] = cap.ObjectName(u.pso), ["stage"] = u.stage }).ToList(),
                ["resolved"] = e.Key.Resolved,
            };
            if (e.Key.Resolved)
            {
                r["foundBy"] = e.Key.FoundBy;
                r["matchedBy"] = e.Key.MatchedBy;
                r["pdbSource"] = e.Key.FoundPath;
                if (e.Key.FoundPath != "(embedded)")
                {
                    var pdbOut = $"{baseName}.pdb";
                    File.Copy(e.Key.FoundPath!, System.IO.Path.Combine(o.OutDir, pdbOut), overwrite: true);
                    r["pdb"] = pdbOut;
                    WritePdbDetails(dxc, File.ReadAllBytes(e.Key.FoundPath!), baseName, o.OutDir, r);
                }
            }
            if (!e.Key.Resolved || o.IlForAll)
            {
                var il = Disassemble(dxc, e.Container);
                if (il is not null)
                {
                    File.WriteAllText(System.IO.Path.Combine(o.OutDir, $"{baseName}.il.txt"), il, new UTF8Encoding(false));
                    r["il"] = $"{baseName}.il.txt";
                }
                else r["il"] = null;
            }
            report.Add(r);
        }
        var summary = new Obj
        {
            ["capture"] = cap.Path,
            ["dxcompiler"] = dxc?.Path,
            ["searched"] = new Obj
            {
                ["captureDir"] = captureDir, ["folders"] = o.Folders.Cast<object?>().ToList(),
                ["symbolServers"] = o.SymbolServers.Cast<object?>().ToList(), ["filesExamined"] = (long)resolver.FilesExamined,
            },
            ["total"] = (long)entries.Count,
            ["resolved"] = (long)entries.Count(e => e.Key.Resolved),
            ["shaders"] = report,
        };
        File.WriteAllText(System.IO.Path.Combine(o.OutDir, "shaders.json"), V.ToJson(summary, indented: true), new UTF8Encoding(false));
        return summary;
    }

    private static void WritePdbDetails(Dxc? dxc, byte[] pdb, string baseName, string outDir, Obj r)
    {
        if (dxc is null) return;
        Dxc.PdbInfo? info;
        try { info = dxc.ReadPdb(pdb, withSources: true); }
        catch (System.Runtime.InteropServices.COMException) { return; }
        if (info is null) return;
        r["entryPoint"] = info.EntryPoint;
        r["targetProfile"] = info.TargetProfile;
        r["mainFile"] = info.MainFileName;
        r["compileArgs"] = info.Args.Cast<object?>().ToList();
        if (info.Sources.Count == 0) return;
        var srcDir = System.IO.Path.Combine(outDir, $"{baseName}.src");
        Directory.CreateDirectory(srcDir);
        var files = new List<object?>();
        foreach (var (name, text) in info.Sources)
        {
            var fn = Sanitize(System.IO.Path.GetFileName(name.Replace('/', '\\')));
            File.WriteAllText(System.IO.Path.Combine(srcDir, fn), text, new UTF8Encoding(false));
            files.Add(new Obj { ["name"] = name, ["file"] = $"{baseName}.src/{fn}" });
        }
        r["sources"] = files;
    }

    private static string? Disassemble(Dxc? dxc, DxContainer c)
    {
        if (c.IsDxil)
        {
            if (dxc is null) return null;
            try { return dxc.DisassembleDxil(c.Bytes); }
            catch (System.Runtime.InteropServices.COMException) { return null; }
        }
        return Dxc.DisassembleDxbc(c.Bytes);
    }

    private static string Sanitize(string s)
    {
        var bad = System.IO.Path.GetInvalidFileNameChars();
        var sb = new StringBuilder();
        foreach (var ch in s) sb.Append(bad.Contains(ch) || ch == ' ' ? '_' : ch);
        return sb.ToString();
    }
}
