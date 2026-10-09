using System.Reflection;
using System.Text.Json;
using Gcap.Meta;
using Gcap.Util;

namespace Gcap.Wpix;

// EVT レコード -> API 呼び出し。規則は docs/formats/wpix.md §2-3、Python 版 tools/gcap/wpix/decoder.py と同一。
//   typeId = base[宣言インターフェース] + 自前メソッド順 (base は knowledge/type_bases.json, Python 版と共有)

public sealed class Call
{
    public required ulong Seq { get; init; }
    public required uint Stream { get; init; }
    public required uint Type { get; init; }
    public required string Api { get; init; }
    public long? This { get; set; }
    public long? Hr { get; set; }
    public Obj Args { get; set; } = new();
    public bool Ok { get; set; } = true;   // ペイロードを過不足なく消費できたか
    public string Error { get; set; } = "";
    public byte[] Raw { get; set; } = [];

    public string Method => Api.Contains("::") ? Api[(Api.LastIndexOf("::", StringComparison.Ordinal) + 2)..] : Api;

    public Obj ToJsonObj()
    {
        var o = new Obj { ["seq"] = Seq, ["stream"] = (long)Stream, ["type"] = (long)Type, ["api"] = Api };
        if (This is not null) o["this"] = This;
        if (Hr is not null) o["hr"] = Hr;
        o["args"] = Args;
        if (!Ok)
        {
            o["ok"] = false;
            o["error"] = Error;
            o["raw"] = V.Hex(Raw);
        }
        return o;
    }
}

public delegate Obj CustomDecoder(ByteReader r, ApiDecoder d);

public sealed partial class ApiDecoder
{
    // union を持つ構造体: 判別フィールドと 値 -> メンバ名
    private static readonly Dictionary<string, (string field, Dictionary<long, string> members)> UnionSelectors = new()
    {
        ["D3D12_RESOURCE_BARRIER"] = ("Type", new() { [0] = "Transition", [1] = "Aliasing", [2] = "UAV" }),
        ["D3D12_ROOT_PARAMETER"] = ("ParameterType", new() { [0] = "DescriptorTable", [1] = "Constants", [2] = "Descriptor", [3] = "Descriptor", [4] = "Descriptor" }),
        ["D3D12_ROOT_PARAMETER1"] = ("ParameterType", new() { [0] = "DescriptorTable", [1] = "Constants", [2] = "Descriptor", [3] = "Descriptor", [4] = "Descriptor" }),
        ["D3D12_SHADER_RESOURCE_VIEW_DESC"] = ("ViewDimension", new()
        {
            [1] = "Buffer", [2] = "Texture1D", [3] = "Texture1DArray", [4] = "Texture2D", [5] = "Texture2DArray", [6] = "Texture2DMS",
            [7] = "Texture2DMSArray", [8] = "Texture3D", [9] = "TextureCube", [10] = "TextureCubeArray", [11] = "RaytracingAccelerationStructure",
        }),
        ["D3D12_UNORDERED_ACCESS_VIEW_DESC"] = ("ViewDimension", new()
        {
            [1] = "Buffer", [2] = "Texture1D", [3] = "Texture1DArray", [4] = "Texture2D", [5] = "Texture2DArray",
            [6] = "Texture2DMS", [7] = "Texture2DMSArray", [8] = "Texture3D",
        }),
        ["D3D12_RENDER_TARGET_VIEW_DESC"] = ("ViewDimension", new()
        {
            [1] = "Buffer", [2] = "Texture1D", [3] = "Texture1DArray", [4] = "Texture2D", [5] = "Texture2DArray", [6] = "Texture2DMS",
            [7] = "Texture2DMSArray", [8] = "Texture3D",
        }),
        ["D3D12_DEPTH_STENCIL_VIEW_DESC"] = ("ViewDimension", new()
        {
            [1] = "Texture1D", [2] = "Texture1DArray", [3] = "Texture2D", [4] = "Texture2DArray", [5] = "Texture2DMS", [6] = "Texture2DMSArray",
        }),
        ["D3D12_VERSIONED_ROOT_SIGNATURE_DESC"] = ("Version", new() { [1] = "Desc_1_0", [2] = "Desc_1_1", [3] = "Desc_1_2" }),
        ["D3D12_TEXTURE_COPY_LOCATION"] = ("Type", new() { [0] = "SubresourceIndex", [1] = "PlacedFootprint" }),
        // "" = union のメンバを書かない (その判別値では union が空)
        ["D3D12_BARRIER_GROUP"] = ("Type", new() { [0] = "pGlobalBarriers", [1] = "pTextureBarriers", [2] = "pBufferBarriers" }),
        ["D3D12_INDIRECT_ARGUMENT_DESC"] = ("Type", new()
        {
            [0] = "", [1] = "", [2] = "", [3] = "VertexBuffer", [4] = "", [5] = "Constant", [6] = "ConstantBufferView",
            [7] = "ShaderResourceView", [8] = "UnorderedAccessView", [9] = "", [10] = "", [11] = "IncrementingConstant",
        }),
        ["D3D12_RENDER_PASS_BEGINNING_ACCESS"] = ("Type", new()
        {
            [0] = "", [1] = "", [2] = "Clear", [3] = "", [4] = "PreserveLocal", [5] = "PreserveLocal", [6] = "PreserveLocal",
        }),
        ["D3D12_RENDER_PASS_ENDING_ACCESS"] = ("Type", new()
        {
            [0] = "", [1] = "", [2] = "Resolve", [3] = "", [4] = "PreserveLocal", [5] = "PreserveLocal", [6] = "PreserveLocal",
        }),
        ["D3D12_RAYTRACING_GEOMETRY_DESC"] = ("Type", new() { [0] = "Triangles", [1] = "AABBs", [2] = "OmmTriangles" }),
    };

