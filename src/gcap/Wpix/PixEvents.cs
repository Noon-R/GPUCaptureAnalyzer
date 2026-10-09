using Gcap.Util;

namespace Gcap.Wpix;

// PIX 独自イベント (D3D12 API に直接対応しないもの)。PIX 2603.25 で判明した形式 (docs/formats/wpix.md §2.2, §4)。
// 確度が低いフィールドは名前に '?' / 'unk' を付ける。Python 版 pix_events.py と同じ出力。

public static class PixEvents
{
    public const string GuidScreenshotPng = "7c29896a-6790-4b40-ab07-f9876ee0e614";

    private static object? Opt(ByteReader r, Func<object?> fn) => r.Flag() ? fn() : null;

    private static string? WStrOpt(ByteReader r) => r.Flag() ? r.WStr(r.U64()) : null;

    private static string Hex(long v) => $"0x{v:x}";

    /// <summary>FILETIME を ISO 8601 (Python 版の isoformat と同じ形式) にする。</summary>
    public static string FileTime(ulong v)
    {
        // 100ns 単位 -> マイクロ秒 (整数演算で偶数丸め)
        long us = (long)(v / 10), rem = (long)(v % 10);
        if (rem > 5 || (rem == 5 && us % 2 == 1)) us++;
        var t = new DateTime(1601, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddTicks(us * 10);
        var frac = us % 1_000_000;
        return t.ToString("yyyy-MM-dd'T'HH:mm:ss", System.Globalization.CultureInfo.InvariantCulture) +
               (frac != 0 ? $".{frac:D6}" : "") + "+00:00";
    }

    // ID3D12CommandQueue::ExecuteCommandLists の PIX 形式 (汎用規則と異なる):
    //   u32 this, u32 NumCommandLists, u64 count, { u32 list, u64 firstSeq, u64 numRecords } x count
    private static Obj ExecuteCommandLists(ByteReader r, ApiDecoder d)
    {
        long self = r.U32();
        long num = r.U32();
        ulong cnt = r.U64();
        var lists = new List<object?>();
        for (ulong i = 0; i < cnt; i++)
        {
            long list = r.U32();
            ulong first = r.U64();
            ulong n = r.U64();
            lists.Add(new Obj { ["list"] = list, ["firstSeq"] = first, ["numRecords"] = n });
        }
        return new Obj { ["this"] = self, ["NumCommandLists"] = num, ["lists"] = lists };
    }

    // 1700: キャプチャ開始時点で存在したリソースの再生成情報
    private static Obj CreateResource(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["device"] = (long)r.U32(), ["resource"] = (long)r.U32() };
        o["heapProperties"] = d.ReadType(r, "D3D12_HEAP_PROPERTIES");
        o["heapFlags"] = (long)r.U32();
        var desc = (Obj)d.ReadType(r, "D3D12_RESOURCE_DESC1")!;      // SamplerFeedbackMipRegion 付き (60B)
        o["desc"] = desc;
        PixEventsExt.ResourceStateAndTail(r, d, o, desc);              // ステート (またはレイアウト), ClearValue, riid, seq
        PixEventsExt.SubresourceStates(r, d, o);
        d.NoteObject(o.GetLong("resource"), ("interface", "ID3D12Resource"), ("created_by", "PIX::CreateResource"),
                     ("desc", o["desc"]), ("heapType", o.GetObj("heapProperties")?.Get("Type")));
        return o;
    }

