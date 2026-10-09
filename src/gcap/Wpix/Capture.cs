using Gcap.Util;

namespace Gcap.Wpix;

public sealed record CommandListRecording(long? Queue, ulong ExecuteSeq, long List, List<Call> Commands);

/// <summary>高レベル API: .wpix を開いて API 呼び出し列・オブジェクト・リソース初期データを取り出す。</summary>
public sealed class Capture
{
    public string Path { get; }
    public WpixFile File { get; }
    public List<EvtRecord> Records { get; }
    public List<Call> Calls { get; }
    public ApiDecoder Decoder { get; }

    private Capture(string path, WpixFile f, List<EvtRecord> recs, List<Call> calls, ApiDecoder dec)
    {
        Path = path; File = f; Records = recs; Calls = calls; Decoder = dec;
    }

    public static Capture Open(string path, ApiDecoder dec)
    {
        var f = new WpixFile(path);
        var recs = EvtRecord.All(f);
        dec.Objects = [];
        var calls = recs.Select(dec.Decode).ToList();
        return new Capture(path, f, recs, calls, dec);
    }

    public SortedDictionary<long, Obj> Objects => Decoder.Objects;

    public string ObjectName(long? oid)
    {
        if (oid is null or 0) return "null";
        var n = Objects.TryGetValue(oid.Value, out var o) ? o.GetStr("name") : null;
        return string.IsNullOrEmpty(n) ? $"#{oid}" : $"#{oid}({n})";
    }

    public IEnumerable<Call> Find(string apiSuffix) => Calls.Where(c => c.Api.EndsWith(apiSuffix, StringComparison.Ordinal));

    public List<Call> Failed() => Calls.Where(c => !c.Ok).ToList();

    private Dictionary<long, List<Call>>? _listCalls;

    /// <summary>
    /// seq >= first で this == listId のコマンドリスト呼び出しを先頭から num 件。
    /// num は seq の範囲幅ではなく「そのリスト自身のレコード数」(間に Bundle の記録や PIX のスナップショットが挟まる)。
    /// </summary>
    private List<Call> ListRecords(long listId, ulong first, ulong num)
    {
        if (_listCalls is null)
        {
            _listCalls = [];
            foreach (var c in Calls) // Calls は seq 順
            {
                long? owner = null;
                if (c.Api.StartsWith("ID3D12GraphicsCommandList") && c.This is not null) owner = c.This;
                else if (c.Api == "PIX::PrepareForPresent") owner = c.Args.GetLong("commandList"); // リストに属する PIX 独自イベント
                if (owner is null) continue;
                if (!_listCalls.TryGetValue(owner.Value, out var l)) _listCalls[owner.Value] = l = [];
                l.Add(c);
            }
        }
        var outp = new List<Call>();
        foreach (var c in _listCalls.GetValueOrDefault(listId) ?? [])
        {
            if (c.Seq < first) continue;
            outp.Add(c);
            if ((ulong)outp.Count == num) break;
        }
        return outp;
    }

    /// <summary>ExecuteCommandLists を起点に、各コマンドリストの記録を取り出す。</summary>
    public List<CommandListRecording> CommandLists()
    {
        var outp = new List<CommandListRecording>();
        foreach (var ecl in Find("::ExecuteCommandLists"))
        {
            foreach (var lo in ecl.Args.GetList("lists") ?? [])
            {
                if (lo is not Obj l) continue;
                long list = l.GetLong("list") ?? 0;
                var cmds = ListRecords(list, (ulong)l.GetLong("firstSeq")!, (ulong)l.GetLong("numRecords")!);
                outp.Add(new CommandListRecording(ecl.Args.GetLong("this"), ecl.Seq, list, cmds));
            }
        }
        return outp;
    }

    /// <summary>ExecuteBundle が実行する Bundle の記録 (Reset/Close を除く)。</summary>
    public List<Call> BundleCommands(Call executeBundle)
    {
        var info = executeBundle.Args.GetObj("_pix");
        if (info is null || !info.ContainsKey("list")) return [];
        return ListRecords(info.GetLong("list") ?? 0, (ulong)info.GetLong("firstSeq")!, (ulong)info.GetLong("numRecords")!)
            .Where(c => c.Method is not ("Reset" or "Close")).ToList();
    }

    public Call? CallBySeq(ulong seq, string api) => Calls.FirstOrDefault(c => c.Seq == seq && c.Api == api);

    public IEnumerable<(long resource, Obj data)> ResourceInitialData() =>
        Calls.Where(c => c.Api == "PIX::ResourceInitialData" && c.Ok).Select(c => (c.Args.GetLong("resource") ?? 0, c.Args));

    public IEnumerable<Obj> Blobs() => Calls.Where(c => c.Api == "PIX::BlobData" && c.Ok).Select(c => c.Args);
}
