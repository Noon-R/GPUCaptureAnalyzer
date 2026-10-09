"""PIX 独自イベント (D3D12 API に直接対応しないもの) のデコーダ。

ID と意味は解析で判明したもの (PIX 2603.25)。確度が低いフィールドは名前に '?' / 'unk' を付ける。
詳細と根拠は docs/formats/wpix.md 参照。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .serializer import DecodeError, Reader

if TYPE_CHECKING:
    from .decoder import ApiDecoder

GUID_SCREENSHOT_PNG = "7c29896a-6790-4b40-ab07-f9876ee0e614"


def _filetime(v: int) -> str:
    # FILETIME (100ns 単位) -> マイクロ秒 (整数演算で偶数丸め。float を経由すると桁落ちする)
    us, rem = divmod(v, 10)
    if rem > 5 or (rem == 5 and us % 2 == 1):
        us += 1
    try:
        return (datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=us)).isoformat()
    except OverflowError:
        return str(v)


def _opt(r: Reader, fn) -> Any:
    return fn() if r.flag() else None


def _wstr(r: Reader) -> str | None:
    if not r.flag():
        return None
    return r.wstr(r.u64())


# ------------------------------------------------------------------------------------------------
def _execute_command_lists(r: Reader, d: "ApiDecoder") -> dict:
    # ID3D12CommandQueue::ExecuteCommandLists の PIX 形式 (汎用規則と異なる):
    #   u32 this, u32 NumCommandLists, u64 count, { u32 list, u64 firstSeq, u64 numRecords } x count
    #   firstSeq/numRecords は そのコマンドリストの記録 (stream 1001 の EVT seq 範囲)
    this = r.u32()
    num = r.u32()
    cnt = r.u64()
    lists = [{"list": r.u32(), "firstSeq": r.u64(), "numRecords": r.u64()} for _ in range(cnt)]
    return {"this": this, "NumCommandLists": num, "lists": lists}


def _is_buffer(desc: dict) -> bool:
    return desc.get("Dimension") in (1, "D3D12_RESOURCE_DIMENSION_BUFFER")


def _resource_state_and_tail(r: Reader, d: "ApiDecoder", out: dict, desc: dict, extra_u32: bool = False) -> None:
    """リソース生成系 (1700/1767/1770) 共通の後半部。
    レガシーステート:      u32 state, [u32 unk], opt CLEAR_VALUE, riid, u64 initialDataSeq, ..., u64 n, u32 states[n]
    レイアウト (Enhanced Barriers で作成したテクスチャ): u32 1, u32 layout, opt CLEAR_VALUE, riid, u64 seq,
                           u64 n, {u32 ?, u32 layout}[n], u64 ?   (🔶 state==1 はテクスチャでは無効値なので判別に使う)
    """
    state = r.u32()
    layout_mode = (not _is_buffer(desc)) and state == 1
    if layout_mode:
        out["layoutAtCaptureStart"] = d.db.enum_name("D3D12_BARRIER_LAYOUT", r.u32()) or "?"
    else:
        out["stateAtCaptureStart"] = state   # 作成時ではなくキャプチャ開始時のステート
        if extra_u32:
            out["unk?"] = r.u32()
    # D3D12_CLEAR_VALUE は union の両メンバ (Color[4] と DepthStencil) が書かれる
    out["optimizedClearValue"] = _opt(r, lambda: d.read_type(r, "D3D12_CLEAR_VALUE"))
    out["riid"] = r.guid()
    out["initialDataSeq"] = r.u64()      # 対応する 2029 (初期内容) レコードの seq


def _subresource_states(r: Reader, d: "ApiDecoder", out: dict) -> None:
    n = r.u64()
    if "layoutAtCaptureStart" in out:
        subs = []
        for _ in range(n):
            unk = r.u32()
            subs.append({"unk?": unk, "layout": d.db.enum_name("D3D12_BARRIER_LAYOUT", r.u32()) or "?"})
        out["subresourceLayoutsAtCapture"] = subs
        out["unkTail?"] = r.u64()
    else:
        out["subresourceStatesAtCapture"] = [r.u32() for _ in range(n)]


def _create_resource(r: Reader, d: "ApiDecoder") -> dict:
    # 1700: キャプチャ開始時点で存在したリソース (committed) の再生成情報
    out: dict[str, Any] = {"device": r.u32(), "resource": r.u32()}
    out["heapProperties"] = d.read_type(r, "D3D12_HEAP_PROPERTIES")
    out["heapFlags"] = r.u32()
    out["desc"] = d.read_type(r, "D3D12_RESOURCE_DESC1")   # SamplerFeedbackMipRegion 付き (60B)
    _resource_state_and_tail(r, d, out, out["desc"])
    _subresource_states(r, d, out)
    d.note_object(out["resource"], interface="ID3D12Resource", created_by="PIX::CreateResource",
                  desc=out["desc"], heapType=out["heapProperties"].get("Type"))
    return out


def _create_placed_resource(r: Reader, d: "ApiDecoder") -> dict:
    # 1767: Placed リソース (ID3D12Heap 上のオフセットに配置)
    out: dict[str, Any] = {"device": r.u32(), "resource": r.u32(), "heap": r.u32(), "heapOffset": r.u64()}
    out["desc"] = d.read_type(r, "D3D12_RESOURCE_DESC1")
    _resource_state_and_tail(r, d, out, out["desc"])
    _subresource_states(r, d, out)
    d.note_object(out["resource"], interface="ID3D12Resource", created_by="PIX::CreatePlacedResource",
                  desc=out["desc"], heap=out["heap"], heapOffset=out["heapOffset"])
    return out


def _create_reserved_resource(r: Reader, d: "ApiDecoder") -> dict:
    # 1770: Reserved (tiled) リソース。タイルマッピングは PIX::TileMappings (2037) が別に記録される
    out: dict[str, Any] = {"device": r.u32(), "resource": r.u32()}
    out["desc"] = d.read_type(r, "D3D12_RESOURCE_DESC1")
    _resource_state_and_tail(r, d, out, out["desc"], extra_u32=True)
    out["tileMappingsSeq"] = r.u64()     # 対応する 2037 (タイルマッピング) レコードの seq
    _subresource_states(r, d, out)
    out["tilingRaw?"] = r.take(r.remaining())   # GetResourceTiling 相当と推定 ❓
    d.note_object(out["resource"], interface="ID3D12Resource", created_by="PIX::CreateReservedResource",
                  desc=out["desc"])
    return out


def _tile_mappings(r: Reader, d: "ApiDecoder") -> dict:
    # 2037: Reserved リソースのキャプチャ開始時点のタイルマッピング ❓ (先頭のリソース ID 以外は未解析)
    return {"resource": r.u32(), "raw?": r.take(r.remaining())}


def _accel_struct_data(r: Reader, d: "ApiDecoder") -> dict:
    # 2041: 加速構造 (BLAS/TLAS) の内容。D3D12 のシリアライズ形式 (ドライバ依存の不透明データ) と推定
    out: dict[str, Any] = {"unk0?": r.u64(), "resource": r.u32(), "size?": r.u32(), "unk1?": r.u32()}
    blob = r.take(r.remaining())
    out["driverMatchingGuid?"] = str(__import__("uuid").UUID(bytes_le=blob[:16])) if len(blob) >= 16 else None
    out["serialized"] = blob
    return out


def _pipeline_desc_pix(r: Reader, d: "ApiDecoder") -> dict:
    """ID3D12Device2::CreatePipelineState の Stream を PIX が展開した固定構造 (wpix.md §3.5)。"""
    sb = "D3D12_SHADER_BYTECODE"
    out: dict[str, Any] = {"pRootSignature": {"obj": r.u32()}}
    for st in ("VS", "PS", "DS", "HS", "GS", "CS"):
        out[st] = d.read_type(r, sb)
    out["StreamOutput"] = d.read_type(r, "D3D12_STREAM_OUTPUT_DESC")
    out["BlendState"] = d.read_type(r, "D3D12_BLEND_DESC")
    out["SampleMask"] = r.u32()
    out["RasterizerState"] = d.read_type(r, "D3D12_RASTERIZER_DESC2")
    out["DepthStencilState"] = d.read_type(r, "D3D12_DEPTH_STENCIL_DESC2")
    out["InputLayout"] = d.read_type(r, "D3D12_INPUT_LAYOUT_DESC")
    out["IBStripCutValue"] = d.read_type(r, "D3D12_INDEX_BUFFER_STRIP_CUT_VALUE")
    out["PrimitiveTopologyType"] = d.read_type(r, "D3D12_PRIMITIVE_TOPOLOGY_TYPE")
    out["NumRenderTargets"] = r.u32()
    out["RTVFormats"] = d.read_field(r, "DXGI_FORMAT", 0, "8", "PIX_PIPELINE", "RTVFormats")
    out["DSVFormat"] = d.read_type(r, "DXGI_FORMAT")
    out["SampleDesc"] = d.read_type(r, "DXGI_SAMPLE_DESC")
    out["NodeMask"] = r.u32()
    out["CachedPSO"] = d.read_type(r, "D3D12_CACHED_PIPELINE_STATE")
    out["Flags"] = d.read_type(r, "D3D12_PIPELINE_STATE_FLAGS")
    out["unk16?"] = r.take(16).hex()       # ❓ (ViewInstancing 等と推定、全て 0 を観測)
    out["MS"] = d.read_type(r, sb)
    out["AS"] = d.read_type(r, sb)
    out["unk3?"] = r.take(3).hex()         # ❓
    return out


def _create_pipeline_state(r: Reader, d: "ApiDecoder") -> dict:
    # ID3D12Device2::CreatePipelineState (typeId 1629): Stream を固定構造に展開した PIX 形式
    out: dict[str, Any] = {"this": r.u32(), "hr": r.u32()}
    out["pDesc"] = _opt(r, lambda: _pipeline_desc_pix(r, d))
    out["riid"] = r.guid()
    pp = _opt(r, r.u32)
    out["ppPipelineState"] = {"obj": pp} if pp is not None else None
    if pp:
        d.note_object(pp, interface="ID3D12PipelineState", created_by="ID3D12Device2::CreatePipelineState")
    return out


# D3D12_STATE_SUBOBJECT_TYPE -> desc 構造体
_SUBOBJECT_DESC = {
    0: "D3D12_STATE_OBJECT_CONFIG", 1: "D3D12_GLOBAL_ROOT_SIGNATURE", 2: "D3D12_LOCAL_ROOT_SIGNATURE",
    3: "D3D12_NODE_MASK", 5: "D3D12_DXIL_LIBRARY_DESC", 6: "D3D12_EXISTING_COLLECTION_DESC",
    8: "D3D12_DXIL_SUBOBJECT_TO_EXPORTS_ASSOCIATION", 9: "D3D12_RAYTRACING_SHADER_CONFIG",
    10: "D3D12_RAYTRACING_PIPELINE_CONFIG", 11: "D3D12_HIT_GROUP_DESC", 12: "D3D12_RAYTRACING_PIPELINE_CONFIG1",
}


def _read_exports(r: Reader, d: "ApiDecoder", strings: bool = False) -> list:
    # state subobject 内のポインタ配列は present フラグなしで u64 count + 要素
    n = r.u64()
    if strings:
        return [d.read_string(r, wide=True) for _ in range(n)]
    return [d.read_type(r, "D3D12_EXPORT_DESC") for _ in range(n)]


def _read_subobject_desc(r: Reader, d: "ApiDecoder", st: int) -> dict:
    if st == 5:    # DXIL_LIBRARY
        return {"DXILLibrary": d.read_type(r, "D3D12_SHADER_BYTECODE"), "NumExports": r.u32(),
                "pExports": _read_exports(r, d)}
    if st == 6:    # EXISTING_COLLECTION
        return {"pExistingCollection": {"obj": r.u32()}, "NumExports": r.u32(), "pExports": _read_exports(r, d)}
    if st == 8:    # DXIL_SUBOBJECT_TO_EXPORTS_ASSOCIATION 🔶
        return {"SubobjectToAssociate": d.read_string(r, wide=True), "NumExports": r.u32(),
                "pExports": _read_exports(r, d, strings=True)}
    return d.read_type(r, _SUBOBJECT_DESC[st])


def _create_state_object(r: Reader, d: "ApiDecoder") -> dict:
    # 2045: CreateStateObject (DXR パイプライン等)。
    #   u32 ?, u32 device, u8 present + u64 count(=1) + D3D12_STATE_OBJECT_DESC {Type, NumSubobjects,
    #   u64 count + { u32 Type, u8 present + 種別ごとの desc }[]}, GUID riid, opt u32 stateObject
    out: dict[str, Any] = {"unk0": r.u32(), "device": r.u32()}
    desc = None
    if r.flag():
        cnt = r.u64()
        descs = []
        for _ in range(cnt):
            t = d.read_type(r, "D3D12_STATE_OBJECT_TYPE")
            num = r.u32()
            n = r.u64()
            subs = []
            for _ in range(n):
                st = r.u32()
                name = d.db.enum_name("D3D12_STATE_SUBOBJECT_TYPE", st) or st
                sdesc = None
                if r.flag():
                    if st == 7:   # SUBOBJECT_TO_EXPORTS_ASSOCIATION: pSubobjectToAssociate は index? ❓
                        raise DecodeError("SUBOBJECT_TO_EXPORTS_ASSOCIATION not yet understood")
                    if st not in _SUBOBJECT_DESC:
                        raise DecodeError(f"state subobject type {st} not yet understood")
                    sdesc = _read_subobject_desc(r, d, st)
                subs.append({"Type": name, "desc": sdesc})
            descs.append({"Type": t, "NumSubobjects": num, "pSubobjects": subs})
        desc = descs[0] if len(descs) == 1 else descs
    out["pDesc"] = desc
    out["unk16?"] = r.take(16).hex()     # ❓ (0 を観測。関連付け情報等と推定)
    out["riid"] = r.guid()
    out["ppStateObject"] = _opt(r, r.u32)
    if out["ppStateObject"]:
        d.note_object(out["ppStateObject"], interface="ID3D12StateObject", created_by="PIX::CreateStateObject")
    return out


def _indirect_arguments(r: Reader, d: "ApiDecoder") -> dict:
    """2030: ExecuteIndirect 実行時の引数バッファ内容 (コマンドシグネチャで解釈済み)。
    u8[16] ?, u64 numCommands, { (u32 argType + 引数) x 引数記述子数 } x numCommands,
    u32 maxCommandCount?, u32 commandCount?, u32 ?, u8[numCommands] ?, D3D12_COMMAND_SIGNATURE_DESC, u64 size, bytes
    """
    out: dict[str, Any] = {"unk16?": r.take(16).hex()}
    n = r.u64()
    sig_id = d.last_indirect_signature
    sig = (d.objects.get(sig_id, {}) if sig_id else {}).get("desc") or {}
    arg_descs = sig.get("pArgumentDescs") or []
    if n and not arg_descs:
        raise DecodeError("command signature for ExecuteIndirect not known")
    cmds = []
    for _ in range(n):
        args = []
        for ad in arg_descs:
            t = r.u32()
            args.append({"type": d.db.enum_name("D3D12_INDIRECT_ARGUMENT_TYPE", t) or t,
                         "args": _indirect_arg_payload(r, d, t, ad)})
        cmds.append(args)
    out["commands"] = cmds
    out["maxCommandCount?"] = r.u32()
    out["commandCount?"] = r.u32()
    out["unk?"] = r.u32()
    out["perCommand?"] = r.take(n).hex()
    out["signature"] = d.read_type(r, "D3D12_COMMAND_SIGNATURE_DESC")
    size = r.u64()
    out["argumentBufferBytes"] = r.take(size)
    return out


def _indirect_arg_payload(r: Reader, d: "ApiDecoder", t: int, ad: dict) -> Any:
    if t == 0:    # DRAW
        return d.read_type(r, "D3D12_DRAW_ARGUMENTS")
    if t == 1:    # DRAW_INDEXED
        return d.read_type(r, "D3D12_DRAW_INDEXED_ARGUMENTS")
    if t in (2, 10):  # DISPATCH / DISPATCH_MESH
        return d.read_type(r, "D3D12_DISPATCH_ARGUMENTS")
    if t == 5:    # CONSTANT
        num = (ad.get("Constant") or {}).get("Num32BitValuesToSet", 1)
        return [r.u32() for _ in range(num)]
    if t == 11:   # INCREMENTING_CONSTANT
        return r.u32()
    raise DecodeError(f"indirect argument type {t} not yet understood")


def _instance_descs(r: Reader, d: "ApiDecoder") -> dict:
    # 2039: TLAS ビルド時のインスタンス記述 (D3D12_RAYTRACING_INSTANCE_DESC, AS アドレスは GPUVA 形式)
    out: dict[str, Any] = {"unk16?": r.take(16).hex()}
    n = r.u64()
    insts = []
    for _ in range(n):
        tr = d.read_field(r, "FLOAT", 0, "12", "INSTANCE", "Transform")
        insts.append({"Transform": tr, "InstanceID": r.u32(), "InstanceMask": r.u32(),
                      "InstanceContributionToHitGroupIndex": r.u32(), "Flags": r.u32(),
                      "AccelerationStructure": d.read_type(r, "D3D12_GPU_VIRTUAL_ADDRESS")})
    out["instances"] = insts
    return out


def _shader_tables(r: Reader, d: "ApiDecoder") -> dict:
    # 2038: DispatchRays 実行時のシェーダテーブル内容 (識別子をエクスポート名に解決したもの) 🔶
    # 構造の一部が未解析のため、エクスポート名 (u8 present + u64 文字数 + UTF-16) を順に抽出し、全体を raw で保持する
    data = r.take(r.remaining())
    names = []
    i = 16
    while i + 9 < len(data):
        if data[i] == 1:
            n = int.from_bytes(data[i + 1:i + 9], "little")
            if 0 < n < 256 and i + 9 + 2 * n <= len(data):
                try:
                    s = data[i + 9:i + 9 + 2 * n].decode("utf-16-le")
                    if s.isprintable():
                        names.append(s)
                        i += 9 + 2 * n
                        continue
                except UnicodeDecodeError:
                    pass
        i += 1
    return {"exports": names, "raw": data}


def _query_results(r: Reader, d: "ApiDecoder") -> dict:
    # 2046: ResolveQueryData の結果 (u64 size + bytes)。タイムスタンプ/オクルージョンは u64 列
    size = r.u64()
    data = r.take(size)
    return {"size": size, "values": [int.from_bytes(data[i:i + 8], "little") for i in range(0, len(data) - 7, 8)]}


def _raw_event(r: Reader, d: "ApiDecoder") -> dict:
    return {"raw": r.take(r.remaining())}


def _resource_initial_data(r: Reader, d: "ApiDecoder") -> dict:
    # 2029: リソースのキャプチャ開始時点の内容
    #   u32 resource, u32 unk1(=1), u64 unk2(=0),
    #   u64 n, D3D12_PLACED_SUBRESOURCE_FOOTPRINT[n] (28B 各), u64 totalSize, data[totalSize]
    #   (data は GetCopyableFootprints のレイアウト。バッファは footprint が全て 0)
    out: dict[str, Any] = {"resource": r.u32(), "unk1": r.u32(), "unk2": r.u64()}
    n = r.u64()
    out["footprints"] = [d.read_type(r, "D3D12_PLACED_SUBRESOURCE_FOOTPRINT") for _ in range(n)]
    size = r.u64()
    out["size"] = size
    out["data"] = r.take(size)
    return out


def _blob_ref(r: Reader, d: "ApiDecoder") -> dict:
    # 36: 付随データ (blob) の参照: GUID kind, u64 size, u64 seq(Present の seq と一致)
    kind = r.guid()
    return {"kind": kind, "size": r.u64(), "seq": r.u64()}


def _blob_data(r: Reader, d: "ApiDecoder") -> dict:
    # 37: 付随データ本体: GUID kind, u64 size, bytes (kind=GUID_SCREENSHOT_PNG なら PNG)
    kind = r.guid()
    size = r.u64()
    return {"kind": kind, "isScreenshotPng": kind == GUID_SCREENSHOT_PNG, "size": size, "data": r.take(size)}


def _present(r: Reader, d: "ApiDecoder") -> dict:
    # 1885: Present (フレーム境界)。u32 queue, u32 backBuffer, u32 PresentFlags, u64 screenshotSeq
    return {"queue": r.u32(), "backBuffer": r.u32(), "presentFlags": r.u32(), "screenshotSeq": r.u64()}


def _prepare_for_present(r: Reader, d: "ApiDecoder") -> dict:
    # 1860: DXGI 内部コマンドリストの PrepareForPresent
    return {"commandList": r.u32()}


def _create_device(r: Reader, d: "ApiDecoder") -> dict:
    # 1566: D3D12CreateDevice + アダプタ情報 + 機能サポート (後半は未解析)
    out: dict[str, Any] = {"pAdapter": r.u32(), "MinimumFeatureLevel": hex(r.u32()), "riid": r.guid()}
    out["ppDevice"] = _opt(r, r.u32)
    if out["ppDevice"]:
        d.note_object(out["ppDevice"], interface="ID3D12Device", created_by="D3D12CreateDevice")
    n = r.u64()
    out["adapterDescription"] = r.wstr(n)
    drv = r.u64()
    out["driverVersion"] = f"{drv >> 48}.{(drv >> 32) & 0xFFFF}.{(drv >> 16) & 0xFFFF}.{drv & 0xFFFF}"
    out["adapterLuid?"] = r.u64()
    out["unk_after_luid"] = r.u32()   # 0xb000 を観測 ❓
    out["VendorId"] = hex(r.u32())
    out["DeviceId"] = hex(r.u32())
    out["SubSysId"] = hex(r.u32())
    out["Revision"] = hex(r.u32())
    out["capsRaw"] = r.take(r.remaining())   # TODO: メモリサイズ / 機能サポート構造体列
    return out


def _capture_environment(r: Reader, d: "ApiDecoder") -> dict:
    # 2042: キャプチャ環境 (CPU, OS, マシン名, プロセス名, PIX バージョン, 時刻)
    out: dict[str, Any] = {}
    out["cpu"] = _wstr(r)
    out["cpuArch"] = _wstr(r)
    out["physicalMemory"] = _opt(r, r.u64)
    out["os"] = _wstr(r)
    out["osVersionRaw"] = _opt(r, r.u64)
    out["osBuildLab"] = _wstr(r)
    out["machineName"] = _wstr(r)
    out["processName"] = _wstr(r)
    t = _opt(r, r.u64)
    out["time1"] = _filetime(t) if t else None
    out["processId"] = _opt(r, r.u32)
    out["processArch"] = _wstr(r)
    out["pixVersion"] = _wstr(r)
    t = _opt(r, r.u64)
    out["time2"] = _filetime(t) if t else None
    return out


def _descriptor_heap(r: Reader, d: "ApiDecoder") -> dict:
    # 2044: ディスクリプタヒープの生成 + キャプチャ開始時点の全スロットの内容
    out: dict[str, Any] = {"unk0": r.u32(), "device": r.u32()}
    out["desc"] = _opt(r, lambda: d.read_type(r, "D3D12_DESCRIPTOR_HEAP_DESC"))
    out["unk1"] = r.u32()
    descs = None
    if r.flag():
        n = r.u64()
        descs = [d.read_descriptor(r) for _ in range(n)]
    out["descriptors"] = descs
    out["numDescriptorEntries"] = r.u32()   # = desc.NumDescriptors (export-to-cpp で確認)
    out["riid"] = r.guid()
    out["ppHeap"] = _opt(r, r.u32)
    if out["ppHeap"]:
        d.note_object(out["ppHeap"], interface="ID3D12DescriptorHeap", created_by="PIX::DescriptorHeap",
                      desc=out["desc"])
    return out


def _u32_only(r: Reader, d: "ApiDecoder") -> dict:
    return {"value": r.u32()}


# ------------------------------------------------------------------------------------------------
# (typeId, 名前, デコーダ)
CUSTOM_EVENTS = [
    (36, "PIX::BlobReference", _blob_ref),
    (37, "PIX::BlobData", _blob_data),
    (1566, "D3D12CreateDevice", _create_device),
    (1700, "PIX::CreateResource", _create_resource),
    (1860, "PIX::PrepareForPresent", _prepare_for_present),
    (1885, "PIX::Present", _present),
    (2029, "PIX::ResourceInitialData", _resource_initial_data),
    (2042, "PIX::CaptureEnvironment", _capture_environment),
    (2043, "PIX::Unknown2043", _u32_only),
    (2044, "PIX::DescriptorHeap", _descriptor_heap),
    (1629, "ID3D12Device2::CreatePipelineState", _create_pipeline_state),
    (1767, "PIX::CreatePlacedResource", _create_placed_resource),
    (1770, "PIX::CreateReservedResource", _create_reserved_resource),
    (2030, "PIX::IndirectArguments", _indirect_arguments),
    (2037, "PIX::TileMappings", _tile_mappings),
    (2038, "PIX::ShaderTables", _shader_tables),
    (2039, "PIX::InstanceDescs", _instance_descs),
    (2041, "PIX::AccelerationStructureData", _accel_struct_data),
    (2045, "PIX::CreateStateObject", _create_state_object),
    (2046, "PIX::QueryResults", _query_results),
    (2139, "PIX::Unknown2139", _raw_event),
]


def register(d: "ApiDecoder") -> None:
    for tid, name, fn in CUSTOM_EVENTS:
        d.custom[tid] = (name, fn)
    # ExecuteCommandLists は汎用規則と異なる PIX 独自形式なので上書き
    base = d.bases.get("ID3D12CommandQueue")
    if base is not None:
        d.custom[base + 2] = ("ID3D12CommandQueue::ExecuteCommandLists", _execute_command_lists)
