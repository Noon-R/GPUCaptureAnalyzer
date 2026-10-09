"""PIX 独自イベント (D3D12 API に直接対応しないもの) のデコーダ。

ID と意味は解析で判明したもの (PIX 2603.25)。確度が低いフィールドは名前に '?' / 'unk' を付ける。
詳細と根拠は docs/formats/wpix.md 参照。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from .serializer import Reader

if TYPE_CHECKING:
    from .decoder import ApiDecoder

GUID_SCREENSHOT_PNG = "7c29896a-6790-4b40-ab07-f9876ee0e614"


def _filetime(v: int) -> str:
    try:
        return (datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=v / 10)).isoformat()
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


def _create_resource(r: Reader, d: "ApiDecoder") -> dict:
    # 1700: キャプチャ開始時点で存在したリソースの再生成情報
    out: dict[str, Any] = {"device": r.u32(), "resource": r.u32()}
    out["heapProperties"] = d.read_type(r, "D3D12_HEAP_PROPERTIES")
    out["heapFlags"] = r.u32()
    out["desc"] = d.read_type(r, "D3D12_RESOURCE_DESC1")   # SamplerFeedbackMipRegion 付き (60B)
    out["stateAtCaptureStart"] = r.u32()   # 作成時ではなくキャプチャ開始時のステート (= subresourceStatesAtCapture[0])
    # D3D12_CLEAR_VALUE は union の両メンバ (Color[4] と DepthStencil) が書かれる
    out["optimizedClearValue"] = _opt(r, lambda: d.read_type(r, "D3D12_CLEAR_VALUE"))
    out["riid"] = r.guid()
    out["initialDataSeq"] = r.u64()      # 対応する 2029 (初期内容) レコードの seq
    n = r.u64()
    out["subresourceStatesAtCapture"] = [r.u32() for _ in range(n)]
    d.note_object(out["resource"], interface="ID3D12Resource", created_by="PIX::CreateResource",
                  desc=out["desc"], heapType=out["heapProperties"].get("Type"))
    return out


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
]


def register(d: "ApiDecoder") -> None:
    for tid, name, fn in CUSTOM_EVENTS:
        d.custom[tid] = (name, fn)
    # ExecuteCommandLists は汎用規則と異なる PIX 独自形式なので上書き
    base = d.bases.get("ID3D12CommandQueue")
    if base is not None:
        d.custom[base + 2] = ("ID3D12CommandQueue::ExecuteCommandLists", _execute_command_lists)
