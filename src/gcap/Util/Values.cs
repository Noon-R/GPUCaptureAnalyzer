using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace Gcap.Util;

/// <summary>
/// デコード結果は動的な木構造で持つ: Obj (順序付き辞書) / List&lt;object?&gt; / long / ulong / double / bool / string / byte[] / null。
/// </summary>
public sealed class Obj : OrderedDictionary<string, object?>
{
    public Obj() { }

    public object? Get(string key) => TryGetValue(key, out var v) ? v : null;
    public Obj? GetObj(string key) => Get(key) as Obj;
    public List<object?>? GetList(string key) => Get(key) as List<object?>;
    public long? GetLong(string key) => V.ToLong(Get(key));
    public string? GetStr(string key) => Get(key) as string;

    /// <summary>{"obj": N} 形式の ID を取り出す。</summary>
    public long? ObjId(string key) => GetObj(key)?.GetLong("obj");
}

public static class V
{
    public static long? ToLong(object? o) => o switch
    {
        long l => l,
        ulong u => (long)u,
        int i => i,
        uint u => u,
        ushort s => s,
        byte b => b,
        bool b => b ? 1 : 0,
        _ => null,
    };

    public static bool IsInt(object? o) => o is long or ulong or int or uint or ushort or byte;

    public static string Hex(ReadOnlySpan<byte> b) => Convert.ToHexStringLower(b);

    // ---------------------------------------------------------------------------------------------
    // JSON 出力
    // ---------------------------------------------------------------------------------------------
    public static readonly JsonWriterOptions Compact = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
    public static readonly JsonWriterOptions Indented = new() { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, Indented = true };

    /// <param name="shortBytes">true なら byte[] を {"bytes": n, "hex": 先頭64B} に短縮 (Python 版の _jsonable と同じ)</param>
    public static void Write(Utf8JsonWriter w, object? v, bool shortBytes = true)
    {
        switch (v)
        {
            case null: w.WriteNullValue(); break;
            case string s: w.WriteStringValue(s); break;
            case bool b: w.WriteBooleanValue(b); break;
            case long l: w.WriteNumberValue(l); break;
            case ulong u: w.WriteNumberValue(u); break;
            case int i: w.WriteNumberValue(i); break;
            case uint u: w.WriteNumberValue(u); break;
            case double d:
                if (double.IsFinite(d)) w.WriteNumberValue(d);
                else w.WriteStringValue(d.ToString(CultureInfo.InvariantCulture));
                break;
            case float f: Write(w, (double)f, shortBytes); break;
            case byte[] bytes:
                if (shortBytes)
                {
                    w.WriteStartObject();
                    w.WriteNumber("bytes", bytes.Length);
                    w.WriteString("hex", Hex(bytes.AsSpan(0, Math.Min(64, bytes.Length))) + (bytes.Length > 64 ? "..." : ""));
                    w.WriteEndObject();
                }
                else w.WriteStringValue(Hex(bytes));
                break;
            case Obj o:
                w.WriteStartObject();
                foreach (var kv in o)
                {
                    w.WritePropertyName(kv.Key);
                    Write(w, kv.Value, shortBytes);
                }
                w.WriteEndObject();
                break;
            case System.Collections.IEnumerable list:
                w.WriteStartArray();
                foreach (var x in list) Write(w, x, shortBytes);
                w.WriteEndArray();
                break;
            default: w.WriteStringValue(v.ToString()); break;
        }
    }

    public static string ToJson(object? v, bool indented = false, bool shortBytes = true)
    {
        using var ms = new MemoryStream();
        using (var w = new Utf8JsonWriter(ms, indented ? Indented : Compact)) Write(w, v, shortBytes);
        return Encoding.UTF8.GetString(ms.ToArray());
    }
}
