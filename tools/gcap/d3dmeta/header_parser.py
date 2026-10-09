"""Windows SDK ヘッダ (d3d12.h, dxgi*.h) から COM インターフェース (vtable 順のメソッド)・構造体・列挙型を抽出する。

PIX の EVT type ID は「インターフェース毎のベース値 + vtable インデックス」になっており、
引数は宣言順・SAL アノテーションに従ってシリアライズされる。よってヘッダから汎用デコーダを組み立てられる。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SDK_INCLUDE_ROOT = Path(r"C:\Program Files (x86)\Windows Kits\10\Include")
DEFAULT_HEADERS = [
    "um/d3d12.h",
    "shared/dxgi.h", "shared/dxgi1_2.h", "shared/dxgi1_3.h", "shared/dxgi1_4.h",
    "shared/dxgi1_5.h", "shared/dxgi1_6.h", "shared/dxgicommon.h", "shared/dxgiformat.h",
    "shared/dxgitype.h", "um/d3dcommon.h",
]


@dataclass
class Param:
    name: str
    type: str            # 基本型名 (const / * を除いたもの)
    ptr: int             # ポインタ段数
    const: bool
    sal: str             # SAL アノテーション全体 (例: "_In_reads_(NumBarriers)")
    array: str = ""      # 固定長配列 [N]


@dataclass
class Method:
    name: str
    declared_in: str     # DECLSPEC_XFGVIRT の第1引数 (メソッドを宣言したインターフェース)
    ret: str
    params: list[Param]  # This を除く


@dataclass
class Interface:
    name: str
    iid: str = ""
    methods: list[Method] = field(default_factory=list)


@dataclass
class StructField:
    name: str
    type: str
    ptr: int
    const: bool
    array: str = ""
    union: int = -1      # 同一 union 内のメンバは同じ番号


@dataclass
class Struct:
    name: str
    fields: list[StructField]


@dataclass
class HeaderDB:
    interfaces: dict[str, Interface] = field(default_factory=dict)
    structs: dict[str, Struct] = field(default_factory=dict)
    enums: dict[str, dict[str, int]] = field(default_factory=dict)
    typedefs: dict[str, str] = field(default_factory=dict)

    # ---- (de)serialize ----
    def to_json(self) -> dict:
        return {
            "interfaces": {k: asdict(v) for k, v in self.interfaces.items()},
            "structs": {k: asdict(v) for k, v in self.structs.items()},
            "enums": self.enums,
            "typedefs": self.typedefs,
        }

    @staticmethod
    def from_json(d: dict) -> "HeaderDB":
        db = HeaderDB()
        for k, v in d["interfaces"].items():
            ms = [Method(m["name"], m["declared_in"], m["ret"], [Param(**p) for p in m["params"]]) for m in v["methods"]]
            db.interfaces[k] = Interface(v["name"], v["iid"], ms)
        for k, v in d["structs"].items():
            db.structs[k] = Struct(v["name"], [StructField(**f) for f in v["fields"]])
        db.enums = d["enums"]
        db.typedefs = d["typedefs"]
        return db

    def enum_name(self, enum: str, value: int) -> str | None:
        e = self.enums.get(enum)
        if not e:
            return None
        for k, v in e.items():
            if v == value:
                return k
        return None

    def iid_to_interface(self) -> dict[str, str]:
        return {i.iid.lower(): n for n, i in self.interfaces.items() if i.iid}


# ------------------------------------------------------------------------------------------------
_RE_VTBL = re.compile(r"typedef struct (\w+)Vtbl\s*\{(.*?)\}\s*\w+Vtbl;", re.S)
_RE_METHOD = re.compile(
    r"DECLSPEC_XFGVIRT\((\w+),\s*(\w+)\)\s*(.*?)\(\s*STDMETHODCALLTYPE\s*\*\s*(\w+)\s*\)\s*\((.*?)\);", re.S)
_RE_IID = re.compile(r'MIDL_INTERFACE\("([0-9A-Fa-f-]{36})"\)\s*\n\s*(\w+)\s*:')
_RE_STRUCT = re.compile(r"typedef struct (\w+)\s*\{(.*?)\}\s*(\w+)\s*;", re.S)
_RE_ENUM = re.compile(r"typedef\s+enum\s+(\w+)\s*\{(.*?)\}\s*(\w+)\s*;", re.S)
_RE_SAL = re.compile(r"(_[A-Z][A-Za-z_]*_(?:\s*\((?:[^()]|\([^()]*\))*\))?)")
_RE_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_RE_SIMPLE_TYPEDEF = re.compile(r"typedef\s+([\w ]+?\**)\s+(\w+)\s*;")


def _parse_decl(decl: str) -> tuple[str, str, int, bool, str, str]:
    """'_In_reads_(N) const D3D12_X *pX' -> (name, type, ptr, const, sal, array)"""
    decl = " ".join(decl.split())
    sal_parts = []
    while True:
        m = _RE_SAL.match(decl)
        if not m:
            break
        sal_parts.append(m.group(1).replace(" ", ""))
        decl = decl[m.end():].strip()
    array = ""
    am = re.search(r"\[([^\]]*)\]\s*$", decl)
    if am:
        array = am.group(1).strip()
        decl = decl[:am.start()].strip()
    ptr = decl.count("*")
    decl = decl.replace("*", " ")
    toks = decl.split()
    const = "const" in toks
    toks = [t for t in toks if t not in ("const", "struct", "enum", "interface", "__RPC__deref_out", "__RPC__in")]
    name = toks[-1] if len(toks) >= 2 else ""
    typ = " ".join(toks[:-1]) if len(toks) >= 2 else (toks[0] if toks else "")
    return name, typ, ptr, const, " ".join(sal_parts), array


def _split_params(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _parse_struct_body(body: str) -> list[StructField]:
    fields: list[StructField] = []
    union_id = [0]

    def parse_block(text: str, union: int):
        i = 0
        while i < len(text):
            # union / struct ブロック
            m = re.match(r"\s*(union|struct)\s*(\w*)\s*\{", text[i:])
            if m:
                depth, j = 1, i + m.end()
                while depth and j < len(text):
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                inner = text[i + m.end(): j - 1]
                endm = re.match(r"\s*(\w*)\s*;", text[j:])
                if m.group(1) == "union":
                    union_id[0] += 1
                    parse_block(inner, union_id[0])
                else:
                    parse_block(inner, union)
                i = j + (endm.end() if endm else 0)
                continue
            semi = text.find(";", i)
            if semi < 0:
                break
            decl = text[i:semi].strip()
            i = semi + 1
            if not decl:
                continue
            name, typ, ptr, const, _sal, array = _parse_decl(decl)
            if name:
                fields.append(StructField(name, typ, ptr, const, array, union))

    parse_block(body, -1)
    return fields


def _eval_enum_value(expr: str, known: dict[str, int]) -> int | None:
    expr = expr.strip()
    expr = re.sub(r"(?<=[0-9a-fA-F])[uUlL]+\b", "", expr)
    try:
        return int(eval(expr, {"__builtins__": {}}, dict(known)))  # noqa: S307 (SDK ヘッダの定数式のみ)
    except Exception:
        return None


def parse_headers(paths: list[Path]) -> HeaderDB:
    db = HeaderDB()
    all_enum_consts: dict[str, int] = {}
    for p in paths:
        src = _RE_COMMENT.sub("", p.read_text(encoding="utf-8", errors="replace"))
        for m in _RE_IID.finditer(src):
            db.interfaces.setdefault(m.group(2), Interface(m.group(2))).iid = m.group(1).lower()
        for m in _RE_VTBL.finditer(src):
            iname, body = m.group(1), m.group(2)
            itf = db.interfaces.setdefault(iname, Interface(iname))
            itf.methods = []
            for mm in _RE_METHOD.finditer(body):
                declared_in, _mname, ret, mname, params = mm.groups()
                plist = _split_params(params)[1:]  # This を除く
                ps = []
                for pd in plist:
                    name, typ, ptr, const, sal, array = _parse_decl(pd)
                    ps.append(Param(name, typ, ptr, const, sal, array))
                itf.methods.append(Method(mname, declared_in, " ".join(ret.split()), ps))
        for m in _RE_STRUCT.finditer(src):
            if m.group(1).endswith("Vtbl") or m.group(3).endswith("Vtbl"):
                continue
            db.structs[m.group(3)] = Struct(m.group(3), _parse_struct_body(m.group(2)))
        for m in _RE_ENUM.finditer(src):
            vals: dict[str, int] = {}
            cur = -1
            for item in m.group(2).split(","):
                item = item.strip()
                if not item:
                    continue
                if "=" in item:
                    k, v = item.split("=", 1)
                    val = _eval_enum_value(v, {**all_enum_consts, **vals})
                    cur = val if val is not None else cur + 1
                    k = k.strip()
                else:
                    k = item
                    cur += 1
                vals[k] = cur
            all_enum_consts.update(vals)
            db.enums[m.group(3)] = vals
        for m in _RE_SIMPLE_TYPEDEF.finditer(src):
            val = " ".join(m.group(1).split())
            if val.startswith("interface ") or val == m.group(2):
                continue
            db.typedefs.setdefault(m.group(2), val)
    # Vtbl を持たない forward 宣言だけのものは除外
    db.interfaces = {k: v for k, v in db.interfaces.items() if v.methods}
    return db


def find_sdk_include() -> Path:
    vers = sorted(p for p in SDK_INCLUDE_ROOT.iterdir() if (p / "um" / "d3d12.h").exists())
    if not vers:
        raise FileNotFoundError("Windows SDK with d3d12.h not found")
    return vers[-1]


CACHE = Path(__file__).with_name("d3d12_meta.json")


def load_db(rebuild: bool = False) -> HeaderDB:
    if CACHE.exists() and not rebuild:
        return HeaderDB.from_json(json.loads(CACHE.read_text(encoding="utf-8")))
    inc = find_sdk_include()
    db = parse_headers([inc / h for h in DEFAULT_HEADERS if (inc / h).exists()])
    CACHE.write_text(json.dumps(db.to_json(), indent=1), encoding="utf-8")
    return db


if __name__ == "__main__":
    db = load_db(rebuild=True)
    print(f"interfaces={len(db.interfaces)} structs={len(db.structs)} enums={len(db.enums)}")
    for n in ("ID3D12GraphicsCommandList", "ID3D12CommandQueue", "ID3D12Device", "IDXGISwapChain"):
        itf = db.interfaces[n]
        print(n, itf.iid, len(itf.methods), [m.name for m in itf.methods[:12]])
    print(db.structs["D3D12_RESOURCE_BARRIER"])
    print(db.enums["D3D12_RESOURCE_STATES"].get("D3D12_RESOURCE_STATE_UNORDERED_ACCESS"))
