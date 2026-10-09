"""解析用ヘルパ: レコードの生ペイロードを hex ダンプする / 複数キャプチャ間で同じ seq を比較する。

    python -m gcap.wpix.inspect CAPTURE.wpix --seq 67 [--seq 77] [--max 512]
    python -m gcap.wpix.inspect v0.wpix v1.wpix --seq 49 --diff
"""
from __future__ import annotations

import argparse

from .container import WpixFile
from .decoder import META_SIZE, ApiDecoder
from .events import all_records


def hexdump(b: bytes, maxlen: int = 512, base: int = 0) -> str:
    lines = []
    for o in range(0, min(len(b), maxlen), 16):
        chunk = b[o:o + 16]
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        lines.append(f"  {base + o:06x}: {chunk.hex(' '):<48}  {asc}")
    if len(b) > maxlen:
        lines.append(f"  ... ({len(b)} bytes)")
    return "\n".join(lines)


def diff_bytes(a: bytes, b: bytes) -> str:
    """同じ長さの部分を比較し、異なるバイト位置を列挙する (差分解析用)。"""
    n = min(len(a), len(b))
    diffs = [i for i in range(n) if a[i] != b[i]]
    out = [f"  len {len(a)} vs {len(b)}, {len(diffs)} differing bytes in common prefix"]
    i = 0
    while i < len(diffs):
        j = i
        while j + 1 < len(diffs) and diffs[j + 1] <= diffs[j] + 4:
            j += 1
        s, e = diffs[i], diffs[j] + 1
        out.append(f"  @{s:#06x}-{e:#06x}: {a[s:e].hex(' ')}  |  {b[s:e].hex(' ')}")
        i = j + 1
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--seq", type=int, action="append", default=[])
    ap.add_argument("--type", type=int, action="append", default=[])
    ap.add_argument("--max", type=int, default=512)
    ap.add_argument("--diff", action="store_true")
    a = ap.parse_args()
    dec = ApiDecoder()
    bodies: dict[tuple[int, int], list[bytes]] = {}
    for cap in a.captures:
        w = WpixFile(cap)
        for r in all_records(w):
            if r.seq in a.seq or r.type in a.type:
                body = r.payload[META_SIZE:]
                bodies.setdefault((r.seq, r.type), []).append(body)
                if not a.diff:
                    print(f"[{cap}] seq={r.seq} type={r.type} {dec.api_name(r.type)} len={len(body)}")
                    print(hexdump(body, a.max))
    if a.diff:
        for (seq, typ), bs in bodies.items():
            print(f"seq={seq} type={typ} {dec.api_name(typ)}")
            for other in bs[1:]:
                print(diff_bytes(bs[0], other))


if __name__ == "__main__":
    main()
