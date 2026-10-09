"""フォーマット非依存のバイナリ調査ツール (未知のキャプチャ形式の初期解析用。Nsight Graphics 等にも使う)。

  * ASCII/UTF-16 タグ・文字列の抽出
  * ブロック単位のエントロピー (圧縮/非圧縮領域の推定)
  * 既知値 (u32/u64/f32/文字列) の検索。manifest(JSON) を渡すとアプリが埋め込んだ値を一括検索
  * 圧縮形式の総当たり (zlib/gzip/LZ4風/Windows Compression API)
"""
from __future__ import annotations

import json
import math
import re
import struct
import zlib
from collections import Counter
from pathlib import Path


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    c = Counter(b)
    n = len(b)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def entropy_map(data: bytes, block: int = 4096) -> list[tuple[int, float]]:
    return [(o, entropy(data[o:o + block])) for o in range(0, len(data), block)]


def find_tags(data: bytes, min_len: int = 4) -> list[tuple[int, str]]:
    """'BLOKCOMP' のような大文字/記号の 4-8 文字タグ候補を列挙。"""
    out = []
    for m in re.finditer(rb"[A-Z0-9<>_]{%d,8}" % min_len, data):
        out.append((m.start(), m.group().decode()))
    return out


def find_strings(data: bytes, min_len: int = 6) -> list[tuple[int, str, str]]:
    out = []
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data):
        out.append((m.start(), "ascii", m.group().decode()))
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len, data):
        out.append((m.start(), "utf16", m.group().decode("utf-16-le")))
    return sorted(out)


def find_values(data: bytes, values: dict[str, bytes]) -> dict[str, list[int]]:
    hits: dict[str, list[int]] = {}
    for name, pat in values.items():
        hits[name] = [m.start() for m in re.finditer(re.escape(pat), data)]
    return hits


def manifest_patterns(manifest: dict) -> dict[str, bytes]:
    """DX12CaptureTarget の manifest(JSON) から検索パターンを作る。"""
    pats: dict[str, bytes] = {}
    for k, v in manifest.items():
        if isinstance(v, list) and v and all(isinstance(x, int) for x in v):
            pats[k] = struct.pack(f"<{len(v)}I", *v)
            for i, x in enumerate(v):
                if x > 0xFFFF:  # 小さい値は誤検出が多いので単体検索しない
                    pats[f"{k}[{i}]"] = struct.pack("<I", x)
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            pats[k] = struct.pack(f"<{len(v)}f", *v)
        elif isinstance(v, int) and v > 0xFFFF:
            pats[k] = struct.pack("<I", v)
        elif isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            for s in v:
                pats[f"{k}:{s}:utf16"] = s.encode("utf-16-le")
                pats[f"{k}:{s}:ascii"] = s.encode()
    return pats


def try_decompress(data: bytes, raw_size: int | None = None) -> list[str]:
    """先頭から各種圧縮形式で展開を試みる。成功した形式名を返す。"""
    ok = []
    for name, fn in (("zlib", lambda b: zlib.decompress(b)),
                     ("raw-deflate", lambda b: zlib.decompress(b, -15)),
                     ("gzip", lambda b: zlib.decompress(b, 31))):
        try:
            fn(data)
            ok.append(name)
        except zlib.error:
            pass
    if raw_size:
        try:
            from . import wincompress as wc
            for n, alg in wc.ALGORITHMS.items():
                for flag in (0, wc.COMPRESS_RAW):
                    try:
                        out = wc.decompress(data, raw_size, alg | flag)
                        if out:
                            ok.append(f"wincompress:{n}{'+raw' if flag else ''}")
                    except OSError:
                        pass
        except OSError:
            pass
    return ok


def report(path: str, manifest: str | None = None, block: int = 4096, max_items: int = 60) -> str:
    data = Path(path).read_bytes()
    lines = [f"file: {path} ({len(data)} bytes)", f"head: {data[:32].hex(' ')}", f"tail: {data[-32:].hex(' ')}"]
    tags = Counter(t for _, t in find_tags(data))
    lines.append("frequent tags: " + ", ".join(f"{t}x{n}" for t, n in tags.most_common(25)))
    em = entropy_map(data, block)
    lines.append(f"entropy per {block}B block (0-8; >7.5 suggests compressed/encrypted):")
    row = []
    for o, e in em:
        row.append(f"{e:3.1f}")
        if len(row) == 16:
            lines.append("  " + " ".join(row))
            row = []
    if row:
        lines.append("  " + " ".join(row))
    strs = find_strings(data)
    lines.append(f"strings ({len(strs)}):")
    for o, kind, s in strs[:max_items]:
        lines.append(f"  {o:#010x} {kind:5s} {s[:100]}")
    if manifest:
        pats = manifest_patterns(json.loads(Path(manifest).read_text()))
        hits = find_values(data, pats)
        lines.append("manifest value hits:")
        for k, offs in hits.items():
            lines.append(f"  {k:50s} {len(offs):4d} {[hex(o) for o in offs[:6]]}")
    return "\n".join(lines)