    // 構造体内の CPU ディスクリプタハンドル (owner, field) -> 種別
    private static readonly Dictionary<(string, string), string> CpuHandleKindByField = new()
    {
        [("D3D12_RENDER_PASS_RENDER_TARGET_DESC", "cpuDescriptor")] = "RTV",
        [("D3D12_RENDER_PASS_DEPTH_STENCIL_DESC", "cpuDescriptor")] = "DSV",
    };

    // RenderPass の ClearValue は Format が深度なら DepthStencil、それ以外は Color だけが書かれる
    private static readonly HashSet<string> DepthFormats =
    [
        "DXGI_FORMAT_D32_FLOAT_S8X24_UINT", "DXGI_FORMAT_D32_FLOAT", "DXGI_FORMAT_D24_UNORM_S8_UINT", "DXGI_FORMAT_D16_UNORM",
    ];

    // PIX が API 引数の後ろに付け足す情報 (参照するスナップショットレコードの seq 等)。args["_pix"] に入る
    private static readonly Dictionary<string, Func<ByteReader, Obj>> MethodTrailers = new()
    {
        ["ExecuteIndirect"] = r => new Obj { ["argumentsSeq"] = r.U64() },               // -> PIX::IndirectArguments
        ["DispatchRays"] = r => new Obj { ["shaderTablesSeq"] = r.U64() },               // -> PIX::ShaderTables
        ["BuildRaytracingAccelerationStructure"] = r => new Obj { ["instanceDescsSeq"] = r.U64() }, // -> PIX::InstanceDescs
        ["ResolveQueryData"] = r =>                                                      // -> PIX::QueryResults
        {
            if (!r.Flag()) return new Obj();
            ulong seq = r.U64();
            ulong n = r.U64();
            var vals = new List<object?>();
            for (ulong i = 0; i < n; i++) vals.Add(r.U64());
            return new Obj { ["resultSeq"] = seq, ["values"] = vals };
        },
        ["ExecuteBundle"] = r =>
        {
            long list = r.U32();
            ulong first = r.U64();
            ulong num = r.U64();
            return new Obj { ["list"] = list, ["firstSeq"] = first, ["numRecords"] = num };
        },
    };

    // union の全メンバを (選択せずに) 順に書き出す構造体
    private static readonly HashSet<string> UnionAllMembers = ["D3D12_CLEAR_VALUE"];

