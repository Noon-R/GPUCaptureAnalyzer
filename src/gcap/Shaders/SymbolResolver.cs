using System.Net.Http;

namespace Gcap.Shaders;

/// <summary>シェーダ PDB の探索対象 (1 シェーダ = 1 PDB)。</summary>
public sealed class ShaderSymbolKey
{
    public required string? HashHex { get; init; }    // HASH パートの digest (hex 小文字)
    public required string? DebugName { get; init; }  // ILDN パートの PDB 名
    public string? FoundPath { get; set; }
    public string? FoundBy { get; set; }              // "capture-dir" / "folder" / "symbol-store" / "symbol-server"
    public string? MatchedBy { get; set; }            // "hash(pdbutils)" / "hash(bytes)" / "name-only"
    public bool Resolved => FoundPath is not null;
    public string Id => HashHex ?? DebugName ?? "?";
}

/// <summary>
/// シェーダ PDB を探索する。探索順 (必要分が全て見つかった時点で打ち切り):
///   1. キャプチャ (.wpix) と同じフォルダ以下 (再帰)
///   2. 指定フォルダ以下 (再帰, 指定順)
///   3. シンボルストア / シンボルサーバ (指定順)
///        "srv*<cache>*<url>" / "srv*<url>" / "https://..."  … HTTP (cache 指定時は cache にストア形式で保存)
///        ローカルパス                                         … ストア形式の直接参照 → 無ければ再帰探索
///      ストア形式: &lt;root&gt;/&lt;pdb名&gt;/&lt;index&gt;/&lt;pdb名&gt;
///        index = symstore / symsrv 標準 (GUID 書式 + Age)。DXC の PDB は GUID の生バイト = シェーダ HASH, Age = 1
///                (PDB info stream と symsrv の SymbolServerGetIndexString で確認済み)
///        互換のため HASH の hex そのまま (大文字/小文字) も試す
/// 照合: PDB 内のハッシュ (IDxcPdbUtils) → PDB ファイル中の digest バイト列 → ファイル名のみ の順に確度が下がる。
/// </summary>
public sealed class SymbolResolver(Dxc? dxc, Action<string>? log = null)
{
    public const long MaxPdbBytes = 256L << 20;
    public int FilesExamined { get; private set; }

    private static readonly EnumerationOptions Recursive = new()
    {
        RecurseSubdirectories = true, IgnoreInaccessible = true, AttributesToSkip = FileAttributes.ReparsePoint,
    };

    public void Resolve(IReadOnlyList<ShaderSymbolKey> keys, string? captureDir, IEnumerable<string> folders,
                        IEnumerable<string> symbolServers)
    {
        var pending = keys.Where(k => !k.Resolved && (k.HashHex is not null || k.DebugName is not null)).ToList();
        bool Done() => pending.All(k => k.Resolved);

        if (captureDir is not null && !Done()) SearchDirectory(captureDir, pending, "capture-dir");
        foreach (var f in folders)
        {
            if (Done()) break;
            SearchDirectory(f, pending, "folder");
        }
        foreach (var s in symbolServers)
        {
            if (Done()) break;
            SearchSymbolPath(s, pending);
        }
    }

