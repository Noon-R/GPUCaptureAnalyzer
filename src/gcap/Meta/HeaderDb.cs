using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace Gcap.Meta;

// Windows SDK ヘッダ (d3d12.h, dxgi*.h) から COM インターフェース (vtable 順のメソッド)・構造体・列挙型を抽出する。
// JSON スキーマは Python 版 (tools/gcap/d3dmeta/header_parser.py) と同一。

public sealed class Param
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("type")] public string Type { get; set; } = "";
    [JsonPropertyName("ptr")] public int Ptr { get; set; }
    [JsonPropertyName("const")] public bool Const { get; set; }
    [JsonPropertyName("sal")] public string Sal { get; set; } = "";
    [JsonPropertyName("array")] public string Array { get; set; } = "";
}

public sealed class Method
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("declared_in")] public string DeclaredIn { get; set; } = "";
    [JsonPropertyName("ret")] public string Ret { get; set; } = "";
    [JsonPropertyName("params")] public List<Param> Params { get; set; } = [];
}

public sealed class Interface
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("iid")] public string Iid { get; set; } = "";
    [JsonPropertyName("methods")] public List<Method> Methods { get; set; } = [];
}

public sealed class StructField
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("type")] public string Type { get; set; } = "";
    [JsonPropertyName("ptr")] public int Ptr { get; set; }
    [JsonPropertyName("const")] public bool Const { get; set; }
    [JsonPropertyName("array")] public string Array { get; set; } = "";
    [JsonPropertyName("union")] public int Union { get; set; } = -1;
}

public sealed class Struct
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("fields")] public List<StructField> Fields { get; set; } = [];
}

public sealed class HeaderDb
{
    [JsonPropertyName("interfaces")] public OrderedDictionary<string, Interface> Interfaces { get; set; } = new();
    [JsonPropertyName("structs")] public OrderedDictionary<string, Struct> Structs { get; set; } = new();
    [JsonPropertyName("enums")] public OrderedDictionary<string, OrderedDictionary<string, long>> Enums { get; set; } = new();
    [JsonPropertyName("typedefs")] public OrderedDictionary<string, string> Typedefs { get; set; } = new();

    public string? EnumName(string enumType, long value)
    {
        if (!Enums.TryGetValue(enumType, out var e)) return null;
        foreach (var kv in e) if (kv.Value == value) return kv.Key;
        return null;
    }

    public Dictionary<string, string> IidToInterface()
    {
        var d = new Dictionary<string, string>();
        foreach (var kv in Interfaces)
            if (!string.IsNullOrEmpty(kv.Value.Iid)) d[kv.Value.Iid.ToLowerInvariant()] = kv.Key;
        return d;
    }

    // ---------------------------------------------------------------------------------------------
    // ロード / キャッシュ
    // ---------------------------------------------------------------------------------------------
    public static readonly string[] DefaultHeaders =
    [
        "um/d3d12.h",
        "shared/dxgi.h", "shared/dxgi1_2.h", "shared/dxgi1_3.h", "shared/dxgi1_4.h",
        "shared/dxgi1_5.h", "shared/dxgi1_6.h", "shared/dxgicommon.h", "shared/dxgiformat.h",
        "shared/dxgitype.h", "um/d3dcommon.h",
    ];

    public static string DefaultCachePath =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "gcap", "d3d12_meta.json");

    public static string FindSdkInclude()
    {
        var env = Environment.GetEnvironmentVariable("GCAP_SDK_INCLUDE");
        if (!string.IsNullOrEmpty(env)) return env;
        var root = @"C:\Program Files (x86)\Windows Kits\10\Include";
        if (Directory.Exists(root))
        {
            var vers = Directory.GetDirectories(root)
                .Where(d => File.Exists(Path.Combine(d, "um", "d3d12.h")))
                .OrderBy(d => d, StringComparer.Ordinal).ToList();
            if (vers.Count > 0) return vers[^1];
        }
        throw new FileNotFoundException(
            "Windows SDK (d3d12.h) が見つかりません。Windows SDK をインストールするか GCAP_SDK_INCLUDE を設定してください。");
    }

    public static HeaderDb Load(string? cachePath = null, bool rebuild = false, string? sdkInclude = null)
    {
        cachePath ??= DefaultCachePath;
        if (!rebuild && File.Exists(cachePath))
            return JsonSerializer.Deserialize<HeaderDb>(File.ReadAllBytes(cachePath))!;
        var inc = sdkInclude ?? FindSdkInclude();
        var files = DefaultHeaders.Select(h => Path.Combine(inc, h)).Where(File.Exists).ToList();
        var db = HeaderParser.Parse(files);
        Directory.CreateDirectory(Path.GetDirectoryName(cachePath)!);
        File.WriteAllBytes(cachePath, JsonSerializer.SerializeToUtf8Bytes(db));
        return db;
    }
}