    // CPU ディスクリプタハンドル引数 -> ディスクリプタ種別 (RTV/DSV は種別フィールドなし)
    private static readonly Dictionary<string, string> CpuHandleKindByParam = new()
    {
        ["RenderTargetView"] = "RTV", ["pRenderTargetDescriptors"] = "RTV",
        ["DepthStencilView"] = "DSV", ["pDepthStencilDescriptor"] = "DSV",
        ["ViewCPUHandle"] = "CBV_SRV_UAV",
    };

    // CBV_SRV_UAV ヒープ上のディスクリプタ種別 (PIX 独自の列挙)
    private static readonly Dictionary<long, string> DescriptorKind = new()
    {
        [0] = "Empty", [1] = "CBV", [2] = "SRV", [3] = "UAV", [4] = "Sampler?", [5] = "RTV?", [6] = "DSV?",
    };

    // SAL 上は単一ポインタだが PIX は配列として記録する引数
    private static readonly HashSet<(string, string)> ArrayParamOverrides = [("OMSetRenderTargets", "pRenderTargetDescriptors")];

    private static readonly Dictionary<string, long> KnownArrayLen = new()
    {
        ["D3D12_SIMULTANEOUS_RENDER_TARGET_COUNT"] = 8, ["D3D12_SO_BUFFER_SLOT_COUNT"] = 4,
        ["D3D12_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE"] = 16,
    };

    public HeaderDb Db { get; }
    public Dictionary<string, long> Bases { get; }
    public Dictionary<string, string> Aliases { get; }
    public long? LastIndirectSignature { get; set; }
    public Dictionary<uint, (string iface, Method method)> TypeMap { get; } = [];
    public Dictionary<uint, (string name, CustomDecoder fn)> Custom { get; } = [];
    public Dictionary<string, string> IidMap { get; }
    public SortedDictionary<long, Obj> Objects { get; set; } = [];
    private string? _cpuKind;

    public ApiDecoder(HeaderDb db, string? typeBasesPath = null)
    {
        Db = db;
        (Bases, Aliases) = LoadKnowledge(typeBasesPath);
        foreach (var (iface, b) in Bases)
        {
            if (iface.StartsWith('_')) continue;
            int i = 0;
            foreach (var m in OwnMethods(iface)) TypeMap[(uint)(b + i++)] = (iface, m);
        }
        // 規則に従わない ID (PIX が別番号で記録するメソッド)
        foreach (var (tid, full) in Aliases)
        {
            var parts = full.Split("::");
            var m = OwnMethods(parts[0]).FirstOrDefault(x => x.Name == parts[1]);
            if (m is not null) TypeMap[uint.Parse(tid)] = (parts[0], m);
        }
        PixEvents.Register(this);
        IidMap = db.IidToInterface();
    }

    private static (Dictionary<string, long>, Dictionary<string, string>) LoadKnowledge(string? path)
    {
        JsonDocument doc;
        if (path is not null) doc = JsonDocument.Parse(File.ReadAllBytes(path));
        else
        {
            using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("type_bases.json")!;
            doc = JsonDocument.Parse(s);
        }
        var d = new Dictionary<string, long>();
        foreach (var p in doc.RootElement.GetProperty("bases").EnumerateObject()) d[p.Name] = p.Value.GetInt64();
        var a = new Dictionary<string, string>();
        if (doc.RootElement.TryGetProperty("aliases", out var al))
            foreach (var p in al.EnumerateObject()) a[p.Name] = p.Value.GetString()!;
        return (d, a);
    }

    public IEnumerable<Method> OwnMethods(string iface) =>
        Db.Interfaces.TryGetValue(iface, out var itf) ? itf.Methods.Where(m => m.DeclaredIn == iface) : [];

    public string ApiName(uint type)
    {
        if (Custom.TryGetValue(type, out var c)) return c.name;
        if (TypeMap.TryGetValue(type, out var t)) return $"{t.iface}::{t.method.Name}";
        return $"Unknown_{type}";
    }

