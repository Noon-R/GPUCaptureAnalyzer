"""Windows Compression API (cabinet.dll) / RtlDecompressBufferEx の ctypes ラッパ。

PIX の WinPixGpuCapturer.dll は cabinet.dll を ordinal import している (CreateDecompressor 等)。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

COMPRESS_ALGORITHM_MSZIP = 2
COMPRESS_ALGORITHM_XPRESS = 3
COMPRESS_ALGORITHM_XPRESS_HUFF = 4
COMPRESS_ALGORITHM_LZMS = 5
COMPRESS_RAW = 1 << 29

ALGORITHMS = {
    "mszip": COMPRESS_ALGORITHM_MSZIP,
    "xpress": COMPRESS_ALGORITHM_XPRESS,
    "xpress_huff": COMPRESS_ALGORITHM_XPRESS_HUFF,
    "lzms": COMPRESS_ALGORITHM_LZMS,
}

_cab = ctypes.WinDLL("cabinet.dll")
_cab.CreateDecompressor.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_cab.CreateDecompressor.restype = wintypes.BOOL
_cab.Decompress.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_cab.Decompress.restype = wintypes.BOOL
_cab.CloseDecompressor.argtypes = [ctypes.c_void_p]
_cab.CloseDecompressor.restype = wintypes.BOOL

_handles: dict[int, ctypes.c_void_p] = {}


def _handle(alg: int) -> ctypes.c_void_p:
    h = _handles.get(alg)
    if h is None:
        h = ctypes.c_void_p()
        if not _cab.CreateDecompressor(alg, None, ctypes.byref(h)):
            raise OSError(ctypes.get_last_error(), f"CreateDecompressor({alg:#x}) failed")
        _handles[alg] = h
    return h


def decompress(data: bytes, raw_size: int, algorithm: int = COMPRESS_ALGORITHM_XPRESS | COMPRESS_RAW) -> bytes:
    """data を展開して raw_size バイトを返す。失敗時は OSError。"""
    h = _handle(algorithm)
    out = ctypes.create_string_buffer(max(raw_size, 1))
    src = ctypes.create_string_buffer(bytes(data), len(data))
    got = ctypes.c_size_t(0)
    ok = _cab.Decompress(h, src, len(data), out, raw_size, ctypes.byref(got))
    if not ok:
        raise OSError(ctypes.GetLastError(), f"Decompress(alg={algorithm:#x}) failed")
    return out.raw[: got.value]
