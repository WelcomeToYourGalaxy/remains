#!/usr/bin/env python3
"""Build one small world-view file per tiled facility layer.

WHY THIS EXISTS
A tiled layer (cemeteries: 166 tiles of 10 degrees) had to fetch every tile to
draw the world view. Four at a time, that filled the map in square patches over
a minute or more, and any slow tile left a square hole.

At world and continent zoom the map draws ONE mark per ~3 screen pixels anyway
(see MarkLayer.redraw). At zoom 5 that bin is 0.13 degrees wide. So a file that
keeps one real point per 0.1-degree bin draws exactly the same picture as the
full data at every zoom below 6 -- in one request instead of 166.

Nothing is dropped from the published data: the tiles are untouched, and from
zoom 6 up the map loads them and draws every row.

Each kept point is a real facility location (the first one in its bin), not a
bin centre, so nothing is placed where no facility is.

Runs after the merge (called from prune_tiles.py) and once from apply_update.py,
so it works on tiles already in the repository without waiting for a harvest.
"""
import gzip
import json
import os

MANIFEST = "remains_local_manifest.json"
BIN_DEG = 0.1


def _read(fn):
    with open(fn, "rb") as fh:
        raw = fh.read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    return json.loads(raw.decode("utf-8"))


def main():
    try:
        man = _read(MANIFEST)
    except Exception as e:
        print("  overview: no readable manifest (%s) -- skipped" % e)
        return
    changed = False
    for ftype, spec in man.get("types", {}).items():
        if spec.get("mode") != "tiled":
            spec.pop("overview", None)
            continue
        seen = set()
        pts = []
        missing = 0
        for key in sorted(spec.get("tiles", {})):
            fn = "remains_local_%s_%s.json.gz" % (ftype, key)
            if not os.path.exists(fn):
                missing += 1
                continue
            try:
                rows = _read(fn)
            except Exception:
                missing += 1
                continue
            for r in rows:
                try:
                    la, lo = float(r[0]), float(r[1])
                except Exception:
                    continue
                b = (int(la // BIN_DEG), int(lo // BIN_DEG))
                if b in seen:
                    continue
                seen.add(b)
                pts.append([round(la, 4), round(lo, 4)])
        if not pts:
            print("  overview: %s has no readable tiles -- skipped" % ftype)
            continue
        out = "remains_local_%s_overview.json.gz" % ftype
        with gzip.open(out, "wb", compresslevel=9) as fh:
            fh.write(json.dumps(pts, separators=(",", ":")).encode("utf-8"))
        spec["overview"] = {"file": out, "count": len(pts), "bin_deg": BIN_DEG,
                            "max_zoom": 5}
        changed = True
        print("  overview: %s -> %d points from %d rows (%.2f MB gz)%s"
              % (out, len(pts), spec.get("count", 0),
                 os.path.getsize(out) / 1e6,
                 (", %d tile(s) unreadable" % missing) if missing else ""))
    if changed:
        with open(MANIFEST, "w", encoding="utf-8") as fh:
            json.dump(man, fh, sort_keys=True)


if __name__ == "__main__":
    main()
