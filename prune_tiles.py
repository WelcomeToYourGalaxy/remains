#!/usr/bin/env python3
"""Drop published facility tiles that the current manifest no longer lists.

Why this exists: the map only ever requests what `remains_local_manifest.json`
advertises, so an orphaned tile is invisible to readers but immortal in git. If a
10-degree cell empties out between sweeps — or a type switches from tiled back to
a single file — the old files would sit in the repository forever, growing the
clone for nobody.

Runs after the merge, before the commit. Deletes only files that match the
published naming scheme AND are absent from the manifest; anything it cannot
account for is left alone, because deleting an unrecognised file is worse than
keeping one.
"""
import glob
import json
import os
import re

MANIFEST = "remains_local_manifest.json"
# remains_local_<type>.json.gz  or  remains_local_<type>_<lat>_<lng>.json.gz
PATTERN = re.compile(r"^remains_local_([a-z]+)(?:_(-?\d+_-?\d+))?\.json\.gz$")


def main():
    try:
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            man = json.load(fh)
    except Exception as e:
        print("  prune: no readable manifest (%s) -- keeping everything" % e)
        return

    types = man.get("types", {})
    keep = set()
    tiled = set()
    for t, spec in types.items():
        if spec.get("mode") == "tiled":
            tiled.add(t)
            for k in spec.get("tiles", {}):
                keep.add("remains_local_%s_%s.json.gz" % (t, k))
        else:
            keep.add(spec.get("file") or "remains_local_%s.json.gz" % t)

    dropped = kept_unknown = 0
    for f in sorted(glob.glob("remains_local_*.json.gz")):
        if f in keep:
            continue
        m = PATTERN.match(f)
        if not m:
            kept_unknown += 1
            continue
        ftype, cell = m.group(1), m.group(2)
        # A cell file, or the stale single file of a type that is now tiled.
        if cell or ftype in tiled:
            os.remove(f)
            dropped += 1
        else:
            kept_unknown += 1

    print("  prune: removed %d stale file(s); %d left alone as unrecognised"
          % (dropped, kept_unknown))


if __name__ == "__main__":
    main()
