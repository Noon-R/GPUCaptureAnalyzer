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

    /// <summary>ExecuteCommandLists を起点に、各コマンドリストの記録 (seq 範囲) を取り出す。</summary>
    public List<CommandListRecording> CommandLists()
    {
        var listStreams = Calls.Where(c => c.Api.StartsWith("ID3D12GraphicsCommandList::")).Select(c => c.Stream).ToHashSet();
        var bySeq = new Dictionary<(ulong, uint), Call>();
        foreach (var c in Calls) bySeq.TryAdd((c.Seq, c.Stream), c);
        var outp = new List<CommandListRecording>();
        foreach (var ecl in Find("::ExecuteCommandLists"))
        {
            foreach (var lo in ecl.Args.GetList("lists") ?? [])
            {
                if (lo is not Obj l) continue;
                ulong first = (ulong)l.GetLong("firstSeq")!, n = (ulong)l.GetLong("numRecords")!;
                var cmds = new List<Call>();
                for (ulong s = first; s < first + n; s++)
                    foreach (var st in listStreams)
                        if (bySeq.TryGetValue((s, st), out var c)) cmds.Add(c);
                outp.Add(new CommandListRecording(ecl.Args.GetLong("this"), ecl.Seq, l.GetLong("list") ?? 0, cmds));
            }
        }
        return outp;
    }

    public IEnumerable<(long resource, Obj data)> ResourceInitialData() =>
        Calls.Where(c => c.Api == "PIX::ResourceInitialData" && c.Ok).Select(c => (c.Args.GetLong("resource") ?? 0, c.Args));

    public IEnumerable<Obj> Blobs() => Calls.Where(c => c.Api == "PIX::BlobData" && c.Ok).Select(c => c.Args);
}
