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

    def _list_records(self, list_id: int, first: int, num: int) -> list[Call]:
        """seq >= first で this == list_id のコマンドリスト呼び出しを先頭から num 件。
        num は seq の範囲幅ではなく「そのリスト自身のレコード数」(間に Bundle の記録や PIX のスナップショット
        レコードが挟まるため)。"""
        if not hasattr(self, "_list_calls"):
            self._list_calls: dict[int, list[Call]] = {}
            for c in self.calls:   # calls は seq 順
                if c.api.startswith("ID3D12GraphicsCommandList") and c.this is not None:
                    self._list_calls.setdefault(c.this, []).append(c)
                elif c.api == "PIX::PrepareForPresent":   # リストに属する PIX 独自イベント
                    self._list_calls.setdefault(c.args.get("commandList"), []).append(c)
        out = []
        for c in self._list_calls.get(list_id, []):
            if c.seq >= first:
                out.append(c)
                if len(out) == num:
                    break
        return out

    def command_lists(self) -> list[dict]:
        """ExecuteCommandLists を起点に、各コマンドリストの記録 (seq 範囲) を取り出す。"""
        out = []
        for ecl in self.find("::ExecuteCommandLists"):
            for lst in ecl.args.get("lists", []):
                cmds = self._list_records(lst["list"], lst["firstSeq"], lst["numRecords"])
                out.append({"queue": ecl.args.get("this"), "executeSeq": ecl.seq, "list": lst["list"],
                            "commands": cmds})
        return out

    def bundle_commands(self, execute_bundle: Call) -> list[Call]:
        """ExecuteBundle が実行する Bundle の記録 (Reset/Close を除く)。"""
        info = execute_bundle.args.get("_pix") or {}
        if "list" not in info:
            return []
        cmds = self._list_records(info["list"], info["firstSeq"], info["numRecords"])
        return [c for c in cmds if c.api.split("::")[-1] not in ("Reset", "Close")]

    def call_by_seq(self, seq: int, api: str) -> Call | None:
        return next((c for c in self.calls if c.seq == seq and c.api == api), None)

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