    public void NoteObject(long? oid, params (string key, object? value)[] kv)
    {
        if (oid is null or 0) return;
        if (!Objects.TryGetValue(oid.Value, out var o)) Objects[oid.Value] = o = new Obj();
        foreach (var (k, v) in kv) o[k] = v;
    }

    // ---------------------------------------------------------------------------------------------
    // 型の解決
    // ---------------------------------------------------------------------------------------------
    public bool IsInterface(string t) =>
        Db.Interfaces.ContainsKey(t) || t == "IUnknown" ||
        (t.StartsWith("ID3D12") && !t.Contains('_')) || (t.StartsWith("IDXGI") && !t.Contains('_'));

    public string Resolve(string t)
    {
        for (int seen = 0; seen < 8; seen++)
        {
            if (!Db.Typedefs.TryGetValue(t, out var nt) || Primitives.IsPrimitive(t) || Db.Structs.ContainsKey(t) || Db.Enums.ContainsKey(t))
                break;
            var first = nt.Split(' ')[0];
            if (nt.Contains('*') || first is "struct" or "enum" or "union") break;
            t = nt;
        }
        return t;
    }

    public object? ReadType(ByteReader r, string t)
    {
        // 特殊型 (typedef 解決前に判定する)
        switch (t)
        {
            case "D3D12_GPU_VIRTUAL_ADDRESS":
            {
                ulong off = r.U64();
                long res = r.U32();
                return new Obj { ["resource"] = res, ["offset"] = off };
            }
            case "D3D12_GPU_DESCRIPTOR_HANDLE":
            {
                long heap = r.U32();
                long index = r.U32();
                return new Obj { ["heap"] = heap, ["index"] = index };
            }
            case "D3D12_CPU_DESCRIPTOR_HANDLE": return ReadCpuDescriptorHandle(r, _cpuKind);
            case "LPCWSTR" or "LPWSTR": return ReadString(r, true);
            case "LPCSTR" or "LPSTR": return ReadString(r, false);
            case "D3D12_RECT" or "RECT":
                return new List<object?> { (long)r.I32(), (long)r.I32(), (long)r.I32(), (long)r.I32() };
        }
        t = Resolve(t);
        if (Primitives.IsPrimitive(t)) return Primitives.Read(r, t);
        if (Db.Enums.ContainsKey(t))
        {
            long v = r.U32();
            return (object?)Db.EnumName(t, v) ?? v;
        }
        if (Db.Structs.ContainsKey(t)) return ReadStruct(r, t);
        if (IsInterface(t)) return new Obj { ["obj"] = (long)r.U32() };
        throw new DecodeException($"unknown type {t}");
    }

    public string? ReadString(ByteReader r, bool wide)
    {
        if (!r.Flag()) return null;
        ulong n = r.U64();
        return wide ? r.WStr(n) : r.AStr(n);
    }

    /// <summary>CPU ディスクリプタハンドルはハンドル値ではなくディスクリプタの中身で記録される (wpix.md §3.2)。</summary>
    public Obj ReadCpuDescriptorHandle(ByteReader r, string? kind)
    {
        if (kind is "RTV" or "DSV")
        {
            var vd = kind == "RTV" ? "D3D12_RENDER_TARGET_VIEW_DESC" : "D3D12_DEPTH_STENCIL_VIEW_DESC";
            var view = r.Flag() ? ReadType(r, vd) : null;
            long res = r.U32();
            var rdesc = r.Flag() ? ReadType(r, "D3D12_RESOURCE_DESC") : null;
            return new Obj { ["kind"] = kind, ["view"] = view, ["resource"] = res, ["resourceDesc"] = rdesc };
        }
        if (kind == "CBV_SRV_UAV") return ReadDescriptor(r);
        throw new DecodeException($"cpu descriptor handle in unknown context ({kind})");
    }

