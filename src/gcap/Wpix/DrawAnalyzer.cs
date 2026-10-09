using Gcap.Util;

namespace Gcap.Wpix;

/// <summary>
/// コマンドリストを再生して、各 Draw/Dispatch 時点のパイプライン状態とバインドされたリソースを解決する。
/// 制限: ディスクリプタヒープはキャプチャ開始時のスナップショットを使う (フレーム内の書き換えは未反映)。
/// </summary>
public sealed class DrawAnalyzer
{
    private static readonly Dictionary<string, string> RegPrefix = new() { ["SRV"] = "t", ["UAV"] = "u", ["CBV"] = "b", ["Sampler"] = "s" };
    private static readonly HashSet<string> DrawApis = ["DrawInstanced", "DrawIndexedInstanced", "Dispatch", "ExecuteIndirect", "DispatchRays", "DispatchMesh"];

    private readonly Capture _cap;
    private readonly Dictionary<long, Obj> _rootsigs = [];
    private readonly Dictionary<long, List<object?>> _heaps = [];

    private sealed class State
    {
        public long? Pso;
        public readonly Dictionary<string, long?> RootSig = new() { ["Graphics"] = null, ["Compute"] = null };
        public readonly Dictionary<string, SortedDictionary<long, (string kind, object? val)>> Root = new()
        {
            ["Graphics"] = [], ["Compute"] = [],
        };
        public List<Obj> Vb = [];
        public object? Ib, Topology, Dsv, Viewports, Scissors;
        public List<object?> Rtv = [];
    }

    public DrawAnalyzer(Capture cap)
    {
        _cap = cap;
        foreach (var c in cap.Calls.Where(c => c.Ok))
        {
            if (c.Api.EndsWith("::CreateRootSignature"))
            {
                var rs = c.Args.ObjId("ppvRootSignature");
                if (rs is long id && c.Args.Get("pBlobWithRootSignature") is byte[] blob)
                {
                    try { _rootsigs[id] = RootSig.Parse(blob); }
                    catch (Exception) { /* 解析不能な blob は無視 */ }
                }
            }
            else if (c.Api == "PIX::DescriptorHeap" && c.Args.GetLong("ppHeap") is long heap)
                _heaps[heap] = c.Args.GetList("descriptors") ?? [];
        }
    }

    private string Name(long? oid) => _cap.ObjectName(oid);

    private List<object?> ResolveTable(Obj? rs, long paramIndex, Obj handle)
    {
        long heapId = handle.GetLong("heap") ?? 0, index = handle.GetLong("index") ?? 0;
        var heap = _heaps.GetValueOrDefault(heapId) ?? [];
        var outp = new List<object?>();
        var ranges = new List<Obj>();
        var pars = rs?.GetList("parameters");
        if (pars is not null && paramIndex < pars.Count && pars[(int)paramIndex] is Obj p && p.GetList("ranges") is { } rl)
            ranges.AddRange(rl.OfType<Obj>());
        if (ranges.Count == 0)
            ranges.Add(new Obj { ["type"] = "?", ["num"] = 1L, ["offset"] = 0L, ["baseRegister"] = null, ["space"] = 0L });
        foreach (var rg in ranges)
        {
            long num = rg.GetLong("num") ?? 1, off = rg.GetLong("offset") ?? 0;
            if (num == 0xFFFFFFFF) num = Math.Max(0, heap.Count - index - off);
            for (long k = 0; k < Math.Min(num, 64); k++)
            {
                long slot = index + off + k;
                var d = slot < heap.Count ? heap[(int)slot] as Obj ?? new Obj() : new Obj { ["kind"] = "OutOfSnapshot" };
                var bas = rg.GetLong("baseRegister");
                string? reg = bas is null ? null : $"{RegPrefix.GetValueOrDefault(rg.GetStr("type") ?? "", "?")}{bas + k}";
                var res = d.GetLong("resource");
                outp.Add(new Obj
                {
                    ["register"] = reg, ["space"] = rg.Get("space"), ["heapSlot"] = slot, ["kind"] = d.Get("kind"),
                    ["resource"] = res is long r && r != 0 ? Name(r) : null, ["view"] = d.Get("view"),
                });
            }
        }
        return outp;
    }

    private List<object?> Bindings(State st, string pipe)
    {
        var rsId = st.RootSig[pipe];
        var rs = rsId is long id ? _rootsigs.GetValueOrDefault(id) : null;
        var pars = rs?.GetList("parameters");
        var outp = new List<object?>();
        foreach (var (idx, (kind, val)) in st.Root[pipe])
        {
            var p = pars is not null && idx < pars.Count ? pars[(int)idx] as Obj ?? new Obj() : new Obj();
            var b = new Obj { ["rootParam"] = idx, ["type"] = kind, ["visibility"] = p.Get("visibility") };
            if (kind == "table") b["descriptors"] = ResolveTable(rs, idx, (Obj)val!);
            else if (kind == "constants")
            {
                b["register"] = p.ContainsKey("register") ? $"b{p.GetLong("register")}" : null;
                b["values"] = val;
            }
            else
            {
                b["register"] = p.ContainsKey("register") ? $"{RegPrefix[kind]}{p.GetLong("register")}" : null;
                var va = (Obj)val!;
                b["gpuva"] = new Obj { ["resource"] = Name(va.GetLong("resource")), ["offset"] = va.Get("offset") };
            }
            outp.Add(b);
        }
        return outp;
    }