    // ---------------------------------------------------------------------------------------------
    private void SearchDirectory(string dir, List<ShaderSymbolKey> pending, string by)
    {
        if (!Directory.Exists(dir)) { log?.Invoke($"  (skip) not a directory: {dir}"); return; }
        log?.Invoke($"  search {by}: {dir}");
        var names = pending.Where(k => k.DebugName is not null).Select(k => k.DebugName!).ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var file in Directory.EnumerateFiles(dir, "*", Recursive))
        {
            if (pending.All(k => k.Resolved)) return; // 必要分が揃ったら打ち切り
            var fn = System.IO.Path.GetFileName(file);
            if (!names.Contains(fn) && !fn.EndsWith(".pdb", StringComparison.OrdinalIgnoreCase)) continue;
            TryCandidate(file, pending, by);
        }
    }

    private void SearchSymbolPath(string spec, List<ShaderSymbolKey> pending)
    {
        string? cache = null, url = null, local = null;
        if (spec.StartsWith("srv*", StringComparison.OrdinalIgnoreCase))
        {
            var parts = spec.Split('*', StringSplitOptions.RemoveEmptyEntries).Skip(1).ToList();
            if (parts.Count >= 2) { cache = parts[0]; url = parts[^1]; }
            else if (parts.Count == 1) url = parts[0];
        }
        else if (spec.StartsWith("http://", StringComparison.OrdinalIgnoreCase) || spec.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
            url = spec;
        else local = spec;

        if (url is not null && !url.StartsWith("http", StringComparison.OrdinalIgnoreCase)) { local = url; url = null; }
        if (cache is not null) SearchStore(cache, pending, "symbol-cache");
        if (local is not null)
        {
            SearchStore(local, pending, "symbol-store");
            if (!pending.All(k => k.Resolved)) SearchDirectory(local, pending, "symbol-store");
        }
        if (url is not null) SearchHttp(url.TrimEnd('/'), cache, pending);
    }

    private static IEnumerable<string> StoreRelPaths(ShaderSymbolKey k)
    {
        var name = k.DebugName ?? (k.HashHex is not null ? k.HashHex + ".pdb" : null);
        if (name is null) yield break;
        if (k.HashHex is not null)
        {
            yield return $"{name}/{SymStoreIndex(k.HashHex, age: 1)}/{name}";
            yield return $"{name}/{k.HashHex.ToUpperInvariant()}/{name}";
            yield return $"{name}/{k.HashHex}/{name}";
        }
        yield return name;
    }

    /// <summary>symstore の PDB 索引: GUID を Data1/2/3 (リトルエンディアン) + Data4 の順に大文字 hex で並べ、Age を hex で付ける。</summary>
    public static string SymStoreIndex(string guidRawHex, int age)
    {
        var b = Convert.FromHexString(guidRawHex);
        if (b.Length != 16) return guidRawHex.ToUpperInvariant() + age.ToString("X");
        var g = new Guid(b);   // バイト列を GUID の内部表現として解釈 (Data1..3 はリトルエンディアン)
        return g.ToString("N").ToUpperInvariant() + age.ToString("X");
    }

    private void SearchStore(string root, List<ShaderSymbolKey> pending, string by)
    {
        if (!Directory.Exists(root)) return;
        log?.Invoke($"  search {by}: {root}");
        foreach (var k in pending.Where(k => !k.Resolved).ToList())
            foreach (var rel in StoreRelPaths(k))
            {
                var p = System.IO.Path.Combine(root, rel.Replace('/', System.IO.Path.DirectorySeparatorChar));
                if (File.Exists(p) && TryCandidate(p, [k], by)) break;
            }
    }

    private void SearchHttp(string url, string? cache, List<ShaderSymbolKey> pending)
    {
        log?.Invoke($"  search symbol-server: {url}");
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        http.DefaultRequestHeaders.UserAgent.ParseAdd("gcap/0.2");
        foreach (var k in pending.Where(k => !k.Resolved).ToList())
        {
            foreach (var rel in StoreRelPaths(k))
            {
                byte[] data;
                try
                {
                    using var resp = http.GetAsync($"{url}/{rel}").GetAwaiter().GetResult();
                    if (!resp.IsSuccessStatusCode) continue;
                    data = resp.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult();
                }
                catch (Exception e) when (e is HttpRequestException or TaskCanceledException)
                {
                    log?.Invoke($"    http error: {e.Message}");
                    break;
                }
                var dest = cache is not null
                    ? System.IO.Path.Combine(cache, rel.Replace('/', System.IO.Path.DirectorySeparatorChar))
                    : System.IO.Path.Combine(System.IO.Path.GetTempPath(), "gcap-symbols", rel.Replace('/', System.IO.Path.DirectorySeparatorChar));
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(dest)!);
                File.WriteAllBytes(dest, data);
                if (TryCandidate(dest, [k], "symbol-server")) break;
            }
        }
    }

    /// <summary>候補ファイルが未解決のどれかに一致するか検証し、一致すれば記録する。</summary>
    private bool TryCandidate(string file, IReadOnlyList<ShaderSymbolKey> pending, string by)
    {
        var open = pending.Where(k => !k.Resolved).ToList();
        if (open.Count == 0) return false;
        byte[] data;
        try
        {
            var fi = new FileInfo(file);
            if (fi.Length == 0 || fi.Length > MaxPdbBytes) return false;
            data = File.ReadAllBytes(file);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { return false; }
        FilesExamined++;
        var fn = System.IO.Path.GetFileName(file);

        // 1) PDB 内のハッシュ (最も確実)
        string? pdbHash = null;
        if (dxc is not null)
        {
            try { pdbHash = dxc.ReadPdb(data, withSources: false)?.HashHex; }
            catch (System.Runtime.InteropServices.COMException) { }
        }
        if (pdbHash is not null)
        {
            var k = open.FirstOrDefault(x => string.Equals(x.HashHex, pdbHash, StringComparison.OrdinalIgnoreCase));
            if (k is not null) return Accept(k, file, by, "hash(pdbutils)");
            // ハッシュが取れたのに一致しない = 別シェーダ (または古い PDB)
            log?.Invoke($"    skip {file}: pdb hash {pdbHash} matches no pending shader");
            return false;
        }
        // 2) digest のバイト列が PDB に含まれるか
        foreach (var k in open.Where(x => x.HashHex is not null))
        {
            var digest = Convert.FromHexString(k.HashHex!);
            if (data.AsSpan().IndexOf(digest) >= 0) return Accept(k, file, by, "hash(bytes)");
        }
        // 3) 名前のみ (確度低)
        var byName = open.FirstOrDefault(x => x.DebugName is not null && string.Equals(x.DebugName, fn, StringComparison.OrdinalIgnoreCase));
        return byName is not null && Accept(byName, file, by, "name-only");
    }

    private bool Accept(ShaderSymbolKey k, string file, string by, string matched)
    {
        k.FoundPath = file;
        k.FoundBy = by;
        k.MatchedBy = matched;
        log?.Invoke($"    found {k.Id} -> {file} [{matched}]");
        return true;
    }
}
