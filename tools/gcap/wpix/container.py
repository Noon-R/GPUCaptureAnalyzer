"""PIX GPU capture (.wpix) コンテナ層のパーサ。

解析結果 (PIX 2603.25, docs/formats/wpix.md 参照):

    File
    +0x000  "GFXA" u32(0xFFFFFFFF) "GFXB" u32 u32 ...      ファイルヘッダ (0x400 バイト)
    +0x400  Section "<CAP" ... "CAP>"                        (DIR1 エントリで参照)
    ...     DIR1 テーブル + フッタ32B(u8[16], u64 tableBytes, u32 count, "DIR0")
    end-0x400 ファイルトレーラ (0x400 バイト, 末尾 "GFXB" "GFXA")

    Section "<CAP" (0x40 バイトヘッダ: "<CAP" u32 version ...)
      BLOKCOMP ブロック列 (各ストリーム)
      DIR3 テーブル + フッタ32B(u8[16], u64 tableBytes, u32 count, "DIR2")
      セクショントレーラ (0x40 バイト, 末尾 "CAP>")

    DIRn エントリ (32 バイト): char[4] tag, u32 id, u64 offset(ファイル先頭から), u64 size, u64 reserved
    BLOKCOMP: "BLOKCOMP" u64 size(以降のバイト数) "CBLKDATA" u64 rawSize, XPRESS(LZ77, RAW) 圧縮データ
    BLOKDATA: "BLOKDATA" u64 size, 非圧縮データ
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .. import wincompress


class WpixFormatError(Exception):
    pass


@dataclass
class DirEntry:
    tag: str
    id: int
    offset: int
    size: int
    reserved: int


@dataclass
class Block:
    offset: int          # BLOKCOMP の位置
    comp_size: int       # BLOKCOMP/BLOKDATA の size フィールド
    raw_size: int
    kind: str            # "CBLKDATA" (XPRESS 圧縮) / "RAW" (BLOKDATA)
    data_offset: int
    data_size: int


@dataclass
class Stream:
    id: int
    entry: DirEntry
    blocks: list[Block] = field(default_factory=list)
    _data: bytes | None = None

    @property
    def data(self) -> bytes:
        assert self._data is not None
        return self._data


@dataclass
class Section:
    tag: str             # "CAP"
    entry: DirEntry
    version: int
    streams: dict[int, Stream] = field(default_factory=dict)
    footer_unknown: bytes = b""
    header_bytes: bytes = b""


def _read_dir_table(buf: bytes, footer_end: int, expect_footer_tag: bytes) -> tuple[list[DirEntry], int]:
    """footer_end (フッタ直後の位置) から逆に読み、エントリ一覧とテーブル開始位置を返す。"""
    tag = buf[footer_end - 4: footer_end]
    if tag != expect_footer_tag:
        raise WpixFormatError(f"expected footer {expect_footer_tag!r} at {footer_end - 4:#x}, got {tag!r}")
    # フッタは 32 バイト: u8[16] reserved, u64 tableBytes, u32 count, char[4] tag
    table_bytes, count = struct.unpack_from("<QI", buf, footer_end - 16)
    start = footer_end - 32 - table_bytes
    if table_bytes != count * 32:
        raise WpixFormatError(f"table size mismatch {table_bytes:#x} != {count}*32")
    entries = []
    for i in range(count):
        t, eid, off, size, rsv = struct.unpack_from("<4sIQQQ", buf, start + i * 32)
        entries.append(DirEntry(t.decode("latin1"), eid, off, size, rsv))
    return entries, start


class WpixFile:
    def __init__(self, path: str | Path, decompress: bool = True):
        self.path = Path(path)
        self.buf = self.path.read_bytes()
        b = self.buf
        if b[:4] != b"GFXA" or b[8:12] != b"GFXB":
            raise WpixFormatError("not a wpix file (missing GFXA/GFXB header)")
        if b[-8:] != b"GFXBGFXA":
            raise WpixFormatError("missing GFXBGFXA trailer")
        self.header = b[:0x400]
        self.header_fields = struct.unpack_from("<4sI4sII", b, 0)
        self.header_u32_0x40 = struct.unpack_from("<II", b, 0x40)

        # ファイル末尾 0x400 バイトはトレーラ (先頭ヘッダと対称、末尾 "GFXBGFXA")
        self.trailer = b[-0x400:]
        self.top_entries, _ = _read_dir_table(b, len(b) - 0x400, b"DIR0")
        self.sections: list[Section] = []
        for e in self.top_entries:
            self.sections.append(self._parse_section(e, decompress))

    def _parse_section(self, e: DirEntry, decompress: bool) -> Section:
        b = self.buf
        start, end = e.offset, e.offset + e.size
        head = b[start:start + 4]
        if head[:1] != b"<":
            raise WpixFormatError(f"section at {start:#x} has unexpected tag {head!r}")
        name = head[1:].decode("latin1")
        if b[end - 4:end] != (name + ">").encode("latin1"):
            raise WpixFormatError(f"section {name} end tag mismatch at {end - 4:#x}")
        version = struct.unpack_from("<I", b, start + 4)[0]
        sec = Section(name, e, version)

        # セクション末尾 0x40 バイトはトレーラ (先頭 0x40 ヘッダと対称、末尾 "CAP>")
        sec.header_bytes = b[start:start + 0x40]
        sec.footer_unknown = b[end - 0x40:end - 4]
        entries, _ = _read_dir_table(b, end - 0x40, b"DIR2")
        for de in entries:
            st = Stream(de.id, de)
            st.blocks = self._parse_blocks(de.offset, de.offset + de.size)
            if decompress:
                st._data = b"".join(self._decompress_block(blk) for blk in st.blocks)
            sec.streams[de.id] = st
        return sec

    def _parse_blocks(self, start: int, end: int) -> list[Block]:
        b = self.buf
        blocks = []
        pos = start
        while pos < end:
            magic = b[pos:pos + 8]
            size = struct.unpack_from("<Q", b, pos + 8)[0]
            if magic == b"BLOKCOMP":
                kind = b[pos + 16:pos + 24].decode("latin1")
                raw_size = struct.unpack_from("<Q", b, pos + 24)[0]
                blocks.append(Block(pos, size, raw_size, kind, pos + 32, size - 16))
            elif magic == b"BLOKDATA":
                # 非圧縮ブロック (圧縮が効かないデータ: PNG スクリーンショット等)
                blocks.append(Block(pos, size, size, "RAW", pos + 16, size))
            else:
                raise WpixFormatError(f"expected BLOKCOMP/BLOKDATA at {pos:#x}, got {magic!r}")
            pos += 16 + size
        if pos != end:
            raise WpixFormatError(f"block chain overran stream end ({pos:#x} != {end:#x})")
        return blocks

    def _decompress_block(self, blk: Block) -> bytes:
        payload = self.buf[blk.data_offset: blk.data_offset + blk.data_size]
        if blk.kind == "RAW":
            return payload
        if blk.kind != "CBLKDATA":
            raise WpixFormatError(f"unknown block kind {blk.kind}")
        out = wincompress.decompress(payload, blk.raw_size)
        if len(out) != blk.raw_size:
            raise WpixFormatError(f"decompressed size mismatch at {blk.offset:#x}")
        return out

    @property
    def streams(self) -> dict[int, Stream]:
        out: dict[int, Stream] = {}
        for s in self.sections:
            out.update(s.streams)
        return out