    public List<Obj> Run()
    {
        var draws = new List<Obj>();
        foreach (var cl in _cap.CommandLists())
        {
            var st = new State();
            foreach (var c in cl.Commands)
            {
                var m = c.Method;
                var a = c.Args;
                switch (m)
                {
                    case "Reset":
                        st = new State();
                        var init = a.ObjId("pInitialState");
                        st.Pso = init is 0 ? null : init;
                        break;
                    case "SetPipelineState": st.Pso = a.ObjId("pPipelineState"); break;
                    case "SetGraphicsRootSignature" or "SetComputeRootSignature":
                    {
                        var pipe = m.Contains("Graphics") ? "Graphics" : "Compute";
                        st.RootSig[pipe] = a.ObjId("pRootSignature");
                        st.Root[pipe] = [];
                        break;
                    }
                    case "IASetVertexBuffers":
                    {
                        long start = a.GetLong("StartSlot") ?? 0;
                        int i = 0;
                        foreach (var vo in a.GetList("pViews") ?? [])
                        {
                            if (vo is not Obj v) continue;
                            long slot = start + i++;
                            var loc = v.GetObj("BufferLocation")!;
                            st.Vb = st.Vb.Where(x => x.GetLong("slot") != slot).ToList();
                            st.Vb.Add(new Obj
                            {
                                ["slot"] = slot, ["resource"] = Name(loc.GetLong("resource")), ["offset"] = loc.Get("offset"),
                                ["size"] = v.Get("SizeInBytes"), ["stride"] = v.Get("StrideInBytes"),
                            });
                        }
                        break;
                    }
                    case "IASetIndexBuffer":
                    {
                        var v = a.GetObj("pView");
                        var loc = v?.GetObj("BufferLocation");
                        st.Ib = v is null || loc is null ? null : new Obj
                        {
                            ["resource"] = Name(loc.GetLong("resource")), ["offset"] = loc.Get("offset"),
                            ["size"] = v.Get("SizeInBytes"), ["format"] = v.Get("Format"),
                        };
                        break;
                    }
                    case "IASetPrimitiveTopology": st.Topology = a.Get("PrimitiveTopology"); break;
                    case "OMSetRenderTargets":
                    {
                        st.Rtv = (a.GetList("pRenderTargetDescriptors") ?? []).OfType<Obj>()
                            .Select(h => (object?)new Obj { ["resource"] = Name(h.GetLong("resource")), ["view"] = h.Get("view") }).ToList();
                        var d = a.GetObj("pDepthStencilDescriptor");
                        st.Dsv = d is null ? null : new Obj { ["resource"] = Name(d.GetLong("resource")), ["view"] = d.Get("view") };
                        break;
                    }
                    case "RSSetViewports": st.Viewports = a.Get("pViewports"); break;
                    case "RSSetScissorRects": st.Scissors = a.Get("pRects"); break;
                    default:
                        if (m.StartsWith("SetGraphicsRoot") || m.StartsWith("SetComputeRoot"))
                        {
                            var pipe = m.StartsWith("SetGraphics") ? "Graphics" : "Compute";
                            long idx = a.GetLong("RootParameterIndex") ?? 0;
                            if (m.EndsWith("DescriptorTable")) st.Root[pipe][idx] = ("table", a.Get("BaseDescriptor"));
                            else if (m.EndsWith("32BitConstants"))
                                st.Root[pipe][idx] = ("constants", a.Get("pSrcData") is byte[] bs ? V.Hex(bs) : null);
                            else if (m.EndsWith("32BitConstant")) st.Root[pipe][idx] = ("constants", a.Get("SrcData"));
                            else if (m.EndsWith("ConstantBufferView")) st.Root[pipe][idx] = ("CBV", a.Get("BufferLocation"));
                            else if (m.EndsWith("ShaderResourceView")) st.Root[pipe][idx] = ("SRV", a.Get("BufferLocation"));
                            else if (m.EndsWith("UnorderedAccessView")) st.Root[pipe][idx] = ("UAV", a.Get("BufferLocation"));
                        }
                        else if (DrawApis.Contains(m))
                        {
                            var pipe = m.StartsWith("Dispatch") ? "Compute" : "Graphics";
                            var args = new Obj();
                            foreach (var kv in a) args[kv.Key] = kv.Value;
                            var d = new Obj
                            {
                                ["seq"] = c.Seq, ["commandList"] = Name(cl.List), ["call"] = m, ["args"] = args,
                                ["pso"] = Name(st.Pso), ["rootSignature"] = Name(st.RootSig[pipe]), ["bindings"] = Bindings(st, pipe),
                            };
                            if (pipe == "Graphics")
                            {
                                d["topology"] = st.Topology;
                                d["vertexBuffers"] = st.Vb.Cast<object?>().ToList();
                                d["indexBuffer"] = st.Ib;
                                d["renderTargets"] = st.Rtv;
                                d["depthStencil"] = st.Dsv;
                                d["viewports"] = st.Viewports;
                                d["scissors"] = st.Scissors;
                            }
                            draws.Add(d);
                        }
                        break;
                }
            }
        }
        return draws;
    }
}
