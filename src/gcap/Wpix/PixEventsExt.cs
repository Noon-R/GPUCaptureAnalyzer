using System.Text;
using Gcap.Util;

namespace Gcap.Wpix;

// PIX 独自イベント (拡張: Placed/Reserved/EB リソース, Stream PSO, StateObject, 各種スナップショット)。
// Python 版 tools/gcap/wpix/pix_events.py と同じ出力。形式は docs/formats/wpix.md §4-5。

public static class PixEventsExt
{
    private static bool IsBuffer(Obj desc) => desc.Get("Dimension") is "D3D12_RESOURCE_DIMENSION_BUFFER" || V.ToLong(desc.Get("Dimension")) == 1;

    /// <summary>リソース生成系 (1700/1767/1770) 共通の後半部 (レガシーステート / レイアウト管理テクスチャ)。</summary>
    public static void ResourceStateAndTail(ByteReader r, ApiDecoder d, Obj o, Obj desc, bool extraU32 = false)
    {
        long state = r.U32();
        bool layoutMode = !IsBuffer(desc) && state == 1;   // 🔶 state==1 はテクスチャでは無効値なので判別に使う
        if (layoutMode) o["layoutAtCaptureStart"] = d.Db.EnumName("D3D12_BARRIER_LAYOUT", r.U32()) ?? "?";
        else
        {
            o["stateAtCaptureStart"] = state;
            if (extraU32) o["unk?"] = (long)r.U32();
        }
        o["optimizedClearValue"] = r.Flag() ? d.ReadType(r, "D3D12_CLEAR_VALUE") : null;
        o["riid"] = r.Guid();
        o["initialDataSeq"] = r.U64();
    }

    public static void SubresourceStates(ByteReader r, ApiDecoder d, Obj o)
    {
        ulong n = r.U64();
        if (o.ContainsKey("layoutAtCaptureStart"))
        {
            var subs = new List<object?>();
            for (ulong i = 0; i < n; i++)
            {
                long unk = r.U32();
                subs.Add(new Obj { ["unk?"] = unk, ["layout"] = d.Db.EnumName("D3D12_BARRIER_LAYOUT", r.U32()) ?? "?" });
            }
            o["subresourceLayoutsAtCapture"] = subs;
            o["unkTail?"] = r.U64();
        }
        else
        {
            var st = new List<object?>();
            for (ulong i = 0; i < n; i++) st.Add((long)r.U32());
            o["subresourceStatesAtCapture"] = st;
        }
    }

    // 1700 (committed) は PixEvents.CreateResource から ResourceStateAndTail/SubresourceStates を使う

