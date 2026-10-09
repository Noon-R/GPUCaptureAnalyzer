using System.Globalization;
using System.Text;
using Gcap.Meta;
using Gcap.Shaders;
using Gcap.Util;
using Gcap.Wpix;

namespace Gcap;

public static class Program
{
    private const string Usage = """
        gcap - GPU capture binary analyzer

        Usage:
          gcap wpix info     <capture.wpix>
          gcap wpix calls    <capture.wpix> [--json] [--failed]
          gcap wpix cmdlists <capture.wpix>
          gcap wpix objects  <capture.wpix>
          gcap wpix draws    <capture.wpix> [--json]
          gcap wpix extract  <capture.wpix> -o <outdir>
          gcap wpix shaders  <capture.wpix> -o <outdir> [--symbols <dir>]... [--symbol-server <spec>]...
                             [--no-capture-dir] [--il-all] [-v]
              シェーダを書き出し PDB を探索・照合する。探索順 (必要分が揃った時点で打ち切り):
                1. キャプチャと同じフォルダ以下 (再帰, --no-capture-dir で無効)
                2. --symbols で指定したフォルダ以下 (再帰)
                3. --symbol-server / 環境変数 GCAP_SYMBOL_PATH (';' 区切り)
                   srv*<cache>*<https://url> | <https://url> | <ローカルのシンボルストア>
              見つからないシェーダは IL (DXIL/DXBC の逆アセンブル) で書き出す
          gcap meta rebuild  [--sdk <Windows Kits Include\<ver> dir>]

        Global options:
          --meta <path>         D3D12 メタデータのキャッシュ (既定: %LOCALAPPDATA%\gcap\d3d12_meta.json)
          --type-bases <path>   typeId base 表 (既定: exe 埋め込み)
        """;