    /// <summary>CBV_SRV_UAV ヒープのディスクリプタ: u32 種別 + 内容 (wpix.md §3.3)。</summary>
    public Obj ReadDescriptor(ByteReader r)
    {
        long k = r.U32();
        var o = new Obj { ["kind"] = DescriptorKind.TryGetValue(k, out var kn) ? kn : k.ToString() };
        switch (k)
        {
            case 0: return o;
            case 1:
                o["view"] = r.Flag() ? ReadType(r, "D3D12_CONSTANT_BUFFER_VIEW_DESC") : null;
                return o;
            case 2:
                o["view"] = r.Flag() ? ReadType(r, "D3D12_SHADER_RESOURCE_VIEW_DESC") : null;
                o["resource"] = (long)r.U32();
                break;
            case 3:
                o["view"] = r.Flag() ? ReadType(r, "D3D12_UNORDERED_ACCESS_VIEW_DESC") : null;
                o["resource"] = (long)r.U32();
                o["counterResource"] = (long)r.U32(); // TODO: counterResource != 0 の場合の追加 desc は未確認
                break;
            default: throw new DecodeException($"descriptor kind {k} not yet understood");
        }
        o["resourceDesc"] = r.Flag() ? ReadType(r, "D3D12_RESOURCE_DESC") : null;
        return o;
    }

    public Obj ReadStruct(ByteReader r, string sname)
    {
        if (sname == "D3D12_RENDER_PASS_BEGINNING_ACCESS_CLEAR_PARAMETERS")
            return new Obj { ["ClearValue"] = ReadClearValueSelected(r) };
        if (sname == "D3D12_BUILD_RAYTRACING_ACCELERATION_STRUCTURE_INPUTS") return ReadBuildInputs(r);
        var st = Db.Structs[sname];
        var o = new Obj();
        UnionSelectors.TryGetValue(sname, out var sel);
        var unionDone = new HashSet<int>();
        foreach (var f0 in st.Fields)
        {
            var f = f0;
            if (f.Union >= 0 && !UnionAllMembers.Contains(sname))
            {
                if (unionDone.Contains(f.Union)) continue;
                if (sel.field is null) throw new DecodeException($"union in {sname} without selector");
                var disc = o.Get(sel.field);
                long? discV = V.ToLong(disc);
                if (discV is null && disc is string ds)
                {
                    var selType = st.Fields.First(x => x.Name == sel.field).Type;
                    if (Db.Enums.TryGetValue(selType, out var en) && en.TryGetValue(ds, out var ev)) discV = ev;
                }
                if (discV is null || !sel.members.TryGetValue(discV.Value, out var member))
                    throw new DecodeException($"{sname}: unknown union selector {disc}");
                if (member.Length == 0)
                {
                    unionDone.Add(f.Union);
                    continue;
                }
                if (f.Name != member)
                {
                    f = st.Fields.FirstOrDefault(x => x.Union == f0.Union && x.Name == member)
                        ?? throw new DecodeException($"{sname}: member {member} not found");
                }
                unionDone.Add(f.Union);
            }
            if (CpuHandleKindByField.TryGetValue((sname, f.Name), out var ck)) _cpuKind = ck;
            o[f.Name] = ReadField(r, f.Type, f.Ptr, f.Array, sname, f.Name);
        }
        return o;
    }

    /// <summary>RenderPass の D3D12_CLEAR_VALUE: Format が深度なら DepthStencil、それ以外は Color のみ。</summary>
    private Obj ReadClearValueSelected(ByteReader r)
    {
        var fmt = ReadType(r, "DXGI_FORMAT");
        if (fmt is string fs && DepthFormats.Contains(fs))
            return new Obj { ["Format"] = fmt, ["DepthStencil"] = ReadType(r, "D3D12_DEPTH_STENCIL_VALUE") };
        return new Obj { ["Format"] = fmt, ["Color"] = ReadFieldPublic(r, "FLOAT", 0, "4", "D3D12_CLEAR_VALUE", "Color") };
    }