    public static Obj CreatePlacedResource(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["device"] = (long)r.U32(), ["resource"] = (long)r.U32(), ["heap"] = (long)r.U32(), ["heapOffset"] = r.U64() };
        var desc = (Obj)d.ReadType(r, "D3D12_RESOURCE_DESC1")!;
        o["desc"] = desc;
        ResourceStateAndTail(r, d, o, desc);
        SubresourceStates(r, d, o);
        d.NoteObject(o.GetLong("resource"), ("interface", "ID3D12Resource"), ("created_by", "PIX::CreatePlacedResource"),
                     ("desc", desc), ("heap", o["heap"]), ("heapOffset", o["heapOffset"]));
        return o;
    }

    public static Obj CreateReservedResource(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["device"] = (long)r.U32(), ["resource"] = (long)r.U32() };
        var desc = (Obj)d.ReadType(r, "D3D12_RESOURCE_DESC1")!;
        o["desc"] = desc;
        ResourceStateAndTail(r, d, o, desc, extraU32: true);
        o["tileMappingsSeq"] = r.U64();
        SubresourceStates(r, d, o);
        o["tilingRaw?"] = r.Take((long)r.Remaining);
        d.NoteObject(o.GetLong("resource"), ("interface", "ID3D12Resource"), ("created_by", "PIX::CreateReservedResource"), ("desc", desc));
        return o;
    }

    public static Obj TileMappings(ByteReader r, ApiDecoder d) =>
        new() { ["resource"] = (long)r.U32(), ["raw?"] = r.Take((long)r.Remaining) };

    public static Obj AccelStructData(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["unk0?"] = r.U64(), ["resource"] = (long)r.U32(), ["deserializedSize"] = (long)r.U32(), ["unk1?"] = (long)r.U32() };
        var blob = r.Take((long)r.Remaining);
        if (blob.Length >= 64)
        {
            var h = new ByteReader(blob);
            o["header"] = new Obj
            {
                ["driverOpaqueGuid"] = new Guid(h.Take(16)).ToString("D"),
                ["driverOpaqueVersioningData"] = Convert.ToHexString(h.Take(16)).ToLowerInvariant(),
                ["unk?"] = h.U64(),
                ["serializedSizeInBytes"] = h.U64(),      // = blob 長 - 32 (ドライバ識別子の後から数える)
                ["deserializedSizeInBytes"] = h.U64(),    // = deserializedSize
                ["headerPostambleType?"] = (long)h.U32(),      // BLAS/TLAS とも 0
                ["numBottomLevelPointers?"] = (long)h.U32(),   // BLAS=0, TLAS=1 (このサンプルの TLAS は BLAS 1 個)
            };
        }
        o["serialized"] = blob;
        return o;
    }

    private static Obj PipelineDescPix(ByteReader r, ApiDecoder d)
    {
        const string sb = "D3D12_SHADER_BYTECODE";
        var o = new Obj { ["pRootSignature"] = new Obj { ["obj"] = (long)r.U32() } };
        foreach (var st in new[] { "VS", "PS", "DS", "HS", "GS", "CS" }) o[st] = d.ReadType(r, sb);
        o["StreamOutput"] = d.ReadType(r, "D3D12_STREAM_OUTPUT_DESC");
        o["BlendState"] = d.ReadType(r, "D3D12_BLEND_DESC");
        o["SampleMask"] = (long)r.U32();
        o["RasterizerState"] = d.ReadType(r, "D3D12_RASTERIZER_DESC2");
        o["DepthStencilState"] = d.ReadType(r, "D3D12_DEPTH_STENCIL_DESC2");
        o["InputLayout"] = d.ReadType(r, "D3D12_INPUT_LAYOUT_DESC");
        o["IBStripCutValue"] = d.ReadType(r, "D3D12_INDEX_BUFFER_STRIP_CUT_VALUE");
        o["PrimitiveTopologyType"] = d.ReadType(r, "D3D12_PRIMITIVE_TOPOLOGY_TYPE");
        o["NumRenderTargets"] = (long)r.U32();
        o["RTVFormats"] = d.ReadFieldPublic(r, "DXGI_FORMAT", 0, "8", "PIX_PIPELINE", "RTVFormats");
        o["DSVFormat"] = d.ReadType(r, "DXGI_FORMAT");
        o["SampleDesc"] = d.ReadType(r, "DXGI_SAMPLE_DESC");
        o["NodeMask"] = (long)r.U32();
        o["CachedPSO"] = d.ReadType(r, "D3D12_CACHED_PIPELINE_STATE");
        o["Flags"] = d.ReadType(r, "D3D12_PIPELINE_STATE_FLAGS");
        o["unk16?"] = V.Hex(r.Take(16L));
        o["MS"] = d.ReadType(r, sb);
        o["AS"] = d.ReadType(r, sb);
        o["unk3?"] = V.Hex(r.Take(3L));
        return o;
    }

    public static Obj CreatePipelineState(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["this"] = (long)r.U32(), ["hr"] = (long)r.U32() };
        o["pDesc"] = r.Flag() ? PipelineDescPix(r, d) : null;
        o["riid"] = r.Guid();
        long? pp = r.Flag() ? r.U32() : null;
        o["ppPipelineState"] = pp is null ? null : new Obj { ["obj"] = pp };
        if (pp is long id && id != 0)
            d.NoteObject(id, ("interface", "ID3D12PipelineState"), ("created_by", "ID3D12Device2::CreatePipelineState"));
        return o;
    }

    private static readonly Dictionary<long, string> SubobjectDesc = new()
    {
        [0] = "D3D12_STATE_OBJECT_CONFIG", [1] = "D3D12_GLOBAL_ROOT_SIGNATURE", [2] = "D3D12_LOCAL_ROOT_SIGNATURE",
        [3] = "D3D12_NODE_MASK", [5] = "D3D12_DXIL_LIBRARY_DESC", [6] = "D3D12_EXISTING_COLLECTION_DESC",
        [8] = "D3D12_DXIL_SUBOBJECT_TO_EXPORTS_ASSOCIATION", [9] = "D3D12_RAYTRACING_SHADER_CONFIG",
        [10] = "D3D12_RAYTRACING_PIPELINE_CONFIG", [11] = "D3D12_HIT_GROUP_DESC", [12] = "D3D12_RAYTRACING_PIPELINE_CONFIG1",
    };

    private static List<object?> ReadExports(ByteReader r, ApiDecoder d, bool strings = false)
    {
        // state subobject 内のポインタ配列は present フラグなしで u64 count + 要素
        ulong n = r.U64();
        var l = new List<object?>();
        for (ulong i = 0; i < n; i++) l.Add(strings ? d.ReadString(r, true) : d.ReadType(r, "D3D12_EXPORT_DESC"));
        return l;
    }

    private static object? ReadSubobjectDesc(ByteReader r, ApiDecoder d, long st) => st switch
    {
        5 => new Obj { ["DXILLibrary"] = d.ReadType(r, "D3D12_SHADER_BYTECODE"), ["NumExports"] = (long)r.U32(), ["pExports"] = ReadExports(r, d) },
        6 => new Obj { ["pExistingCollection"] = new Obj { ["obj"] = (long)r.U32() }, ["NumExports"] = (long)r.U32(), ["pExports"] = ReadExports(r, d) },
        8 => new Obj { ["SubobjectToAssociate"] = d.ReadString(r, true), ["NumExports"] = (long)r.U32(), ["pExports"] = ReadExports(r, d, true) },
        _ => d.ReadType(r, SubobjectDesc[st]),
    };

    public static Obj CreateStateObject(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["unk0"] = (long)r.U32(), ["device"] = (long)r.U32() };
        object? desc = null;
        if (r.Flag())
        {
            ulong cnt = r.U64();
            var descs = new List<object?>();
            for (ulong c = 0; c < cnt; c++)
            {
                var t = d.ReadType(r, "D3D12_STATE_OBJECT_TYPE");
                long num = r.U32();
                ulong n = r.U64();
                var subs = new List<object?>();
                for (ulong i = 0; i < n; i++)
                {
                    long st = r.U32();
                    object name = (object?)d.Db.EnumName("D3D12_STATE_SUBOBJECT_TYPE", st) ?? st;
                    object? sdesc = null;
                    if (r.Flag())
                    {
                        if (st == 7) throw new Util.DecodeException("SUBOBJECT_TO_EXPORTS_ASSOCIATION not yet understood");
                        if (!SubobjectDesc.ContainsKey(st)) throw new Util.DecodeException($"state subobject type {st} not yet understood");
                        sdesc = ReadSubobjectDesc(r, d, st);
                    }
                    subs.Add(new Obj { ["Type"] = name, ["desc"] = sdesc });
                }
                descs.Add(new Obj { ["Type"] = t, ["NumSubobjects"] = num, ["pSubobjects"] = subs });
            }
            desc = descs.Count == 1 ? descs[0] : descs;
        }
        o["pDesc"] = desc;
        o["unk16?"] = V.Hex(r.Take(16L));
        o["riid"] = r.Guid();
        o["ppStateObject"] = r.Flag() ? (long)r.U32() : null;
        if (o.GetLong("ppStateObject") is long so && so != 0)
            d.NoteObject(so, ("interface", "ID3D12StateObject"), ("created_by", "PIX::CreateStateObject"));
        return o;
    }

    public static Obj IndirectArguments(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["unk16?"] = V.Hex(r.Take(16L)) };
        ulong n = r.U64();
        var sigId = d.LastIndirectSignature;
        var sig = sigId is long sid && d.Objects.TryGetValue(sid, out var so) ? so.GetObj("desc") : null;
        var argDescs = sig?.GetList("pArgumentDescs")?.OfType<Obj>().ToList() ?? [];
        if (n > 0 && argDescs.Count == 0) throw new Util.DecodeException("command signature for ExecuteIndirect not known");
        var cmds = new List<object?>();
        for (ulong i = 0; i < n; i++)
        {
            var args = new List<object?>();
            foreach (var ad in argDescs)
            {
                long t = r.U32();
                args.Add(new Obj
                {
                    ["type"] = (object?)d.Db.EnumName("D3D12_INDIRECT_ARGUMENT_TYPE", t) ?? t,
                    ["args"] = IndirectArgPayload(r, d, t, ad),
                });
            }
            cmds.Add(args);
        }
        o["commands"] = cmds;
        o["maxCommandCount?"] = (long)r.U32();
        o["commandCount?"] = (long)r.U32();
        o["unk?"] = (long)r.U32();
        o["perCommand?"] = V.Hex(r.Take((long)n));
        o["signature"] = d.ReadType(r, "D3D12_COMMAND_SIGNATURE_DESC");
        ulong size = r.U64();
        o["argumentBufferBytes"] = r.Take(size);
        return o;
    }

    private static object? IndirectArgPayload(ByteReader r, ApiDecoder d, long t, Obj ad) => t switch
    {
        0 => d.ReadType(r, "D3D12_DRAW_ARGUMENTS"),
        1 => d.ReadType(r, "D3D12_DRAW_INDEXED_ARGUMENTS"),
        2 or 10 => d.ReadType(r, "D3D12_DISPATCH_ARGUMENTS"),
        5 => Enumerable.Range(0, (int)(ad.GetObj("Constant")?.GetLong("Num32BitValuesToSet") ?? 1)).Select(_ => (object?)(long)r.U32()).ToList(),
        11 => (long)r.U32(),
        _ => throw new Util.DecodeException($"indirect argument type {t} not yet understood"),
    };

    public static Obj InstanceDescs(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["unk16?"] = V.Hex(r.Take(16L)) };
        ulong n = r.U64();
        var insts = new List<object?>();
        for (ulong i = 0; i < n; i++)
        {
            var tr = d.ReadFieldPublic(r, "FLOAT", 0, "12", "INSTANCE", "Transform");
            insts.Add(new Obj
            {
                ["Transform"] = tr, ["InstanceID"] = (long)r.U32(), ["InstanceMask"] = (long)r.U32(),
                ["InstanceContributionToHitGroupIndex"] = (long)r.U32(), ["Flags"] = (long)r.U32(),
                ["AccelerationStructure"] = d.ReadType(r, "D3D12_GPU_VIRTUAL_ADDRESS"),
            });
        }
        o["instances"] = insts;
        return o;
    }

    private static readonly UnicodeEncoding StrictUtf16 = new(false, false, true);

    /// <summary>2038: シェーダテーブル (識別子をエクスポート名に解決したもの)。エクスポート名を順に抽出し raw を保持。🔶</summary>
    public static Obj ShaderTables(ByteReader r, ApiDecoder d)
    {
        var data = r.Take((long)r.Remaining);
        var names = new List<object?>();
        int i = 16;
        while (i + 9 < data.Length)
        {
            if (data[i] == 1)
            {
                ulong n = BitConverter.ToUInt64(data, i + 1);
                if (n > 0 && n < 256 && (ulong)i + 9 + 2 * n <= (ulong)data.Length)
                {
                    try
                    {
                        var s = StrictUtf16.GetString(data, i + 9, (int)n * 2);
                        if (IsPrintable(s))
                        {
                            names.Add(s);
                            i += 9 + 2 * (int)n;
                            continue;
                        }
                    }
                    catch (DecoderFallbackException) { }
                }
            }
            i++;
        }
        return new Obj { ["exports"] = names, ["raw"] = data };
    }

    /// <summary>Python の str.isprintable() 相当 (制御文字・区切り文字 (空白以外) を含まない)。</summary>
    private static bool IsPrintable(string s)
    {
        foreach (var ch in s)
        {
            var cat = char.GetUnicodeCategory(ch);
            if (ch == ' ') continue;
            if (cat is System.Globalization.UnicodeCategory.Control or System.Globalization.UnicodeCategory.Format
                or System.Globalization.UnicodeCategory.Surrogate or System.Globalization.UnicodeCategory.PrivateUse
                or System.Globalization.UnicodeCategory.OtherNotAssigned or System.Globalization.UnicodeCategory.LineSeparator
                or System.Globalization.UnicodeCategory.ParagraphSeparator or System.Globalization.UnicodeCategory.SpaceSeparator)
                return false;
        }
        return true;
    }

    public static Obj QueryResults(ByteReader r, ApiDecoder d)
    {
        ulong size = r.U64();
        var data = r.Take(size);
        var vals = new List<object?>();
        for (int i = 0; i + 8 <= data.Length; i += 8) vals.Add(BitConverter.ToUInt64(data, i));
        return new Obj { ["size"] = size, ["values"] = vals };
    }

    public static Obj RawEvent(ByteReader r, ApiDecoder d) => new() { ["raw"] = r.Take((long)r.Remaining) };

    public static void Register(ApiDecoder d)
    {
        d.Custom[1629] = ("ID3D12Device2::CreatePipelineState", CreatePipelineState);
        d.Custom[1767] = ("PIX::CreatePlacedResource", CreatePlacedResource);
        d.Custom[1770] = ("PIX::CreateReservedResource", CreateReservedResource);
        d.Custom[2030] = ("PIX::IndirectArguments", IndirectArguments);
        d.Custom[2037] = ("PIX::TileMappings", TileMappings);
        d.Custom[2038] = ("PIX::ShaderTables", ShaderTables);
        d.Custom[2039] = ("PIX::InstanceDescs", InstanceDescs);
        d.Custom[2041] = ("PIX::AccelerationStructureData", AccelStructData);
        d.Custom[2045] = ("PIX::CreateStateObject", CreateStateObject);
        d.Custom[2046] = ("PIX::QueryResults", QueryResults);
        d.Custom[2139] = ("PIX::ShaderTableRecords", RawEvent);
    }
}
