"""高レベル API: .wpix を開いて API 呼び出し列・オブジェクト・リソース初期データを取り出す。

    from gcap.wpix.capture import Capture
    cap = Capture.open("v0.wpix")
    for call in cap.calls: ...
    cap.command_lists()      # ExecuteCommandLists ごとのコマンド列
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .container import WpixFile
from .decoder import ApiDecoder, Call
from .events import EvtRecord, all_records


@dataclass
class Capture:
    path: Path
    file: WpixFile
    records: list[EvtRecord]
    calls: list[Call]
    decoder: ApiDecoder

    @staticmethod
    def open(path: str | Path, decoder: ApiDecoder | None = None) -> "Capture":
        f = WpixFile(path)
        recs = all_records(f)
        dec = decoder or ApiDecoder()
        dec.objects = {}
        calls = [dec.decode(r) for r in recs]
        return Capture(Path(path), f, recs, calls, dec)

    # ---- 便利関数 ----
    @property
    def objects(self) -> dict[int, dict[str, Any]]:
        return self.decoder.objects

    def object_name(self, oid: int | None) -> str:
        if not oid:
            return "null"
        o = self.objects.get(oid, {})
        n = o.get("name")
        return f"#{oid}({n})" if n else f"#{oid}"

    def find(self, api_suffix: str) -> list[Call]:
        return [c for c in self.calls if c.api.endswith(api_suffix)]

    def failed(self) -> list[Call]:
        return [c for c in self.calls if not c.ok]

    def command_lists(self) -> list[dict]:
        """ExecuteCommandLists を起点に、各コマンドリストの記録 (seq 範囲) を取り出す。"""
        by_seq = {(c.seq, c.stream): c for c in self.calls}
        list_stream = {c.stream for c in self.calls if c.api.startswith("ID3D12GraphicsCommandList::")}
        out = []
        for ecl in self.find("::ExecuteCommandLists"):
            for lst in ecl.args.get("lists", []):
                cmds = []
                for s in range(lst["firstSeq"], lst["firstSeq"] + lst["numRecords"]):
                    for st in list_stream:
                        c = by_seq.get((s, st))
                        if c:
                            cmds.append(c)
                out.append({"queue": ecl.args.get("this"), "executeSeq": ecl.seq, "list": lst["list"],
                            "commands": cmds})
        return out

    def resource_initial_data(self) -> Iterator[tuple[int, dict]]:
        """(resourceId, 2029 の args) を返す。args['data'] は bytes のまま保持するため生レコードから再デコード。"""
        from .serializer import Reader
        from .pix_events import _resource_initial_data
        from .decoder import META_SIZE
        for r in self.records:
            if r.type == 2029:
                d = _resource_initial_data(Reader(r.payload[META_SIZE:]), self.decoder)
                yield d["resource"], d

    def blobs(self) -> Iterator[dict]:
        from .serializer import Reader
        from .pix_events import _blob_data
        from .decoder import META_SIZE
        for r in self.records:
            if r.type == 37:
                yield _blob_data(Reader(r.payload[META_SIZE:]), self.decoder)

    def raw_args(self, call: Call) -> dict:
        """bytes を保持したままのデコード結果 (to_json 用の短縮をしない)。"""
        rec = next(r for r in self.records if r.seq == call.seq and r.stream_id == call.stream)
        from .serializer import Reader
        from .decoder import META_SIZE
        body = rec.payload[META_SIZE:]
        rd = Reader(body)
        if rec.type in self.decoder.custom:
            return self.decoder.custom[rec.type][1](rd, self.decoder)
        iface, m = self.decoder.type_map[rec.type]
        out: dict[str, Any] = {"this": rd.u32()}
        if m.ret == "HRESULT":
            out["hr"] = rd.u32()
        for p in m.params:
            out[p.name] = self.decoder.read_param(rd, p, m.name)
        return out