    public static int Main(string[] argv)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
        var args = new Args(argv);
        try
        {
            var cmd = args.Positional(0);
            if (args.Has("--version")) { Console.WriteLine(typeof(Program).Assembly.GetName().Version); return 0; }
            if (cmd is null || args.Has("--help") || args.Has("-h")) { Console.WriteLine(Usage); return cmd is null && !args.Has("--help") ? 1 : 0; }
            if (cmd == "meta")
            {
                var db = HeaderDb.Load(args.Value("--meta"), rebuild: true, sdkInclude: args.Value("--sdk"));
                Console.WriteLine($"interfaces={db.Interfaces.Count} structs={db.Structs.Count} enums={db.Enums.Count} -> {args.Value("--meta") ?? HeaderDb.DefaultCachePath}");
                return 0;
            }
            if (cmd != "wpix") throw new ArgumentException($"unknown command: {cmd}");
            var sub = args.Positional(1) ?? throw new ArgumentException("wpix subcommand required");
            var file = args.Positional(2) ?? throw new ArgumentException("capture file required");
            var dec = new ApiDecoder(HeaderDb.Load(args.Value("--meta")), args.Value("--type-bases"));
            switch (sub)
            {
                case "info": Info(file, dec); break;
                case "calls": Calls(Capture.Open(file, dec), args.Has("--json"), args.Has("--failed")); break;
                case "cmdlists": CmdLists(Capture.Open(file, dec)); break;
                case "objects": Objects(Capture.Open(file, dec)); break;
                case "draws": Draws(Capture.Open(file, dec), args.Has("--json")); break;
                case "shaders":
                {
                    var servers = args.Values("--symbol-server").ToList();
                    var envPath = Environment.GetEnvironmentVariable("GCAP_SYMBOL_PATH");
                    if (!string.IsNullOrEmpty(envPath)) servers.AddRange(envPath.Split(';', StringSplitOptions.RemoveEmptyEntries));
                    var summary = ShaderExporter.Export(Capture.Open(file, dec), new ShaderExportOptions
                    {
                        OutDir = args.Value("-o") ?? args.Value("--output") ?? throw new ArgumentException("-o <outdir> required"),
                        SearchCaptureDir = !args.Has("--no-capture-dir"),
                        Folders = args.Values("--symbols").ToList(),
                        SymbolServers = servers,
                        IlForAll = args.Has("--il-all"),
                        Verbose = args.Has("-v") || args.Has("--verbose"),
                    });
                    PrintShaderSummary(summary);
                    break;
                }
                case "extract":
                    Extract(Capture.Open(file, dec), args.Value("-o") ?? args.Value("--output") ?? throw new ArgumentException("-o <outdir> required"));
                    break;
                default: throw new ArgumentException($"unknown wpix subcommand: {sub}");
            }
            return 0;
        }
        catch (Exception e) when (e is ArgumentException or FileNotFoundException or WpixFormatException or DirectoryNotFoundException)
        {
            Console.Error.WriteLine($"error: {e.Message}");
            Console.Error.WriteLine("run 'gcap --help' for usage");
            return 2;
        }
    }

    // ---------------------------------------------------------------------------------------------
    private static void Info(string file, ApiDecoder dec)
    {
        var w = new WpixFile(file);
        Console.WriteLine($"file: {file}  size=0x{w.Buf.Length:x}");
        Console.WriteLine($"header: u32@0x0c={w.HeaderU32_0x0C} u32@0x10={w.HeaderU32_0x10} u32@0x40={w.HeaderU32_0x40}");
        foreach (var s in w.Sections)
        {
            Console.WriteLine($"section <{s.Tag}> version={s.Version} offset=0x{s.Entry.Offset:x} size=0x{s.Entry.Size:x}");
            Console.WriteLine($"  {"stream",6} {"blocks",6} {"comp",10} {"raw",10} {"recs",5}  types");
            foreach (var st in s.Streams)
            {
                var recs = EvtRecord.Iterate(st.Id, st.Data).ToList();
                var comp = st.Blocks.Aggregate(0UL, (acc, b) => acc + b.CompSize);
                var types = recs.Select(r => r.Type).Distinct().Order().ToList();
                var tn = string.Join(", ", types.Take(6).Select(t => $"{t}:{dec.ApiName(t).Split("::")[^1]}"));
                if (types.Count > 6) tn += $", ... ({types.Count} types)";
                Console.WriteLine($"  {st.Id,6} {st.Blocks.Count,6} {comp,10} {st.Data.Length,10} {recs.Count,5}  {tn}");
            }
        }
    }

    /// <summary>引数を 1 行で読みやすく整形 ({"obj": N} はオブジェクト名に置換)。</summary>
    private static string Short(object? v, Capture cap, int depth = 0)
    {
        switch (v)
        {
            case null: return "null";
            case byte[] b: return $"<{b.Length} bytes>";
            case Obj o:
                if (o.Count == 1 && o.ContainsKey("obj")) return cap.ObjectName(o.GetLong("obj"));
                if (o.Count == 2 && o.ContainsKey("resource") && o.ContainsKey("offset"))
                    return $"GPUVA({cap.ObjectName(o.GetLong("resource"))}+{o.Get("offset")})";
                if (o.Count == 2 && o.ContainsKey("heap") && o.ContainsKey("index"))
                    return $"GPUDesc({cap.ObjectName(o.GetLong("heap"))}[{o.Get("index")}])";
                var items = new List<string>();
                foreach (var (k, x) in o)
                {
                    if (k == "resourceDesc" && depth > 0) continue;
                    if (k == "resource" && V.IsInt(x)) { items.Add($"resource={cap.ObjectName(V.ToLong(x))}"); continue; }
                    items.Add($"{k}={Short(x, cap, depth + 1)}");
                }
                return "{" + string.Join(", ", items) + "}";
            case List<object?> l:
                var parts = l.Take(8).Select(x => Short(x, cap, depth + 1));
                return "[" + string.Join(", ", parts) + (l.Count > 8 ? $", ...({l.Count})" : "") + "]";
            case double d: return d.ToString("G6", CultureInfo.InvariantCulture).Replace("E", "e");
            case bool bo: return bo ? "True" : "False";
            case string s: return s.StartsWith("D3D12_") ? s : V.ToJson(s);
            default: return Convert.ToString(v, CultureInfo.InvariantCulture) ?? "";
        }
    }

    private static string FormatArgs(Obj args, Capture cap) => string.Join(", ", args.Select(kv => $"{kv.Key}={Short(kv.Value, cap)}"));

    private static void Calls(Capture cap, bool json, bool failedOnly)
    {
        var sb = new StringBuilder();
        foreach (var c in cap.Calls)
        {
            if (failedOnly && c.Ok) continue;
            if (json) { sb.AppendLine(V.ToJson(c.ToJsonObj())); continue; }
            var self = c.This is not null ? $"{cap.ObjectName(c.This)}." : "";
            var flag = c.Ok ? "" : $"   !! {c.Error}";
            sb.AppendLine($"{c.Seq,6} [{c.Stream,5}] {self}{(self.Length > 0 ? c.Method : c.Api)}({FormatArgs(c.Args, cap)}){flag}");
        }
        Console.Write(sb);
        Console.Error.WriteLine($"# {cap.Calls.Count} records, {cap.Failed().Count} failed");
    }

    private static string EventText(Call c) => c.Args.GetStr("_text") ?? "?";

    private static string FormatArgsNoPix(Obj args, Capture cap) =>
        string.Join(", ", args.Where(kv => kv.Key != "_pix").Select(kv => $"{kv.Key}={Short(kv.Value, cap)}"));

    private static void CmdLists(Capture cap)
    {
        foreach (var cl in cap.CommandLists())
        {
            Console.WriteLine($"=== ExecuteCommandLists seq={cl.ExecuteSeq} queue={cap.ObjectName(cl.Queue)} " +
                              $"list={cap.ObjectName(cl.List)} ({cl.Commands.Count} commands)");
            int depth = 0;
            foreach (var c in cl.Commands)
            {
                var name = c.Method;
                if (name == "EndEvent") { depth = Math.Max(0, depth - 1); continue; }
                var ind = new string(' ', 2 * (depth + 1));
                if (name is "BeginEvent" or "SetMarker")
                {
                    Console.WriteLine($"{ind}{(name == "BeginEvent" ? '>' : '*')} {EventText(c)}");
                    if (name == "BeginEvent") depth++;
                    continue;
                }
                Console.WriteLine($"{ind}{c.Seq,5} {name}({FormatArgsNoPix(c.Args, cap)})");
                if (name == "ExecuteBundle")
                    foreach (var bc in cap.BundleCommands(c))
                        Console.WriteLine($"{ind}    | {bc.Seq,5} {bc.Method}({FormatArgsNoPix(bc.Args, cap)})");
            }
        }
    }

    // リソースの配置: committed はヒープ種別、placed は ID3D12Heap とオフセット、reserved はタイル
    private static string Placement(Capture cap, Obj o) => o.GetStr("created_by") switch
    {
        "PIX::CreatePlacedResource" => $"placed={cap.ObjectName(o.GetLong("heap") ?? 0)}+0x{o.GetLong("heapOffset") ?? 0:x}",
        "PIX::CreateReservedResource" => "reserved",
        _ => $"heap={o.Get("heapType")}",
    };

    private static void Objects(Capture cap)
    {
        foreach (var (oid, o) in cap.Objects)
        {
            var extra = "";
            if (o.GetObj("desc") is { } d && d.ContainsKey("Dimension"))
                extra = $" {d.GetStr("Dimension")?.Replace("D3D12_RESOURCE_DIMENSION_", "")} {d.Get("Width")}x{d.Get("Height")} " +
                        $"fmt={d.Get("Format")} mips={d.Get("MipLevels")} {Placement(cap, o)}";
            Console.WriteLine($"#{oid,-4} {o.GetStr("interface") ?? "?",-28} {o.GetStr("name") ?? "",-32}{extra}");
        }
    }

    private static void Draws(Capture cap, bool json)
    {
        var draws = new DrawAnalyzer(cap).Run();
        if (json) { Console.WriteLine(V.ToJson(draws, indented: true)); return; }
        foreach (var d in draws)
        {
            var args = string.Join(", ", d.GetObj("args")!.Select(kv => $"{kv.Key}={Short(kv.Value, cap)}"));
            var where = $"queue={d.Get("queue")} list={d.Get("commandList")}" + (d.Get("bundle") is string b0 ? $" bundle={b0}" : "");
            Console.WriteLine($"[{d.Get("seq")}] {d.Get("call")}({args})  {where}");
            Console.WriteLine($"    PSO={d.Get("pso")}  RootSig={d.Get("rootSignature")}");
            if (d.Get("indirectCommands") is { } ic) Console.WriteLine($"    indirect: {Short(ic, cap)}");
            if (d.Get("shaderTableExports") is List<object?> ex)
                Console.WriteLine($"    shader tables: [{string.Join(", ", ex.Select(x => $"'{x}'"))}]");
            foreach (var b in d.GetList("bindings")!.OfType<Obj>())
            {
                var head = $"    root[{b.Get("rootParam")}] {b.GetStr("type"),-9} vis={b.Get("visibility")}";
                switch (b.GetStr("type"))
                {
                    case "table":
                        Console.WriteLine(head);
                        foreach (var x in b.GetList("descriptors")!.OfType<Obj>())
                            Console.WriteLine($"        {x.GetStr("register") ?? "?",-5} heap[{x.Get("heapSlot")}] {x.Get("kind"),-5} {x.Get("resource")}");
                        break;
                    case "constants": Console.WriteLine($"{head} {b.Get("register")} = {b.Get("values")}"); break;
                    default:
                        var va = b.GetObj("gpuva")!;
                        Console.WriteLine($"{head} {b.Get("register")} = {va.Get("resource")}+{va.Get("offset")}");
                        break;
                }
            }
            if (d.ContainsKey("renderTargets"))
            {
                var vbs = string.Join(", ", d.GetList("vertexBuffers")!.OfType<Obj>().Select(v => $"({v.Get("slot")}, {v.Get("resource")}, {v.Get("stride")})"));
                Console.WriteLine($"    IA: {d.Get("topology")} VB=[{vbs}] IB={d.GetObj("indexBuffer")?.Get("resource")}");
                var rts = string.Join(", ", d.GetList("renderTargets")!.OfType<Obj>().Select(r => r.Get("resource")));
                Console.WriteLine($"    OM: RTV=[{rts}] DSV={d.GetObj("depthStencil")?.Get("resource")}" +
                                  (d.Get("inRenderPass") is true ? "  (render pass)" : ""));
            }
        }
    }

    private static readonly string[] Rgba8Formats = ["DXGI_FORMAT_R8G8B8A8_UNORM", "DXGI_FORMAT_R8G8B8A8_UNORM_SRGB", "DXGI_FORMAT_B8G8R8A8_UNORM"];

    private static void Extract(Capture cap, string outDir)
    {
        Directory.CreateDirectory(Path.Combine(outDir, "resources"));
        Directory.CreateDirectory(Path.Combine(outDir, "shaders"));
        var resources = new List<object?>();
        var shaders = new List<object?>();
        var blobs = new List<object?>();
        int bi = 0;
        foreach (var b in cap.Blobs())
        {
            var kind = b.GetStr("kind")!;
            var name = $"blob_{bi++}_{kind[..8]}.{(b.Get("isScreenshotPng") is true ? "png" : "bin")}";
            File.WriteAllBytes(Path.Combine(outDir, name), (byte[])b["data"]!);
            blobs.Add(new Obj { ["file"] = name, ["kind"] = kind, ["size"] = b.Get("size") });
        }
        foreach (var (rid, d) in cap.ResourceInitialData())
        {
            var nm = (cap.Objects.GetValueOrDefault(rid)?.GetStr("name") ?? $"res{rid}").Replace(' ', '_');
            var fname = $"{rid:D4}_{nm}.bin";
            var data = (byte[])d["data"]!;
            File.WriteAllBytes(Path.Combine(outDir, "resources", fname), data);
            var entry = new Obj { ["resource"] = rid, ["name"] = nm, ["file"] = $"resources/{fname}", ["footprints"] = d.Get("footprints") };
            // RGBA8 / BGRA8 テクスチャは mip0 を PNG 化
            if (d.GetList("footprints") is { Count: > 0 } fps && fps[0] is Obj fp0 && fp0.GetObj("Footprint") is { } f &&
                Rgba8Formats.Contains(f.GetStr("Format")))
            {
                int w = (int)f.GetLong("Width")!, h = (int)f.GetLong("Height")!, pitch = (int)f.GetLong("RowPitch")!;
                long off = (long)fp0.GetLong("Offset")!;
                bool bgra = f.GetStr("Format") == "DXGI_FORMAT_B8G8R8A8_UNORM";
                var rows = new List<byte[]>();
                for (int y = 0; y < h; y++)
                {
                    var row = data.AsSpan((int)(off + (long)y * pitch), w * 4).ToArray();
                    if (bgra) for (int x = 0; x < w; x++) (row[x * 4], row[x * 4 + 2]) = (row[x * 4 + 2], row[x * 4]);
                    rows.Add(row);
                }
                var png = Path.ChangeExtension(fname, ".png");
                Png.WriteRgba(Path.Combine(outDir, "resources", png), w, h, rows);
                entry["png"] = $"resources/{png}";
            }
            resources.Add(entry);
        }
        foreach (var c in cap.Calls.Where(c => c.Ok))
        {
            if (c.Method.StartsWith("Create") && c.Api.EndsWith("PipelineState"))
            {
                var pso = c.Args.ObjId("ppPipelineState") ?? 0;
                var desc = c.Args.GetObj("pDesc");
                foreach (var stage in new[] { "VS", "PS", "DS", "HS", "GS", "CS", "AS", "MS" })
                {
                    if (desc?.GetObj(stage)?.Get("pShaderBytecode") is not byte[] blob) continue;
                    var nm = cap.Objects.GetValueOrDefault(pso)?.GetStr("name") ?? $"pso{pso}";
                    var ext = IndexOf(blob.AsSpan(0, Math.Min(4096, blob.Length)), "DXIL"u8) >= 0 ? "dxil" : "dxbc";
                    var fname = $"{pso:D4}_{nm}_{stage}.{ext}";
                    File.WriteAllBytes(Path.Combine(outDir, "shaders", fname), blob);
                    shaders.Add(new Obj { ["pso"] = pso, ["stage"] = stage, ["file"] = $"shaders/{fname}" });
                }
            }
            if (c.Api == "PIX::CreateStateObject")
            {
                // DXR などの StateObject: DXIL ライブラリを書き出す
                var so = c.Args.GetLong("ppStateObject") ?? 0;
                var nm = cap.Objects.GetValueOrDefault(so)?.GetStr("name") ?? $"so{so}";
                int li = 0;
                foreach (var sub in (c.Args.GetObj("pDesc")?.GetList("pSubobjects") ?? []).OfType<Obj>())
                {
                    if (sub.GetObj("desc")?.GetObj("DXILLibrary")?.Get("pShaderBytecode") is not byte[] lib) continue;
                    var fname = $"{so:D4}_{nm}_LIB{li++}.dxil";
                    File.WriteAllBytes(Path.Combine(outDir, "shaders", fname), lib);
                    shaders.Add(new Obj { ["stateObject"] = so, ["stage"] = "LIB", ["file"] = $"shaders/{fname}" });
                }
            }
            if (c.Api.EndsWith("::CreateRootSignature") && c.Args.Get("pBlobWithRootSignature") is byte[] rsb)
            {
                var rs = c.Args.ObjId("ppvRootSignature") ?? 0;
                var nm = cap.Objects.GetValueOrDefault(rs)?.GetStr("name") ?? $"rs{rs}";
                var fname = $"{rs:D4}_{nm}.rootsig";
                File.WriteAllBytes(Path.Combine(outDir, "shaders", fname), rsb);
                shaders.Add(new Obj { ["rootSignature"] = rs, ["file"] = $"shaders/{fname}" });
            }
        }
        var manifest = new Obj { ["resources"] = resources, ["shaders"] = shaders, ["blobs"] = blobs };
        File.WriteAllText(Path.Combine(outDir, "extract.json"), V.ToJson(manifest, indented: true), new UTF8Encoding(false));
        Console.WriteLine($"extracted {resources.Count} resources, {shaders.Count} shader blobs, {blobs.Count} blobs -> {outDir}");
    }

    private static void PrintShaderSummary(Obj s)
    {
        Console.WriteLine($"shaders: {s.Get("resolved")}/{s.Get("total")} resolved  (dxcompiler: {s.Get("dxcompiler") ?? "not found"})");
        foreach (var r in s.GetList("shaders")!.OfType<Obj>())
        {
            var uses = string.Join(",", r.GetList("uses")!.OfType<Obj>().Select(u => $"{u.Get("pso")}:{u.Get("stage")}"));
            var state = r.Get("resolved") is true
                ? $"PDB {r.Get("foundBy")} [{r.Get("matchedBy")}] {r.Get("pdbSource")}"
                : (r.Get("il") is string il ? $"NOT FOUND -> IL {il}" : "NOT FOUND (no disassembler)");
            Console.WriteLine($"  {r.Get("file"),-40} {r.Get("debugName") ?? r.Get("hash") ?? "-",-40} {uses}");
            Console.WriteLine($"      {state}");
        }
        Console.WriteLine($"  report: shaders.json  (files examined: {s.GetObj("searched")?.Get("filesExamined")})");
    }

    private static int IndexOf(ReadOnlySpan<byte> hay, ReadOnlySpan<byte> needle) => hay.IndexOf(needle);

    /// <summary>最小限の引数パーサ: 位置引数と --flag / --key value。</summary>
    private sealed class Args
    {
        private static readonly HashSet<string> Valued =
            ["--meta", "--type-bases", "--sdk", "-o", "--output", "--symbols", "--symbol-server"];
        private readonly List<string> _pos = [];
        private readonly Dictionary<string, string?> _opt = [];
        private readonly Dictionary<string, List<string>> _multi = [];

        public Args(string[] argv)
        {
            for (int i = 0; i < argv.Length; i++)
            {
                var a = argv[i];
                if (a.StartsWith('-') && a.Length > 1)
                {
                    if (Valued.Contains(a))
                    {
                        var v = i + 1 < argv.Length ? argv[++i] : throw new ArgumentException($"{a} requires a value");
                        _opt[a] = v;
                        if (!_multi.TryGetValue(a, out var l)) _multi[a] = l = [];
                        l.Add(v);
                    }
                    else _opt[a] = null;
                }
                else _pos.Add(a);
            }
        }

        public string? Positional(int i) => i < _pos.Count ? _pos[i] : null;
        public bool Has(string k) => _opt.ContainsKey(k);
        public string? Value(string k) => _opt.GetValueOrDefault(k);
        public IEnumerable<string> Values(string k) => _multi.TryGetValue(k, out var l) ? l : [];
    }
}