public static class HeaderParser
{
    private const RegexOptions S = RegexOptions.Singleline | RegexOptions.CultureInvariant;
    private static readonly Regex ReVtbl = new(@"typedef struct (\w+)Vtbl\s*\{(.*?)\}\s*\w+Vtbl;", S);
    private static readonly Regex ReMethod = new(
        @"DECLSPEC_XFGVIRT\((\w+),\s*(\w+)\)\s*(.*?)\(\s*STDMETHODCALLTYPE\s*\*\s*(\w+)\s*\)\s*\((.*?)\);", S);
    private static readonly Regex ReIid = new(@"MIDL_INTERFACE\(""([0-9A-Fa-f-]{36})""\)\s*\n\s*(\w+)\s*:", S);
    private static readonly Regex ReStruct = new(@"typedef struct (\w+)\s*\{(.*?)\}\s*(\w+)\s*;", S);
    private static readonly Regex ReEnum = new(@"typedef\s+enum\s+(\w+)\s*\{(.*?)\}\s*(\w+)\s*;", S);
    private static readonly Regex ReSal = new(@"^(_[A-Z][A-Za-z_]*_(?:\s*\((?:[^()]|\([^()]*\))*\))?)", S);
    private static readonly Regex ReComment = new(@"/\*.*?\*/|//[^\n]*", S);
    private static readonly Regex ReSimpleTypedef = new(@"typedef\s+([\w ]+?\**)\s+(\w+)\s*;", S);
    private static readonly Regex ReArray = new(@"\[([^\]]*)\]\s*$", S);
    private static readonly Regex ReBlock = new(@"^\s*(union|struct)\s*(\w*)\s*\{", S);
    private static readonly Regex ReBlockEnd = new(@"^\s*(\w*)\s*;", S);
    private static readonly Regex ReSuffix = new(@"(?<=[0-9a-fA-F])[uUlL]+\b", S);
    private static readonly HashSet<string> DropTokens = ["const", "struct", "enum", "interface", "__RPC__deref_out", "__RPC__in"];