    // 2029: リソースのキャプチャ開始時点の内容
    //   u32 resource, u32 unk1(=1), u64 unk2(=0), u64 n, D3D12_PLACED_SUBRESOURCE_FOOTPRINT[n], u64 totalSize, data
    private static Obj ResourceInitialData(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["resource"] = (long)r.U32(), ["unk1"] = (long)r.U32(), ["unk2"] = r.U64() };
        ulong n = r.U64();
        var fps = new List<object?>();
        for (ulong i = 0; i < n; i++) fps.Add(d.ReadType(r, "D3D12_PLACED_SUBRESOURCE_FOOTPRINT"));
        o["footprints"] = fps;
        ulong size = r.U64();
        o["size"] = size;
        o["data"] = r.Take(size);
        return o;
    }

    // 36: 付随データ (blob) の参照: GUID kind, u64 size, u64 seq
    private static Obj BlobRef(ByteReader r, ApiDecoder d)
    {
        var kind = r.Guid();
        ulong size = r.U64();
        return new Obj { ["kind"] = kind, ["size"] = size, ["seq"] = r.U64() };
    }

    // 37: 付随データ本体: GUID kind, u64 size, bytes (kind=スクリーンショットなら PNG)
    private static Obj BlobData(ByteReader r, ApiDecoder d)
    {
        var kind = r.Guid();
        ulong size = r.U64();
        return new Obj { ["kind"] = kind, ["isScreenshotPng"] = kind == GuidScreenshotPng, ["size"] = size, ["data"] = r.Take(size) };
    }

    // 1885: Present。u32 queue, u32 backBuffer, u32 PresentFlags, u64 screenshotSeq
    private static Obj Present(ByteReader r, ApiDecoder d)
    {
        long q = r.U32(), bb = r.U32(), fl = r.U32();
        return new Obj { ["queue"] = q, ["backBuffer"] = bb, ["presentFlags"] = fl, ["screenshotSeq"] = r.U64() };
    }

    // 1860: DXGI 内部コマンドリストの PrepareForPresent
    private static Obj PrepareForPresent(ByteReader r, ApiDecoder d) => new() { ["commandList"] = (long)r.U32() };

    // 1566: D3D12CreateDevice + アダプタ情報 + 機能サポート (後半は未解析)
    private static Obj CreateDevice(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["pAdapter"] = (long)r.U32(), ["MinimumFeatureLevel"] = Hex(r.U32()), ["riid"] = r.Guid() };
        o["ppDevice"] = r.Flag() ? (long)r.U32() : null;
        if (o.GetLong("ppDevice") is long dev && dev != 0)
            d.NoteObject(dev, ("interface", "ID3D12Device"), ("created_by", "D3D12CreateDevice"));
        o["adapterDescription"] = r.WStr(r.U64());
        ulong drv = r.U64();
        o["driverVersion"] = $"{drv >> 48}.{(drv >> 32) & 0xFFFF}.{(drv >> 16) & 0xFFFF}.{drv & 0xFFFF}";
        o["adapterLuid?"] = r.U64();
        o["unk_after_luid"] = (long)r.U32();
        o["VendorId"] = Hex(r.U32());
        o["DeviceId"] = Hex(r.U32());
        o["SubSysId"] = Hex(r.U32());
        o["Revision"] = Hex(r.U32());
        o["capsRaw"] = r.Take((long)r.Remaining); // TODO: 機能サポート構造体列
        return o;
    }

    // 2042: キャプチャ環境 (CPU, OS, マシン名, プロセス名, PIX バージョン, 時刻)
    private static Obj CaptureEnvironment(ByteReader r, ApiDecoder d)
    {
        var o = new Obj
        {
            ["cpu"] = WStrOpt(r),
            ["cpuArch"] = WStrOpt(r),
        };
        o["physicalMemory"] = Opt(r, () => r.U64());
        o["os"] = WStrOpt(r);
        o["osVersionRaw"] = Opt(r, () => r.U64());
        o["osBuildLab"] = WStrOpt(r);
        o["machineName"] = WStrOpt(r);
        o["processName"] = WStrOpt(r);
        var t1 = r.Flag() ? r.U64() : 0UL;
        o["time1"] = t1 != 0 ? FileTime(t1) : null;
        o["processId"] = Opt(r, () => (long)r.U32());
        o["processArch"] = WStrOpt(r);
        o["pixVersion"] = WStrOpt(r);
        var t2 = r.Flag() ? r.U64() : 0UL;
        o["time2"] = t2 != 0 ? FileTime(t2) : null;
        return o;
    }

    // 2044: ディスクリプタヒープの生成 + キャプチャ開始時点の全スロットの内容
    private static Obj DescriptorHeap(ByteReader r, ApiDecoder d)
    {
        var o = new Obj { ["unk0"] = (long)r.U32(), ["device"] = (long)r.U32() };
        o["desc"] = Opt(r, () => d.ReadType(r, "D3D12_DESCRIPTOR_HEAP_DESC"));
        o["unk1"] = (long)r.U32();
        List<object?>? descs = null;
        if (r.Flag())
        {
            ulong n = r.U64();
            descs = [];
            for (ulong i = 0; i < n; i++) descs.Add(d.ReadDescriptor(r));
        }
        o["descriptors"] = descs;
        o["numDescriptorEntries"] = (long)r.U32();
        o["riid"] = r.Guid();
        o["ppHeap"] = r.Flag() ? (long)r.U32() : null;
        if (o.GetLong("ppHeap") is long heap && heap != 0)
            d.NoteObject(heap, ("interface", "ID3D12DescriptorHeap"), ("created_by", "PIX::DescriptorHeap"), ("desc", o["desc"]));
        return o;
    }

    private static Obj U32Only(ByteReader r, ApiDecoder d) => new() { ["value"] = (long)r.U32() };

    public static void Register(ApiDecoder d)
    {
        d.Custom[36] = ("PIX::BlobReference", BlobRef);
        d.Custom[37] = ("PIX::BlobData", BlobData);
        d.Custom[1566] = ("D3D12CreateDevice", CreateDevice);
        d.Custom[1700] = ("PIX::CreateResource", CreateResource);
        d.Custom[1860] = ("PIX::PrepareForPresent", PrepareForPresent);
        d.Custom[1885] = ("PIX::Present", Present);
        d.Custom[2029] = ("PIX::ResourceInitialData", ResourceInitialData);
        d.Custom[2042] = ("PIX::CaptureEnvironment", CaptureEnvironment);
        d.Custom[2043] = ("PIX::Unknown2043", U32Only);
        d.Custom[2044] = ("PIX::DescriptorHeap", DescriptorHeap);
        // ExecuteCommandLists は汎用規則と異なる PIX 独自形式なので上書き
        PixEventsExt.Register(d);
        if (d.Bases.TryGetValue("ID3D12CommandQueue", out var b))
            d.Custom[(uint)(b + 2)] = ("ID3D12CommandQueue::ExecuteCommandLists", ExecuteCommandLists);
    }
}
