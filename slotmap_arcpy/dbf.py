"""Minimal dBASE III reader (the .dbf of a shapefile) - enough to read Natural Earth attribute tables."""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence


def read_dbf(path: Path, fields: Optional[Sequence[str]] = None, encoding: str = "utf-8") -> Iterator[Dict[str, object]]:
    with open(str(path), "rb") as fh:
        head = fh.read(32)
        n_rec, hdr_len, rec_len = struct.unpack("<IHH", head[4:12])
        specs = []  # type: List[tuple]
        while True:
            d = fh.read(32)
            if not d or d[0:1] == b"\r":
                break
            name = d[:11].split(b"\x00")[0].decode("ascii", "ignore")
            specs.append((name, chr(d[11]), d[16], d[17]))
        fh.seek(hdr_len)
        want = None if fields is None else {f.lower() for f in fields}
        for _ in range(n_rec):
            rec = fh.read(rec_len)
            if not rec or rec[0:1] == b"*":
                continue
            pos, out = 1, {}
            for name, typ, ln, dec in specs:
                raw = rec[pos:pos + ln]
                pos += ln
                if want is not None and name.lower() not in want:
                    continue
                s = raw.decode(encoding, "replace").strip()
                if typ in ("N", "F"):
                    try:
                        out[name] = float(s) if s else None
                    except ValueError:
                        out[name] = None
                else:
                    out[name] = s
            yield out