    /// <summary>BUILD_RAYTRACING_ACCELERATION_STRUCTURE_INPUTS: union は Type (TOP=InstanceDescs) と DescsLayout で選択。</summary>
    private Obj ReadBuildInputs(ByteReader r)
    {
        var o = new Obj
        {
            ["Type"] = ReadType(r, "D3D12_RAYTRACING_ACCELERATION_STRUCTURE_TYPE"),
            ["Flags"] = ReadType(r, "D3D12_RAYTRACING_ACCELERATION_STRUCTURE_BUILD_FLAGS"),
            ["NumDescs"] = ReadType(r, "UINT"),
            ["DescsLayout"] = ReadType(r, "D3D12_ELEMENTS_LAYOUT"),
        };
        var t = o.Get("Type");
        if (t is "D3D12_RAYTRACING_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL" || V.ToLong(t) == 0)
        {
            o["InstanceDescs"] = ReadType(r, "D3D12_GPU_VIRTUAL_ADDRESS");
            o["unk?"] = r.U64();   // TOP_LEVEL では union の後ろに 8 バイト (0 を観測)
        }
        else if (o.Get("DescsLayout") is "D3D12_ELEMENTS_LAYOUT_ARRAY" || V.ToLong(o.Get("DescsLayout")) == 0)
            o["pGeometryDescs"] = ReadFieldPublic(r, "D3D12_RAYTRACING_GEOMETRY_DESC", 1, "", "INPUTS", "pGeometryDescs");
        else
            o["ppGeometryDescs"] = ReadFieldPublic(r, "D3D12_RAYTRACING_GEOMETRY_DESC", 1, "", "INPUTS", "ppGeometryDescs");
        return o;
    }

    public object? ReadFieldPublic(ByteReader r, string t, int ptr, string array, string owner, string name) =>
        ReadField(r, t, ptr, array, owner, name);

    private object? ReadField(ByteReader r, string t, int ptr, string array, string owner, string name)
    {
        var rt = Resolve(t);
        if (array.Length > 0)
        {
            // 構造体内の固定長配列: u64 要素数 + 要素 (present フラグなし)
            ulong n = r.U64();
            long declared = ArrayLen(array);
            if (n > (ulong)declared) throw new DecodeException($"{owner}.{name}: array count {n} > declared {declared}");
            if (rt is "WCHAR" or "wchar_t") return r.WStr(n).TrimEnd('\0');
            if (rt is "CHAR" or "char") return r.AStr(n).TrimEnd('\0');
            var l = new List<object?>();
            for (ulong i = 0; i < n; i++) l.Add(ReadType(r, t));
            return l;
        }
        if (ptr == 0) return ReadType(r, t);
        if (IsInterface(rt) && ptr == 1) return new Obj { ["obj"] = (long)r.U32() };
        if (rt is "LPCWSTR" or "LPCSTR" || (rt is "WCHAR" or "wchar_t" or "CHAR" or "char" && ptr == 1))
        {
            if (!r.Flag()) return null;
            ulong n = r.U64();
            return rt is "LPCWSTR" or "WCHAR" or "wchar_t" ? r.WStr(n) : r.AStr(n);
        }
        // データポインタ: present + u64 count + 要素
        if (!r.Flag()) return null;
        ulong cnt = r.U64();
        if (rt == "void") return r.Take(cnt);
        var list = new List<object?>();
        if (IsInterface(rt)) { for (ulong i = 0; i < cnt; i++) list.Add(new Obj { ["obj"] = (long)r.U32() }); }
        else { for (ulong i = 0; i < cnt; i++) list.Add(ReadType(r, t)); }
        return list;
    }

    private long ArrayLen(string expr)
    {
        if (long.TryParse(expr, out var n)) return n;
        if (expr.StartsWith("0x") && long.TryParse(expr[2..], System.Globalization.NumberStyles.HexNumber, null, out n)) return n;
        foreach (var e in Db.Enums.Values) if (e.TryGetValue(expr, out var v)) return v;
        if (KnownArrayLen.TryGetValue(expr, out var k)) return k;
        throw new DecodeException($"array length {expr}");
    }