    private static string Squash(string s) => string.Join(' ', s.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));

    /// <summary>'_In_reads_(N) const D3D12_X *pX' -> (name, type, ptr, const, sal, array)</summary>
    public static (string name, string type, int ptr, bool isConst, string sal, string array) ParseDecl(string decl)
    {
        decl = Squash(decl);
        var sal = new List<string>();
        while (true)
        {
            var m = ReSal.Match(decl);
            if (!m.Success) break;
            sal.Add(m.Groups[1].Value.Replace(" ", ""));
            decl = decl[m.Length..].Trim();
        }
        string array = "";
        var am = ReArray.Match(decl);
        if (am.Success)
        {
            array = am.Groups[1].Value.Trim();
            decl = decl[..am.Index].Trim();
        }
        int ptr = decl.Count(c => c == '*');
        var toks = decl.Replace('*', ' ').Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries).ToList();
        bool isConst = toks.Contains("const");
        toks = toks.Where(t => !DropTokens.Contains(t)).ToList();
        string name = toks.Count >= 2 ? toks[^1] : "";
        string type = toks.Count >= 2 ? string.Join(' ', toks.Take(toks.Count - 1)) : (toks.Count > 0 ? toks[0] : "");
        return (name, type, ptr, isConst, string.Join(' ', sal), array);
    }

    private static List<string> SplitParams(string s)
    {
        var outp = new List<string>();
        int depth = 0;
        var cur = new System.Text.StringBuilder();
        foreach (var ch in s)
        {
            if (ch == '(') depth++;
            else if (ch == ')') depth--;
            if (ch == ',' && depth == 0) { outp.Add(cur.ToString()); cur.Clear(); }
            else cur.Append(ch);
        }
        if (cur.ToString().Trim().Length > 0) outp.Add(cur.ToString());
        return outp;
    }

    private static List<StructField> ParseStructBody(string body)
    {
        var fields = new List<StructField>();
        int unionId = 0;

        void ParseBlock(string text, int union)
        {
            int i = 0;
            while (i < text.Length)
            {
                var m = ReBlock.Match(text[i..]);
                if (m.Success)
                {
                    int depth = 1, j = i + m.Length;
                    while (depth > 0 && j < text.Length)
                    {
                        if (text[j] == '{') depth++;
                        else if (text[j] == '}') depth--;
                        j++;
                    }
                    var inner = text[(i + m.Length)..(j - 1)];
                    var endm = ReBlockEnd.Match(text[j..]);
                    if (m.Groups[1].Value == "union") { unionId++; ParseBlock(inner, unionId); }
                    else ParseBlock(inner, union);
                    i = j + (endm.Success ? endm.Length : 0);
                    continue;
                }
                int semi = text.IndexOf(';', i);
                if (semi < 0) break;
                var decl = text[i..semi].Trim();
                i = semi + 1;
                if (decl.Length == 0) continue;
                var (name, type, ptr, isConst, _, array) = ParseDecl(decl);
                if (name.Length > 0)
                    fields.Add(new StructField { Name = name, Type = type, Ptr = ptr, Const = isConst, Array = array, Union = union });
            }
        }

        ParseBlock(body, -1);
        return fields;
    }

    public static HeaderDb Parse(IEnumerable<string> paths)
    {
        var db = new HeaderDb();
        var allConsts = new Dictionary<string, long>();
        foreach (var p in paths)
        {
            var src = ReComment.Replace(File.ReadAllText(p), "");
            foreach (Match m in ReIid.Matches(src))
            {
                var n = m.Groups[2].Value;
                if (!db.Interfaces.TryGetValue(n, out var itf)) db.Interfaces[n] = itf = new Interface { Name = n };
                itf.Iid = m.Groups[1].Value.ToLowerInvariant();
            }
            foreach (Match m in ReVtbl.Matches(src))
            {
                var iname = m.Groups[1].Value;
                if (!db.Interfaces.TryGetValue(iname, out var itf)) db.Interfaces[iname] = itf = new Interface { Name = iname };
                itf.Methods = [];
                foreach (Match mm in ReMethod.Matches(m.Groups[2].Value))
                {
                    var ps = new List<Param>();
                    foreach (var pd in SplitParams(mm.Groups[5].Value).Skip(1)) // This を除く
                    {
                        var (name, type, ptr, isConst, sal, array) = ParseDecl(pd);
                        ps.Add(new Param { Name = name, Type = type, Ptr = ptr, Const = isConst, Sal = sal, Array = array });
                    }
                    itf.Methods.Add(new Method
                    {
                        Name = mm.Groups[4].Value, DeclaredIn = mm.Groups[1].Value, Ret = Squash(mm.Groups[3].Value), Params = ps,
                    });
                }
            }
            foreach (Match m in ReStruct.Matches(src))
            {
                if (m.Groups[1].Value.EndsWith("Vtbl") || m.Groups[3].Value.EndsWith("Vtbl")) continue;
                db.Structs[m.Groups[3].Value] = new Struct { Name = m.Groups[3].Value, Fields = ParseStructBody(m.Groups[2].Value) };
            }
            foreach (Match m in ReEnum.Matches(src))
            {
                var vals = new OrderedDictionary<string, long>();
                long cur = -1;
                foreach (var raw in m.Groups[2].Value.Split(','))
                {
                    var item = raw.Trim();
                    if (item.Length == 0) continue;
                    string k;
                    int eq = item.IndexOf('=');
                    if (eq >= 0)
                    {
                        k = item[..eq].Trim();
                        var known = new Dictionary<string, long>(allConsts);
                        foreach (var kv in vals) known[kv.Key] = kv.Value;
                        var val = EvalEnum(item[(eq + 1)..], known);
                        cur = val ?? cur + 1;
                    }
                    else
                    {
                        k = item;
                        cur++;
                    }
                    vals[k] = cur;
                }
                foreach (var kv in vals) allConsts[kv.Key] = kv.Value;
                db.Enums[m.Groups[3].Value] = vals;
            }
            foreach (Match m in ReSimpleTypedef.Matches(src))
            {
                var val = Squash(m.Groups[1].Value);
                var name = m.Groups[2].Value;
                if (val.StartsWith("interface ") || val == name) continue;
                db.Typedefs.TryAdd(name, val);
            }
        }
        // Vtbl を持たない forward 宣言だけのものは除外
        foreach (var k in db.Interfaces.Where(kv => kv.Value.Methods.Count == 0).Select(kv => kv.Key).ToList())
            db.Interfaces.Remove(k);
        return db;
    }

    /// <summary>SDK ヘッダの enum 値の定数式を評価する (Python 版の eval と同じく、未知の識別子があれば null)。</summary>
    public static long? EvalEnum(string expr, IReadOnlyDictionary<string, long> known)
    {
        expr = ReSuffix.Replace(expr.Trim(), "");
        try
        {
            var p = new ExprParser(expr, known);
            var v = p.ParseOr();
            p.SkipWs();
            return p.AtEnd ? v : null;
        }
        catch (FormatException) { return null; }
        catch (KeyNotFoundException) { return null; }
        catch (OverflowException) { return null; }
        catch (DivideByZeroException) { return null; }
    }

    private sealed class ExprParser(string s, IReadOnlyDictionary<string, long> known)
    {
        private int _i;
        public bool AtEnd => _i >= s.Length;
        public void SkipWs() { while (_i < s.Length && char.IsWhiteSpace(s[_i])) _i++; }

        private bool Eat(string op)
        {
            SkipWs();
            if (string.CompareOrdinal(s, _i, op, 0, op.Length) != 0) return false;
            // "<" と "<<" などの取り違え防止
            if (op.Length == 1 && _i + 1 < s.Length && (op == "<" || op == ">") && s[_i + 1] == op[0]) return false;
            _i += op.Length;
            return true;
        }

        public long ParseOr() { var v = ParseXor(); while (PeekSingle('|')) { _i++; v |= ParseXor(); } return v; }
        private long ParseXor() { var v = ParseAnd(); while (PeekSingle('^')) { _i++; v ^= ParseAnd(); } return v; }
        private long ParseAnd() { var v = ParseShift(); while (PeekSingle('&')) { _i++; v &= ParseShift(); } return v; }

        private bool PeekSingle(char c)
        {
            SkipWs();
            return _i < s.Length && s[_i] == c && !(_i + 1 < s.Length && s[_i + 1] == c);
        }

        private long ParseShift()
        {
            var v = ParseAdd();
            while (true)
            {
                if (Eat("<<")) v <<= (int)ParseAdd();
                else if (Eat(">>")) v >>= (int)ParseAdd();
                else return v;
            }
        }

        private long ParseAdd()
        {
            var v = ParseMul();
            while (true)
            {
                SkipWs();
                if (_i < s.Length && s[_i] == '+') { _i++; v = checked(v + ParseMul()); }
                else if (_i < s.Length && s[_i] == '-') { _i++; v = checked(v - ParseMul()); }
                else return v;
            }
        }

        private long ParseMul()
        {
            var v = ParseUnary();
            while (true)
            {
                SkipWs();
                if (_i < s.Length && s[_i] == '*') { _i++; v = checked(v * ParseUnary()); }
                else if (_i < s.Length && s[_i] == '/') { _i++; v /= ParseUnary(); }
                else if (_i < s.Length && s[_i] == '%') { _i++; v %= ParseUnary(); }
                else return v;
            }
        }

        private long ParseUnary()
        {
            SkipWs();
            if (_i >= s.Length) throw new FormatException();
            char c = s[_i];
            if (c == '-') { _i++; return -ParseUnary(); }
            if (c == '+') { _i++; return ParseUnary(); }
            if (c == '~') { _i++; return ~ParseUnary(); }
            if (c == '(')
            {
                _i++;
                var v = ParseOr();
                SkipWs();
                if (_i >= s.Length || s[_i] != ')') throw new FormatException();
                _i++;
                return v;
            }
            if (char.IsDigit(c))
            {
                int st = _i;
                while (_i < s.Length && (char.IsLetterOrDigit(s[_i]) || s[_i] == '_')) _i++;
                var tok = s[st.._i];
                if (tok.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
                    return Convert.ToInt64(tok[2..], 16);
                if (tok.Length > 1 && tok[0] == '0' && tok.Any(ch => ch != '0')) throw new FormatException(); // Python3 と同じく 010 は不可
                return long.Parse(tok, System.Globalization.CultureInfo.InvariantCulture);
            }
            if (char.IsLetter(c) || c == '_')
            {
                int st = _i;
                while (_i < s.Length && (char.IsLetterOrDigit(s[_i]) || s[_i] == '_')) _i++;
                return known[s[st.._i]];
            }
            throw new FormatException();
        }
    }
}
