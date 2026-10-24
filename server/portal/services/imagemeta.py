"""Fjern metadata (EXIF med GPS-position, kameraserienummer, XMP, kommentarer) fra uploadede billeder.

Billedet genkodes ikke: kun metadata-blokkene skæres fra. For JPEG bevares EXIF-orienteringen i en minimal EXIF-blok,
så telefonbilleder stadig vender rigtigt. Filer der ikke kan tolkes, afvises (ValueError) i stedet for at blive gemt
med metadata.
"""

from __future__ import annotations

import struct
from pathlib import Path

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


def strip(data: bytes) -> bytes:
    if data[:3] == b"\xff\xd8\xff":
        return _jpeg(data)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp(data)
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return data  # GIF har ingen EXIF/GPS
    raise ValueError("Ukendt billedformat")


# ---------------------------------------------------------------- JPEG

def _orientation(exif: bytes) -> int | None:
    """Orientering (tag 0x0112) fra IFD0 i en EXIF-blok ('Exif\\0\\0' + TIFF)."""
    tiff = exif[6:]
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    e = "<" if tiff[:2] == b"II" else ">"
    try:
        (ifd,) = struct.unpack_from(e + "I", tiff, 4)
        (n,) = struct.unpack_from(e + "H", tiff, ifd)
        for i in range(n):
            tag, typ, _, value = struct.unpack_from(e + "HHI4s", tiff, ifd + 2 + i * 12)
            if tag == 0x0112 and typ == 3:
                (o,) = struct.unpack_from(e + "H", value)
                return o if 1 <= o <= 8 else None
    except struct.error:
        return None
    return None


def _exif_orientation_segment(o: int) -> bytes:
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 1) + struct.pack("<HHIHH", 0x0112, 3, 1, o, 0) + struct.pack("<I", 0)
    body = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _jpeg(data: bytes) -> bytes:
    out = [b"\xff\xd8"]
    i, orientation = 2, None
    while i < len(data):
        if data[i] != 0xFF:
            raise ValueError("Ødelagt JPEG")
        marker = data[i + 1]
        if marker == 0xFF:  # fyldbyte
            i += 1
            continue
        if marker == 0xDA or marker == 0xD9:  # start på billeddata / slut: resten kopieres uændret
            if orientation and orientation != 1:
                at = 2 if len(out) > 1 and out[1][:2] == b"\xff\xe0" else 1  # efter JFIF-hovedet
                out.insert(at, _exif_orientation_segment(orientation))
            out.append(data[i:])
            return b"".join(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out.append(data[i:i + 2])
            i += 2
            continue
        (length,) = struct.unpack_from(">H", data, i + 2)
        seg = data[i:i + 2 + length]
        payload = seg[4:]
        # APP1 (EXIF/XMP), APP2-APP15 (bortset fra ICC-farveprofil i APP2) og kommentarer (COM) fjernes
        if marker == 0xE1:
            if payload.startswith(b"Exif\x00\x00"):
                orientation = _orientation(payload)
        elif marker == 0xE2 and payload.startswith(b"ICC_PROFILE\x00"):
            out.append(seg)
        elif 0xE2 <= marker <= 0xEF or marker == 0xFE:
            pass
        else:
            out.append(seg)  # APP0 (JFIF), kvantiserings-/Huffman-tabeller, rammehoved osv.
        i += 2 + length
    raise ValueError("Ødelagt JPEG")


# ---------------------------------------------------------------- PNG

PNG_DROP = {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"}


def _png(data: bytes) -> bytes:
    out, i = [data[:8]], 8
    while i < len(data):
        (length,) = struct.unpack_from(">I", data, i)
        kind = data[i + 4:i + 8]
        if kind not in PNG_DROP:
            out.append(data[i:i + 12 + length])
        i += 12 + length
        if kind == b"IEND":
            return b"".join(out)
    raise ValueError("Ødelagt PNG")


# ---------------------------------------------------------------- WebP

def _webp(data: bytes) -> bytes:
    chunks, i = [], 12
    while i + 8 <= len(data):
        kind = data[i:i + 4]
        (size,) = struct.unpack_from("<I", data, i + 4)
        chunk = data[i:i + 8 + size + (size & 1)]
        if kind == b"VP8X":
            flags = chunk[8] & ~0x0C  # bit for EXIF (0x08) og XMP (0x04) slås fra
            chunk = chunk[:8] + bytes([flags]) + chunk[9:]
        if kind not in (b"EXIF", b"XMP "):
            chunks.append(chunk)
        i += 8 + size + (size & 1)
    body = b"WEBP" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def strip_file(path: Path) -> bool:
    """Fjern metadata fra en fil på disken. True hvis filen blev ændret."""
    data = path.read_bytes()
    try:
        clean = strip(data)
    except (ValueError, struct.error, IndexError):
        return False
    if clean == data:
        return False
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(clean)
    tmp.replace(path)
    return True


def strip_tree(root: Path) -> int:
    """Rens alle billeder under root (engangskørsel for filer uploadet før rensningen fandtes)."""
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXT:
            n += strip_file(p)
    return n

