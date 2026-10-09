"""gcap CLI

    python -m gcap wpix info     CAPTURE.wpix
    python -m gcap wpix calls    CAPTURE.wpix [--json] [--failed]
    python -m gcap wpix cmdlists CAPTURE.wpix
    python -m gcap wpix objects  CAPTURE.wpix
    python -m gcap wpix draws    CAPTURE.wpix [--json]   # Draw/Dispatch 毎のバインディング解決
    python -m gcap wpix extract  CAPTURE.wpix -o OUTDIR
    python -m gcap probe FILE [--manifest known.json]
    python -m gcap meta rebuild          # SDK ヘッダからメタデータ (d3d12_meta.json) を再生成
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _short(v: Any, cap: "Capture", depth: int = 0) -> str:  # noqa: F821
    """引数を 1 行で読みやすく整形 ({'obj': N} はオブジェクト名に置換)。"""
    if isinstance(v, dict):
        if set(v) == {"obj"}:
            return cap.object_name(v["obj"])
        if set(v) == {"bytes", "hex"}:
            return f"<{v['bytes']} bytes>"
        if set(v) == {"resource", "offset"}:
            return f"GPUVA({cap.object_name(v['resource'])}+{v['offset']})"
        if set(v) == {"heap", "index"}:
            return f"GPUDesc({cap.object_name(v['heap'])}[{v['index']}])"
        items = []
        for k, x in v.items():
            if k == "resourceDesc" and depth > 0:
                continue
            if k == "resource" and isinstance(x, int):
                items.append(f"resource={cap.object_name(x)}")
                continue
            items.append(f"{k}={_short(x, cap, depth + 1)}")
        return "{" + ", ".join(items) + "}"
    if isinstance(v, list):
        if len(v) > 8:
            return "[" + ", ".join(_short(x, cap, depth + 1) for x in v[:8]) + f", ...({len(v)})]"
        return "[" + ", ".join(_short(x, cap, depth + 1) for x in v) + "]"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, str) and v.startswith("D3D12_"):
        return v
    return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else str(v)


def cmd_info(a) -> None:
    from .wpix.container import WpixFile
    from .wpix.events import iter_records
    from .wpix.decoder import ApiDecoder
    w = WpixFile(a.file)
    dec = ApiDecoder()
    print(f"file: {a.file}  size={len(w.buf):#x}")
    print(f"header: {w.header_fields} u32@0x40={w.header_u32_0x40}")
    for s in w.sections:
        print(f"section <{s.tag}> version={s.version} offset={s.entry.offset:#x} size={s.entry.size:#x}")
        print(f"  {'stream':>6} {'blocks':>6} {'comp':>10} {'raw':>10} {'recs':>5}  types")
        for sid, st in s.streams.items():
            recs = list(iter_records(sid, st.data))
            comp = sum(b.comp_size for b in st.blocks)
            types = sorted({r.type for r in recs})
            tnames = ", ".join(f"{t}:{dec.api_name(t).split('::')[-1]}" for t in types[:6])
            if len(types) > 6:
                tnames += f", ... ({len(types)} types)"
            print(f"  {sid:6d} {len(st.blocks):6d} {comp:10d} {len(st.data):10d} {len(recs):5d}  {tnames}")


def cmd_calls(a) -> None:
    from .wpix.capture import Capture
    cap = Capture.open(a.file)
    for c in cap.calls:
        if a.failed and c.ok:
            continue
        if a.json:
            print(json.dumps(c.to_json(), ensure_ascii=False))
            continue
        this = f"{cap.object_name(c.this)}." if c.this is not None else ""
        args = ", ".join(f"{k}={_short(v, cap)}" for k, v in c.args.items())
        flag = "" if c.ok else f"   !! {c.error}"
        print(f"{c.seq:6d} [{c.stream:5d}] {this}{c.api.split('::')[-1] if this else c.api}({args}){flag}")
    bad = cap.failed()
    print(f"# {len(cap.calls)} records, {len(bad)} failed", file=sys.stderr)


def _event_text(c) -> str:
    return c.args.get("_text", "?")


def cmd_cmdlists(a) -> None:
    from .wpix.capture import Capture
    cap = Capture.open(a.file)
    for cl in cap.command_lists():
        print(f"=== ExecuteCommandLists seq={cl['executeSeq']} queue={cap.object_name(cl['queue'])} "
              f"list={cap.object_name(cl['list'])} ({len(cl['commands'])} commands)")
        depth = 0
        for c in cl["commands"]:
            name = c.api.split("::")[-1]
            if name == "EndEvent":
                depth = max(0, depth - 1)
                continue
            ind = "  " * (depth + 1)
            if name in ("BeginEvent", "SetMarker"):
                print(f"{ind}{'>' if name == 'BeginEvent' else '*'} {_event_text(c)}")
                if name == "BeginEvent":
                    depth += 1
                continue
            args = ", ".join(f"{k}={_short(v, cap)}" for k, v in c.args.items() if k != "_pix")
            print(f"{ind}{c.seq:5d} {name}({args})")
            if name == "ExecuteBundle":
                for bc in cap.bundle_commands(c):
                    bargs = ", ".join(f"{k}={_short(v, cap)}" for k, v in bc.args.items() if k != "_pix")
                    print(f"{ind}    | {bc.seq:5d} {bc.api.split('::')[-1]}({bargs})")


def _placement(cap, o: dict) -> str:
    """リソースの配置: committed はヒープ種別、placed は ID3D12Heap とオフセット、reserved はタイル。"""
    if o.get("created_by") == "PIX::CreatePlacedResource":
        return f"placed={cap.object_name(o['heap'])}+0x{o['heapOffset']:x}"
    if o.get("created_by") == "PIX::CreateReservedResource":
        return "reserved"
    return f"heap={o.get('heapType')}"


def cmd_objects(a) -> None:
    from .wpix.capture import Capture
    cap = Capture.open(a.file)
    for oid in sorted(cap.objects):
        o = cap.objects[oid]
        extra = ""
        if "desc" in o and isinstance(o["desc"], dict) and "Dimension" in o["desc"]:
            dsc = o["desc"]
            extra = f" {dsc['Dimension'].replace('D3D12_RESOURCE_DIMENSION_', '')} {dsc['Width']}x{dsc['Height']} " \
                    f"fmt={dsc['Format']} mips={dsc['MipLevels']} {_placement(cap, o)}"
        print(f"#{oid:<4d} {o.get('interface', '?'):28s} {o.get('name') or '':32s}{extra}")


def cmd_extract(a) -> None:
    from .pngwrite import write_png_rgba
    from .wpix.capture import Capture
    cap = Capture.open(a.file)
    out = Path(a.output)
    (out / "resources").mkdir(parents=True, exist_ok=True)
    (out / "shaders").mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"resources": [], "shaders": [], "blobs": []}
    for i, b in enumerate(cap.blobs()):
        ext = "png" if b["isScreenshotPng"] else "bin"
        p = out / f"blob_{i}_{b['kind'][:8]}.{ext}"
        p.write_bytes(b["data"])
        manifest["blobs"].append({"file": p.name, "kind": b["kind"], "size": b["size"]})
    for rid, d in cap.resource_initial_data():
        name = (cap.objects.get(rid, {}).get("name") or f"res{rid}").replace(" ", "_")
        p = out / "resources" / f"{rid:04d}_{name}.bin"
        p.write_bytes(d["data"])
        entry = {"resource": rid, "name": name, "file": f"resources/{p.name}", "footprints": d["footprints"]}
        fps = d["footprints"]
        # RGBA8 / BGRA8 テクスチャは mip0 を PNG 化
        if fps and fps[0]["Footprint"]["Format"] in ("DXGI_FORMAT_R8G8B8A8_UNORM", "DXGI_FORMAT_R8G8B8A8_UNORM_SRGB",
                                                     "DXGI_FORMAT_B8G8R8A8_UNORM"):
            fp = fps[0]
            w, h, pitch = fp["Footprint"]["Width"], fp["Footprint"]["Height"], fp["Footprint"]["RowPitch"]
            rows = [d["data"][fp["Offset"] + y * pitch: fp["Offset"] + y * pitch + w * 4] for y in range(h)]
            if fp["Footprint"]["Format"] == "DXGI_FORMAT_B8G8R8A8_UNORM":
                rows = [bytes(x for px in range(w) for x in (r[px*4+2], r[px*4+1], r[px*4], r[px*4+3])) for r in rows]
            png = p.with_suffix(".png")
            write_png_rgba(str(png), w, h, rows)
            entry["png"] = f"resources/{png.name}"
        manifest["resources"].append(entry)
    for c in cap.calls:
        if c.api.endswith("PipelineState") and c.api.split("::")[-1].startswith("Create"):
            raw = cap.raw_args(c)
            desc = raw.get("pDesc") or {}
            pso = (raw.get("ppPipelineState") or {}).get("obj")
            for stage in ("VS", "PS", "DS", "HS", "GS", "CS", "AS", "MS"):
                sb = desc.get(stage)
                if isinstance(sb, dict) and isinstance(sb.get("pShaderBytecode"), (bytes, bytearray)):
                    nm = cap.objects.get(pso, {}).get("name") or f"pso{pso}"
                    blob = bytes(sb["pShaderBytecode"])
                    ext = "dxil" if b"DXIL" in blob[:4096] else "dxbc"
                    p = out / "shaders" / f"{pso:04d}_{nm}_{stage}.{ext}"
                    p.write_bytes(blob)
                    manifest["shaders"].append({"pso": pso, "stage": stage, "file": f"shaders/{p.name}"})
        if c.api == "PIX::CreateStateObject" and c.ok:
            # DXR などの StateObject: DXIL ライブラリを書き出す
            raw = cap.raw_args(c)
            so = raw.get("ppStateObject") or 0
            nm = cap.objects.get(so, {}).get("name") or f"so{so}"
            li = 0
            for sub in (raw.get("pDesc") or {}).get("pSubobjects") or []:
                lib = ((sub.get("desc") or {}).get("DXILLibrary") or {}).get("pShaderBytecode")
                if not isinstance(lib, (bytes, bytearray)):
                    continue
                p = out / "shaders" / f"{so:04d}_{nm}_LIB{li}.dxil"
                li += 1
                p.write_bytes(bytes(lib))
                manifest["shaders"].append({"stateObject": so, "stage": "LIB", "file": f"shaders/{p.name}"})
        if c.api.endswith("::CreateRootSignature"):
            raw = cap.raw_args(c)
            rs = (raw.get("ppvRootSignature") or {}).get("obj")
            blob = raw.get("pBlobWithRootSignature")
            if isinstance(blob, (bytes, bytearray)):
                nm = cap.objects.get(rs, {}).get("name") or f"rs{rs}"
                p = out / "shaders" / f"{rs:04d}_{nm}.rootsig"
                p.write_bytes(bytes(blob))
                manifest["shaders"].append({"rootSignature": rs, "file": f"shaders/{p.name}"})
    (out / "extract.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"extracted {len(manifest['resources'])} resources, {len(manifest['shaders'])} shader blobs, "
          f"{len(manifest['blobs'])} blobs -> {out}")


def cmd_draws(a) -> None:
    from .wpix.capture import Capture
    from .wpix.state import analyze_draws
    cap = Capture.open(a.file)
    draws = analyze_draws(cap)
    if a.json:
        print(json.dumps(draws, ensure_ascii=False, indent=1))
        return
    for d in draws:
        args = ", ".join(f"{k}={_short(v, cap)}" for k, v in d["args"].items())
        where = f"queue={d['queue']} list={d['commandList']}" + (f" bundle={d['bundle']}" if d.get("bundle") else "")
        print(f"[{d['seq']}] {d['call']}({args})  {where}")
        print(f"    PSO={d['pso']}  RootSig={d['rootSignature']}")
        if d.get("indirectCommands") is not None:
            print(f"    indirect: {_short(d['indirectCommands'], cap)}")
        if d.get("shaderTableExports") is not None:
            print(f"    shader tables: {d['shaderTableExports']}")
        for b in d["bindings"]:
            head = f"    root[{b['rootParam']}] {b['type']:9s} vis={b['visibility']}"
            if b["type"] == "table":
                print(head)
                for x in b["descriptors"]:
                    print(f"        {x['register'] or '?':5s} heap[{x['heapSlot']}] {x['kind']:5s} {x['resource'] or ''}")
            elif b["type"] == "constants":
                print(f"{head} {b['register']} = {b['values']}")
            else:
                print(f"{head} {b['register']} = {b['gpuva']['resource']}+{b['gpuva']['offset']}")
        if "renderTargets" in d:
            print(f"    IA: {d['topology']} VB={[(v['slot'], v['resource'], v['stride']) for v in d['vertexBuffers']]} "
                  f"IB={(d['indexBuffer'] or {}).get('resource')}")
            print(f"    OM: RTV={[r['resource'] for r in d['renderTargets']]} "
                  f"DSV={(d['depthStencil'] or {}).get('resource')}" + ("  (render pass)" if d.get("inRenderPass") else ""))


def cmd_probe(a) -> None:
    from .probe import report
    print(report(a.file, a.manifest, a.block))


def cmd_meta(a) -> None:
    from .d3dmeta.header_parser import load_db
    db = load_db(rebuild=True)
    print(f"interfaces={len(db.interfaces)} structs={len(db.structs)} enums={len(db.enums)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="gcap", description="GPU capture binary analyzer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    wp = sub.add_parser("wpix", help="PIX GPU capture (.wpix)")
    wsub = wp.add_subparsers(dest="wcmd", required=True)
    p = wsub.add_parser("info"); p.add_argument("file"); p.set_defaults(fn=cmd_info)
    p = wsub.add_parser("calls"); p.add_argument("file"); p.add_argument("--json", action="store_true")
    p.add_argument("--failed", action="store_true"); p.set_defaults(fn=cmd_calls)
    p = wsub.add_parser("cmdlists"); p.add_argument("file"); p.set_defaults(fn=cmd_cmdlists)
    p = wsub.add_parser("objects"); p.add_argument("file"); p.set_defaults(fn=cmd_objects)
    p = wsub.add_parser("draws"); p.add_argument("file"); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_draws)
    p = wsub.add_parser("extract"); p.add_argument("file"); p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("probe", help="format-agnostic binary probe")
    p.add_argument("file"); p.add_argument("--manifest"); p.add_argument("--block", type=int, default=4096)
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("meta"); p.add_argument("action", choices=["rebuild"]); p.set_defaults(fn=cmd_meta)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