    public object? ReadParam(ByteReader r, Param p, string method)
    {
        var rt = Resolve(p.Type);
        var sal = p.Sal;
        if (ArrayParamOverrides.Contains((method, p.Name))) sal += " _In_reads_(override)";
        _cpuKind = CpuHandleKindByParam.GetValueOrDefault(p.Name);
        if (p.Array.Length > 0)
        {
            // 関数引数の固定長配列 (例 const FLOAT ColorRGBA[4]) は present + count + 要素
            if (!r.Flag()) return null;
            ulong n = r.U64();
            var l = new List<object?>();
            for (ulong i = 0; i < n; i++) l.Add(ReadType(r, p.Type));
            return l;
        }
        if (p.Ptr == 0) return ReadType(r, p.Type);
        if (rt is "LPCWSTR" or "LPCSTR") return ReadType(r, rt);
        if (IsInterface(rt) || (rt == "void" && p.Ptr == 2))
        {
            if (p.Ptr == 1) return new Obj { ["obj"] = (long)r.U32() };
            if (sal.Contains("Outptr") || sal.Contains("_Out"))
                return r.Flag() ? new Obj { ["obj"] = (long)r.U32() } : null;
            // 入力配列 (例 ID3D12DescriptorHeap *const *ppHeaps)
            if (!r.Flag()) return null;
            ulong n = r.U64();
            var l = new List<object?>();
            for (ulong i = 0; i < n; i++) l.Add(new Obj { ["obj"] = (long)r.U32() });
            return l;
        }
        if (!r.Flag()) return null;
        if (rt == "void") return r.Take(r.U64());
        if (sal.Contains("reads") || sal.Contains("writes") || sal.Contains("updates"))
        {
            ulong n = r.U64();
            if (rt is "WCHAR" or "wchar_t") return r.WStr(n);
            var l = new List<object?>();
            for (ulong i = 0; i < n; i++) l.Add(ReadType(r, p.Type));
            return l;
        }
        return ReadType(r, p.Type);
    }

    // ---------------------------------------------------------------------------------------------
    // レコード
    // ---------------------------------------------------------------------------------------------
    public Call Decode(EvtRecord rec)
    {
        var payload = rec.Payload;
        var api = ApiName(rec.Type);
        if (payload.Length < EvtRecord.MetaSize || System.Text.Encoding.Latin1.GetString(payload, 0, 4) != "META")
            return new Call { Seq = rec.Seq, Stream = rec.StreamId, Type = rec.Type, Api = api, Ok = false,
                              Error = "payload does not start with META", Raw = payload };
        var body = payload[EvtRecord.MetaSize..];
        var call = new Call { Seq = rec.Seq, Stream = rec.StreamId, Type = rec.Type, Api = api, Raw = body };
        var r = new ByteReader(body);
        try
        {
            if (Custom.TryGetValue(rec.Type, out var cd)) call.Args = cd.fn(r, this);
            else if (TypeMap.TryGetValue(rec.Type, out var tm))
            {
                call.This = r.U32();
                if (tm.method.Ret == "HRESULT") call.Hr = r.U32();
                foreach (var p in tm.method.Params) call.Args[p.Name] = ReadParam(r, p, tm.method.Name);
                if (MethodTrailers.TryGetValue(tm.method.Name, out var trailer)) call.Args["_pix"] = trailer(r);
                PostProcess(call, tm.method);
            }
            else throw new DecodeException("unknown type id");
            if (r.Remaining > 0)
            {
                call.Ok = false;
                call.Error = $"{r.Remaining} bytes left over";
            }
        }
        catch (Exception e) when (e is DecodeException or KeyNotFoundException or InvalidOperationException or ArgumentException)
        {
            call.Ok = false;
            call.Error = $"{e.GetType().Name}: {e.Message} (at {r.Pos})";
        }
        return call;
    }

    private void PostProcess(Call call, Method m)
    {
        // 生成オブジェクトの型・名前を追跡
        string? riid = null;
        foreach (var p in m.Params)
        {
            var v = call.Args.Get(p.Name);
            if (p.Type == "REFIID") riid = v as string;
            if (v is Obj o && o.ContainsKey("obj") && p.Sal.Contains("Outptr") && riid is not null)
                NoteObject(o.GetLong("obj"), ("interface", IidMap.GetValueOrDefault(riid, riid)),
                           ("created_by", call.Api), ("seq", call.Seq));
        }
        if (m.Name is "BeginEvent" or "SetMarker" && call.Args.Get("pData") is byte[] pd)
            call.Args["_text"] = PixEventText(call.Args.GetLong("Metadata"), pd);
        if (m.Name == "SetName" && call.This is not null) NoteObject(call.This, ("name", call.Args.Get("Name")));
        if (m.Name == "CreateCommandSignature") NoteObject(call.Args.ObjId("ppvCommandSignature"), ("desc", call.Args.Get("pDesc")));
        if (m.Name == "ExecuteIndirect") LastIndirectSignature = call.Args.ObjId("pCommandSignature");
    }
}

