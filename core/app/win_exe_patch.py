# core/app/win_exe_patch.py — Windows PE resource patching for the Flutter exe.
#
# Extracted from app.py (P3-7): rewrites the icon (RT_ICON / RT_GROUP_ICON) and
# VERSIONINFO of the renamed SumisoraOMR.exe so Task Manager and the file's
# properties show the app identity instead of "flet". Windows-only, driven by
# app.py's _setup_flet_view_name(); no GUI/Flet dependency here.

import os


def _build_versioninfo_bytes(
    major: int, minor: int, patch_: int, build: int,
    file_desc: str, product_name: str, company: str, copyright_str: str,
) -> bytes:
    """Build a binary RT_VERSION resource from scratch (no external deps)."""
    import struct as _s

    def _enc(s: str) -> bytes:
        return s.encode('utf-16-le') + b'\x00\x00'

    def _kpad(k: bytes) -> bytes:
        r = (6 + len(k)) % 4
        return b'\x00' * ((4 - r) % 4)

    def _str_entry(key: str, value: str) -> bytes:
        k, v = _enc(key), _enc(value)
        body = k + _kpad(k) + v
        n = 6 + len(body)
        tail = (4 - n % 4) % 4
        return _s.pack('<HHH', n + tail, len(v) // 2, 1) + body + b'\x00' * tail

    def _node(key: str, vbytes: bytes, wtype: int, children: bytes) -> bytes:
        k = _enc(key)
        body = k + _kpad(k) + vbytes + children
        n = 6 + len(body)
        tail = (4 - n % 4) % 4
        wval = len(vbytes) if wtype == 0 else len(vbytes) // 2
        return _s.pack('<HHH', n + tail, wval, wtype) + body + b'\x00' * tail

    ffi = _s.pack('<IIIIIIIIIIIII',
        0xFEEF04BD, 0x00010000,
        (major << 16) | minor,  (patch_ << 16) | build,
        (major << 16) | minor,  (patch_ << 16) | build,
        0x3F, 0, 0x00040004, 1, 0, 0, 0,
    )
    vs = f'{major}.{minor}.{patch_}.{build}'
    str_data = b''.join([
        _str_entry('CompanyName',      company),
        _str_entry('FileDescription',  file_desc),
        _str_entry('FileVersion',      vs),
        _str_entry('InternalName',     file_desc),
        _str_entry('LegalCopyright',   copyright_str),
        _str_entry('OriginalFilename', file_desc + '.exe'),
        _str_entry('ProductName',      product_name),
        _str_entry('ProductVersion',   vs),
    ])
    str_fi = _node('StringFileInfo', b'', 1, _node('040904b0', b'', 1, str_data))
    import struct as _s2
    var_fi = _node('VarFileInfo', b'', 1,
                   _node('Translation', _s2.pack('<HH', 0x0409, 0x04B0), 0, b''))
    root_k   = _enc('VS_VERSION_INFO')
    root_pad = _kpad(root_k)
    body     = root_k + root_pad + ffi + str_fi + var_fi
    n        = 6 + len(body)
    tail     = (4 - n % 4) % 4
    return _s.pack('<HHH', n + tail, len(ffi), 0) + body + b'\x00' * tail


def patch_exe_resources(
    exe_path: str, ico_path: str,
    file_desc: str, product_name: str, company: str, copyright_str: str,
    major: int, minor: int, patch_: int, build: int,
) -> bool:
    """Replace icon (RT_ICON/RT_GROUP_ICON) and VERSIONINFO in a PE exe."""
    import struct as _s
    import ctypes as _ct
    from ctypes import wintypes as _wt

    if not os.path.isfile(ico_path):
        return False

    with open(ico_path, 'rb') as _f:
        ico = _f.read()
    _, _, cnt = _s.unpack_from('<HHH', ico, 0)
    icons = []
    for _i in range(cnt):
        w, h, cc, _, pl, bpp, sz, off = _s.unpack_from('<BBBBHHII', ico, 6 + _i * 16)
        icons.append((w or 256, h or 256, cc, pl, bpp, ico[off:off + sz]))

    grp = _s.pack('<HHH', 0, 1, cnt)
    for _i, (w, h, cc, pl, bpp, data) in enumerate(icons):
        grp += _s.pack('<BBBBHHiH',
                       0 if w == 256 else w, 0 if h == 256 else h,
                       cc, 0, pl, bpp, len(data), _i + 1)

    ver = _build_versioninfo_bytes(major, minor, patch_, build,
                                    file_desc, product_name, company, copyright_str)

    k32 = _ct.windll.kernel32
    k32.BeginUpdateResourceW.restype  = _wt.HANDLE
    k32.BeginUpdateResourceW.argtypes = [_wt.LPCWSTR, _wt.BOOL]
    # lpType / lpName accept either a string pointer or MAKEINTRESOURCE
    # (an integer ID stuffed into the low word of a "fake" pointer).
    # Declare them as c_void_p so we can pass either; declaring LPCWSTR makes
    # Python 3.14 ctypes reject the integer-pointer cast with a TypeError.
    k32.UpdateResourceW.restype       = _wt.BOOL
    k32.UpdateResourceW.argtypes      = [_wt.HANDLE, _ct.c_void_p, _ct.c_void_p,
                                          _wt.WORD, _ct.c_void_p, _wt.DWORD]
    k32.EndUpdateResourceW.restype    = _wt.BOOL
    k32.EndUpdateResourceW.argtypes   = [_wt.HANDLE, _wt.BOOL]

    def _mir(n: int) -> _ct.c_void_p:
        # MAKEINTRESOURCE(n): integer ID as a pointer-shaped value.
        return _ct.c_void_p(n)

    h = k32.BeginUpdateResourceW(exe_path, False)
    if not h:
        return False
    try:
        for _i, (_, _, _, _, _, data) in enumerate(icons):
            _buf = (_ct.c_char * len(data)).from_buffer_copy(data)
            k32.UpdateResourceW(h, _mir(3), _mir(_i + 1), 0x0409, _buf, len(data))
        _gb = (_ct.c_char * len(grp)).from_buffer_copy(grp)
        k32.UpdateResourceW(h, _mir(14), _mir(1), 0x0409, _gb, len(grp))
        _vb = (_ct.c_char * len(ver)).from_buffer_copy(ver)
        k32.UpdateResourceW(h, _mir(16), _mir(1), 0x0409, _vb, len(ver))
        return bool(k32.EndUpdateResourceW(h, False))
    except Exception:
        k32.EndUpdateResourceW(h, True)
        return False