public sealed partial class ApiDecoder
{
    /// <summary>
    /// BeginEvent/SetMarker の pData を文字列化する。Metadata 0 = UTF-16, 1 = ANSI, 2 = PIX3 blob
    /// (UINT64[]: eventInfo, color, stringInfo (isANSI = bit 54), 文字列)。
    /// </summary>
    public static string PixEventText(long? metadata, byte[] data)
    {
        static string Cut(string s) { int i = s.IndexOf('\0'); return i >= 0 ? s[..i] : s; }
        switch (metadata)
        {
            case 0: return Cut(System.Text.Encoding.Unicode.GetString(data));
            case 1: return Cut(System.Text.Encoding.Latin1.GetString(data));
            case 2 when data.Length >= 24:
                ulong info = System.Buffers.Binary.BinaryPrimitives.ReadUInt64LittleEndian(data.AsSpan(16));
                var body = data.AsSpan(24);
                if (((info >> 54) & 1) != 0)
                {
                    int z = body.IndexOf((byte)0);
                    return System.Text.Encoding.Latin1.GetString(z >= 0 ? body[..z] : body);
                }
                return Cut(System.Text.Encoding.Unicode.GetString(body));
            default: return $"<metadata={(metadata?.ToString() ?? "None")}>";
        }
    }
}

/// <summary>プリミティブ型の読み取り (Python 版 serializer.py と同じ分類)。</summary>
public static class Primitives
{
    private static readonly HashSet<string> U32 = ["UINT", "UINT32", "DWORD", "ULONG", "D3D12_RECT_UINT", "UINT_PTR32", "D3D12_COMMAND_QUEUE_PRIORITY"];
    private static readonly HashSet<string> I32 = ["INT", "LONG", "INT32", "HRESULT"];
    private static readonly HashSet<string> U64 = ["UINT64", "SIZE_T", "ULONG_PTR", "ULONGLONG", "D3D12_GPU_VIRTUAL_ADDRESS_RAW", "DWORD64"];
    private static readonly HashSet<string> I64 = ["INT64", "LONG64", "LONGLONG"];
    private static readonly HashSet<string> U16 = ["UINT16", "USHORT", "WORD"];
    private static readonly HashSet<string> U8 = ["UINT8", "BYTE", "BOOLEAN", "UCHAR"];
    private static readonly HashSet<string> F32 = ["FLOAT", "float"];
    private static readonly HashSet<string> F64 = ["DOUBLE", "double"];
    private static readonly HashSet<string> Guid = ["REFIID", "REFGUID", "GUID", "IID"];
    private static readonly HashSet<string> Handle = ["HANDLE", "HWND", "HMONITOR"];

    public static bool IsPrimitive(string t) =>
        U32.Contains(t) || I32.Contains(t) || t == "BOOL" || U64.Contains(t) || I64.Contains(t) || U16.Contains(t) ||
        U8.Contains(t) || F32.Contains(t) || F64.Contains(t) || Guid.Contains(t) || Handle.Contains(t);

    public static object Read(ByteReader r, string t)
    {
        if (U32.Contains(t)) return (long)r.U32();
        if (I32.Contains(t)) return (long)r.I32();
        if (t == "BOOL") return r.U32() != 0;
        if (U64.Contains(t)) return r.U64();
        if (I64.Contains(t)) return r.I64();
        if (U16.Contains(t)) return (long)r.U16();
        if (U8.Contains(t)) return (long)r.U8();
        if (F32.Contains(t)) return (double)r.F32();
        if (F64.Contains(t)) return r.F64();
        if (Guid.Contains(t)) return r.Guid();
        if (Handle.Contains(t)) return r.U64();
        throw new KeyNotFoundException(t);
    }
}
