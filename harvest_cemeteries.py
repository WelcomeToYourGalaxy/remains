#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_cemeteries.py
=====================
Bulk-extract the world's cemeteries and burial grounds from OpenStreetMap and
write them in the SAME flat JSON shape the projects map's facility layer uses
(execmap_local_*.json):

    [lat, lng, name, website, "", address, "", phone]

Only lat/lng/name are required; the rest are filled when OSM has them.

Output (gzipped -- see WHY GZIP below):
    cemetery  -> remains_local_cemetery.json.gz   (landuse=cemetery, amenity=grave_yard)
    crematory -> remains_local_crematory.json.gz  (amenity=crematorium)
    mortuary  -> remains_local_mortuary.json.gz   (amenity=mortuary, shop=funeral_directors)

-----------------------------------------------------------------------------
WHY GZIP
-----------------------------------------------------------------------------
A worldwide cemetery sweep is on the order of half a million to a million features.
At roughly 76 bytes per row that is 45-75 MB of raw JSON: under GitHub's 100 MB
hard push limit, but over its 50 MB warning, and a punishing fetch for a browser
that cannot draw a single dot until the whole file has arrived. Gzip takes it to
roughly 11-19 MB.

The map inflates it client-side with the same dual-path loader it uses for
remains.json.gz, which handles BOTH hosting behaviours: GitHub Pages serves a .gz
file with Content-Encoding: gzip so fetch() has already inflated the body, while a
plain static host sends the raw bytes. It tries JSON first, then gunzips.

Shard artifacts stay uncompressed -- they are transient and never pushed.

-----------------------------------------------------------------------------
LEGAL BASIS  --  why this layer is fine to publish, and where it stops
-----------------------------------------------------------------------------
OpenStreetMap is licensed ODbL 1.0. Extracting and republishing it is expressly
permitted with attribution, which the map carries. Nothing here is scraped from a
gated source.

More importantly, this layer is NOT subject to the unearthings map's blurring
rule, and the distinction is deliberate:

  * A working cemetery is a signposted public place. Its gate has an address, its
    location is on every street map, and people need to find it. Blurring it would
    serve nobody and would break the layer's only purpose -- letting someone find
    the burial grounds near a place.
  * An archaeological burial site, an unmarked grave, or a mass grave is the exact
    opposite: unadvertised, and endangered by publication.

So this harvester takes ONLY tags that denote an established, publicly known
burial facility, and EXPLICITLY REFUSES the archaeological tags:

    included:  landuse=cemetery, amenity=grave_yard, amenity=crematorium,
               amenity=mortuary, shop=funeral_directors
    refused:   historic=archaeological_site, historic=tomb, historic=tumulus,
               historic=memorial, any *:landuse=cemetery lifecycle prefix

The lifecycle-prefixed tags (was:, demolished:, removed:, abandoned:) are refused
here on purpose -- those are unearthing EVENTS and belong to harvest_remains.py,
where the blurring gate applies to them. Keeping the two harvesters apart is what
keeps the policy coherent: this file is a directory, that file is a record of
disturbance.

_reject_archaeological() enforces the refusal per element, so a mis-tagged feature
that carries both a cemetery tag and an archaeological tag is dropped rather than
published.

-----------------------------------------------------------------------------
Why this looks like harvest_local_facilities.py
-----------------------------------------------------------------------------
It reuses the SAME hardening that fixed the box-shaped gaps on the projects map:
it detects Overpass's silent server-side timeout (HTTP 200 plus a "remark" field)
and RECURSIVELY quarter-splits any tile that times out, down to a floor, so dense
regions never come back empty. Cemeteries are far denser than courthouses, so the
default floor is tighter and the default tile smaller.

Run:
    python harvest_cemeteries.py                       # all sets, whole world
    FAC_TYPE=cemetery python harvest_cemeteries.py     # one set
    FAC_TYPE=cemetery FAC_SHARD=3 FAC_SHARDS=24 python harvest_cemeteries.py
    FAC_MERGE=1 FAC_TYPE=cemetery python harvest_cemeteries.py   # fold the parts

Tunables (env):
    FAC_TYPE       cemetery | crematory | mortuary | all   (default all)
    FAC_SHARD      shard index k (0..FAC_SHARDS-1)         (default: unsharded)
    FAC_SHARDS     number of shards                        (default 24)
    FAC_MERGE      1 to merge *_part*.json into one file
    OSM_BUDGET_MIN wall-clock minutes before stopping early (default 150)
    OSM_MIN_DEG    recursion floor in degrees (~0.3125 = 35km) (default 0.3125)
    OSM_TILE_DEG   starting tile size in degrees            (default 2.5)
    CONTACT        contact string for the User-Agent
"""

import os, sys, time, json, glob, gzip, math, urllib.request, urllib.parse

CONTACT     = os.environ.get("CONTACT", "wheelock.chris@gmail.com")
UA          = "remains-map-cemeteries/1.0 (+%s)" % CONTACT
OSM_MIN_DEG = float(os.environ.get("OSM_MIN_DEG", "0.3125"))
TILE_DEG    = float(os.environ.get("OSM_TILE_DEG", "2.5"))
BUDGET_MIN  = int(os.environ.get("OSM_BUDGET_MIN", "150"))

_LAT_MIN, _LAT_MAX = -60.0, 84.0
_LNG_MIN, _LNG_MAX = -180.0, 180.0

_EPS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# facility type -> Overpass selector (node + way + relation).
# NOTE the absence of any historic=* selector. That is the policy, not an omission.
SETS = {
    "cemetery":  ('node["landuse"="cemetery"](%s);'
                  'way["landuse"="cemetery"](%s);'
                  'relation["landuse"="cemetery"](%s);'
                  'node["amenity"="grave_yard"](%s);'
                  'way["amenity"="grave_yard"](%s);'
                  'relation["amenity"="grave_yard"](%s);'),
    "crematory": ('node["amenity"="crematorium"](%s);'
                  'way["amenity"="crematorium"](%s);'
                  'relation["amenity"="crematorium"](%s);'),
    "mortuary":  ('node["amenity"="mortuary"](%s);'
                  'way["amenity"="mortuary"](%s);'
                  'relation["amenity"="mortuary"](%s);'
                  'node["shop"="funeral_directors"](%s);'
                  'way["shop"="funeral_directors"](%s);'
                  'relation["shop"="funeral_directors"](%s);'),
    # Museums are a DIFFERENT KIND OF LAYER and it matters that the map says so.
    # The other three are places that receive the dead by arrangement. A museum is
    # a place that may be holding ancestors it was never given permission to hold.
    # It is the potential-holder layer -- the physical counterpart to the `holding`
    # records and to the NAGPRA inventories.
    #
    # An OSM museum tag says nothing about whether that museum holds human remains,
    # and most do not. So this layer is explicitly NOT a claim: it is the set of
    # institutions a researcher would have to ask. The map labels it that way.
    "museum":    ('node["tourism"="museum"](%s);'
                  'way["tourism"="museum"](%s);'
                  'relation["tourism"="museum"](%s);'),
}
# Types that are burial grounds, and so subject to the archaeological refusal.
# Museums are exempt: a museum in a listed building carries historic=* and is still
# a museum, not a grave. Applying the burial-site refusal to it would delete the
# holder layer almost entirely.
_BURIAL_TYPES = ("cemetery", "crematory", "mortuary")

# Tags that mean "this is an archaeological or unadvertised burial site".
# Any element carrying one of these is dropped, even if it also carries a
# cemetery tag -- publishing it is what this project refuses to do.
_ARCH_KEYS = ("historic", "archaeological_site", "site_type", "megalith_type",
              "tomb", "ruins")
_ARCH_VALUES = ("archaeological_site", "tomb", "tumulus", "burial_mound", "barrow",
                "necropolis", "megalith", "dolmen", "cairn", "kurgan", "grave",
                "crypt", "catacomb", "ossuary")
_LIFECYCLE = ("was:", "demolished:", "removed:", "abandoned:", "disused:",
              "razed:", "destroyed:", "former:", "proposed:", "construction:")


def classify(tags):
    """Which facility type is this element? None if it is none of them.

    This is what makes the combined query possible: ask Overpass for all four
    selectors at once, then sort the answers here instead of asking four times.
    Order matters -- a museum inside a cemetery is a museum, and funeral_directors
    is checked before the generic amenity fallbacks."""
    t = {k.lower(): str(v).lower() for k, v in tags.items()}
    if t.get("tourism") == "museum":
        return "museum"
    if t.get("amenity") == "crematorium":
        return "crematory"
    if t.get("amenity") == "mortuary" or t.get("shop") == "funeral_directors":
        return "mortuary"
    if t.get("landuse") == "cemetery" or t.get("amenity") == "grave_yard":
        return "cemetery"
    return None


def _reject_archaeological(tags, ftype=None):
    """True if this element must not be published in the facility layer.

    `ftype` scopes the rule. For burial types the refusal is broad: any historic=*
    at all is enough, because the cost of publishing one unadvertised grave is worse
    than the cost of dropping a handful of legitimate old cemeteries.

    For MUSEUMS that same breadth would be wrong -- a museum in a listed building
    carries historic=* and is still a museum. So museums are refused only on the
    burial-site VALUES and on lifecycle prefixes, never on the mere presence of
    historic=*. A demolished museum is still not a facility."""
    burial = (ftype is None) or (ftype in _BURIAL_TYPES)
    for k, v in tags.items():
        kl, vl = k.lower(), str(v).lower()
        if any(kl.startswith(p) for p in _LIFECYCLE):
            return True                      # an unearthing event, not a facility
        if kl in _ARCH_KEYS and vl in _ARCH_VALUES:
            return True
        if burial and kl == "historic":
            return True                      # any historic=* burial feature
        if kl == "archaeological_site":
            return True
    return False


def _tiles(step=None):
    step = step or TILE_DEG
    out = []
    la = _LAT_MIN
    while la < _LAT_MAX:
        lo = _LNG_MIN
        while lo < _LNG_MAX:
            out.append((round(la, 3), round(lo, 3),
                        round(min(la + step, _LAT_MAX), 3),
                        round(min(lo + step, _LNG_MAX), 3)))
            lo += step
        la += step
    return out


def _quarters(s, w, n, e):
    ms, mw = (s + n) / 2.0, (w + e) / 2.0
    return [(s, w, ms, mw), (s, mw, ms, e), (ms, w, n, mw), (ms, mw, n, e)]


ALL_SEL = "".join(SETS[k] for k in ("cemetery", "crematory", "mortuary", "museum"))


def _query(sel, s, w, n, e):
    bb = "%s,%s,%s,%s" % (s, w, n, e)
    body = sel % tuple([bb] * sel.count("%s"))
    return "[out:json][timeout:70];(" + body + ");out center tags;"


def _overpass(q, label=""):
    """POST to each mirror; return parsed JSON, or None on a real/silent timeout."""
    for i, ep in enumerate(_EPS):
        try:
            req = urllib.request.Request(
                ep, data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=75) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            # HTTP 200 + a "remark" == server-side timeout. Treat as failure so the
            # caller splits, instead of recording an empty tile (the bug that left
            # box-shaped holes on the projects layer).
            rm = str((data or {}).get("remark") or "")
            if rm and ("timed out" in rm.lower() or "error" in rm.lower()
                       or "out of memory" in rm.lower()):
                if i == len(_EPS) - 1:
                    print("  %s server-side timeout -> will split: %s"
                          % (label, rm[:60]))
                time.sleep(1.0)
                continue
            return data
        except Exception as ex:
            if i == len(_EPS) - 1:
                print("  %s failed on all mirrors: %s" % (label, str(ex)[:60]))
            time.sleep(1.0)
    return None


def _collect(data, out, stats, ftype=None):
    """out is a dict of type -> rows when ftype is None (combined mode),
    or a plain list when harvesting a single type."""
    for el in (data.get("elements") or []):
        try:
            tg = el.get("tags") or {}
            kind = ftype or classify(tg)
            if kind is None:
                continue
            if _reject_archaeological(tg, kind):
                stats["refused"] += 1
                continue
            bucket = out if isinstance(out, list) else out.setdefault(kind, [])
            c = el.get("center") or {}
            lat = c.get("lat", el.get("lat"))
            lng = c.get("lon", el.get("lon"))
            if lat is None or lng is None:
                continue
            name = (tg.get("name") or tg.get("official_name")
                    or tg.get("operator") or "")
            website = tg.get("website") or tg.get("contact:website") or ""
            phone = tg.get("phone") or tg.get("contact:phone") or ""
            hn = tg.get("addr:housenumber", ""); st = tg.get("addr:street", "")
            city = tg.get("addr:city", "")
            address = " ".join(x for x in [(hn + " " + st).strip(), city] if x).strip()
            bucket.append([round(float(lat), 5), round(float(lng), 5),
                           name[:140], website, "", address, "", phone])
        except Exception:
            continue


def _fetch_recursive(sel, s, w, n, e, deadline, out, label, stats, ftype=None):
    """Query a box; on timeout, quarter-split recursively to OSM_MIN_DEG."""
    if deadline and time.time() > deadline:
        return (0, 1, 0)
    data = _overpass(_query(sel, s, w, n, e), label)
    if data is not None:
        _collect(data, out, stats, ftype)
        return (1, 0, 0)
    if (n - s) <= OSM_MIN_DEG * 1.5:
        return (0, 1, 0)                   # smallest box, still failing -> give up
    ok = gu = 0; sp = 1
    for (qs, qw, qn, qe) in _quarters(s, w, n, e):
        a, g, s2 = _fetch_recursive(sel, qs, qw, qn, qe, deadline, out,
                                    label + "/q", stats, ftype)
        ok += a; gu += g; sp += s2
        time.sleep(0.5)
    return (ok, gu, sp)


def _outfile(ftype, tag=""):
    """Shard parts stay plain JSON (transient artifacts); the merged file is gzipped
    (it is what gets committed and what the browser fetches)."""
    if tag:
        return "remains_local_%s%s.json" % (ftype, tag)
    return "remains_local_%s.json.gz" % ftype


def _read_rows(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _write_rows(path, rows):
    if path.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        stale = path[:-3]
        if os.path.exists(stale):
            try:
                os.remove(stale)      # never leave an uncompressed twin to be pushed
            except OSError:
                pass
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))


EMPTIES_FILE = "osm_empty_tiles.json"


def _load_empties():
    """Tiles that yielded nothing last sweep. Most of the grid is ocean."""
    try:
        with open(EMPTIES_FILE, "r", encoding="utf-8") as fh:
            return set(json.load(fh).get("empty", []))
    except Exception:
        return set()


def _save_empties(empties):
    try:
        with open(EMPTIES_FILE, "w", encoding="utf-8") as fh:
            json.dump({"note": "Tiles that returned no facilities. Skipped on the "
                               "next sweep except for a rotating eighth, so every "
                               "tile is re-checked within eight runs.",
                       "count": len(empties),
                       "empty": sorted(empties)}, fh)
    except Exception as e:
        print("  could not save %s: %s" % (EMPTIES_FILE, e))


def harvest(ftype):
    """ftype='all' runs ONE query per tile covering every facility type.

    This is the speed fix. The old shape asked Overpass for each type separately:
    8,352 tiles x 4 types = 33,408 requests per sweep, spread over 32 jobs that a
    12-wide runner limit had to process in three waves. Roughly five to six hours.

    Overpass unions are free -- asking for cemeteries, crematoria, mortuaries and
    museums in one statement costs about the same as asking for cemeteries alone,
    because the expensive part is the bbox scan, not the number of selectors. So
    one query per tile does the work of four: 8,352 requests, in 8 jobs that fit a
    single wave. Same coverage, same data, a quarter of the traffic."""
    combined = (ftype == "all")
    sel = ALL_SEL if combined else SETS[ftype]
    grid = _tiles()
    shard = os.environ.get("FAC_SHARD")
    if shard is not None and shard != "":
        k = int(shard); nsh = int(os.environ.get("FAC_SHARDS", "24"))
        grid = [grid[i] for i in range(len(grid)) if i % nsh == k]
        tag = "_part%d" % k
        print("== %s: shard %d/%d -> %d tiles ==" % (ftype, k, nsh, len(grid)))
    else:
        tag = ""
        print("== %s: whole world -> %d tiles ==" % (ftype, len(grid)))

    # Tiles that returned nothing last sweep are skipped, except for a rotating
    # eighth that is always re-checked -- so every tile is revisited within eight
    # runs and newly mapped features are still found. Most of the grid is ocean.
    empties = _load_empties()
    rot = int(os.environ.get("OSM_ROTATION", str(int(time.time() // 604800) % 8)))
    recheck_all = os.environ.get("OSM_RECHECK_ALL") == "1"

    t_end = time.time() + BUDGET_MIN * 60
    out = {} if combined else []
    stats = {"refused": 0}
    ok = split = gaveup = skipped = preskipped = 0
    for (s_, w, n, e) in grid:
        key = "%.3f,%.3f" % (s_, w)
        if (not recheck_all) and key in empties and (hash(key) % 8) != rot:
            preskipped += 1
            continue
        if time.time() > t_end:
            skipped += 1; continue
        label = "%s %.1f,%.1f" % (ftype, s_, w)
        before = sum(len(v) for v in out.values()) if combined else len(out)
        a, g, sp = _fetch_recursive(sel, s_, w, n, e, t_end, out, label, stats,
                                    None if combined else ftype)
        after = sum(len(v) for v in out.values()) if combined else len(out)
        if a > 0:
            ok += 1
            if after == before:
                empties.add(key)
            else:
                empties.discard(key)
        if sp > 0: split += 1
        gaveup += g
        time.sleep(0.4)

    _save_empties(empties)
    types = sorted(out.keys()) if combined else [ftype]
    for t in types:
        rows = _dedup(out[t] if combined else out)
        fn = _outfile(t, tag)
        _write_rows(fn, rows)
        print("  %s: %d facilities -> %s" % (t, len(rows), fn))
    print("  swept %d tiles (%d ok, %d split, %d gave up, %d over budget, "
          "%d pre-skipped as known-empty), %d refused as archaeological"
          % (len(grid), ok, split, gaveup, skipped, preskipped, stats["refused"]))
    if gaveup:
        print("  NOTE: %d leaves gave up at the %.4f-degree floor -- lower "
              "OSM_MIN_DEG if a gap remains." % (gaveup, OSM_MIN_DEG))


def _dedup(rows):
    seen, merged = set(), []
    for row in rows:
        key = (round(row[0], 4), round(row[1], 4))
        if key in seen:
            continue
        seen.add(key); merged.append(row)
    return merged


def merge(ftype):
    """Fold shard parts into one file. Never overwrites a healthy file with a
    thinner one -- same anti-wipe rule the other harvesters use."""
    parts = sorted(glob.glob("remains_local_%s_part*.json" % ftype))
    rows = []
    for p in parts:
        try:
            rows += _read_rows(p)
            print("  merge: read %s" % p)
        except Exception as e:
            print("  merge: %s unreadable: %s" % (p, e))
    merged = _dedup(rows)
    fn = _outfile(ftype)
    if os.path.exists(fn):
        try:
            prior = _read_rows(fn)
            if len(merged) < len(prior) * 0.6:
                print("  merge: %d rows is far thinner than the existing %d -- "
                      "keeping the existing file" % (len(merged), len(prior)))
                return prior
        except Exception:
            pass
    if not merged:
        print("  merge: nothing to write for %s" % ftype)
        return []
    _write_type(ftype, merged, len(parts))
    return merged


# ---------------------------------------------------------------------------
# GEOGRAPHIC SHARDING OF THE PUBLISHED LAYER
# ---------------------------------------------------------------------------
# One global file per type worked at 17,000 cemeteries. It does not work at half a
# million: that is roughly 7 MB gzipped and ~40 MB of JSON parsed on every page
# load, which is slow on a desktop and fatal on a phone.
#
# So any type over TILE_THRESHOLD rows is published as a grid of LAYER_TILE_DEG
# cells, and the map fetches only the cells its viewport touches. Small types
# (crematoria are ~119 worldwide) stay as one file, because a manifest lookup and
# a second request would cost more than the file does.
#
# The manifest records which scheme each type uses and how many rows sit in each
# cell, so the map never has to probe for a file that does not exist -- an empty
# ocean cell is simply absent from the manifest and never requested.
LAYER_TILE_DEG = float(os.environ.get("LAYER_TILE_DEG", "10"))
TILE_THRESHOLD = int(os.environ.get("TILE_THRESHOLD", "40000"))
MANIFEST_FILE = "remains_local_manifest.json"


def _cell_key(lat, lng):
    d = LAYER_TILE_DEG
    return "%d_%d" % (int(math.floor(lat / d) * d), int(math.floor(lng / d) * d))


def _load_manifest():
    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"tile_deg": LAYER_TILE_DEG, "types": {}}


def _save_manifest(man):
    man["tile_deg"] = LAYER_TILE_DEG
    man["note"] = ("Which facility types are published as one file and which as a "
                   "grid of tiles, with the row count in each tile. The map reads "
                   "this first so it only requests cells that exist.")
    with open(MANIFEST_FILE, "w", encoding="utf-8") as fh:
        json.dump(man, fh, sort_keys=True)


def _write_type(ftype, rows, nparts):
    """Publish one type: a single file if small, a tile grid if large."""
    man = _load_manifest()
    if len(rows) < TILE_THRESHOLD:
        fn = _outfile(ftype)
        _write_rows(fn, rows)
        sz = os.path.getsize(fn) / 1e6
        man["types"][ftype] = {"mode": "single", "count": len(rows),
                               "file": fn, "mb": round(sz, 3)}
        _save_manifest(man)
        print("  merge: %s -> %d facilities from %d parts (%.2f MB gz, single file)"
              % (fn, len(rows), nparts, sz))
        return

    cells = {}
    for r in rows:
        cells.setdefault(_cell_key(r[0], r[1]), []).append(r)
    total_mb = 0.0
    counts = {}
    for key, cr in cells.items():
        fn = "remains_local_%s_%s.json.gz" % (ftype, key)
        _write_rows(fn, cr)
        total_mb += os.path.getsize(fn) / 1e6
        counts[key] = len(cr)
    # a stale single file would otherwise be served forever alongside the tiles
    old = _outfile(ftype)
    if os.path.exists(old):
        os.remove(old)
        print("  merge: removed the old single %s (now tiled)" % old)
    man["types"][ftype] = {"mode": "tiled", "count": len(rows),
                           "tile_deg": LAYER_TILE_DEG, "tiles": counts,
                           "mb": round(total_mb, 2)}
    _save_manifest(man)
    biggest = max(counts.values())
    print("  merge: %s -> %d facilities from %d parts, split into %d tiles of "
          "%.0f deg (%.1f MB gz total, largest tile %d rows)"
          % (ftype, len(rows), nparts, len(cells), LAYER_TILE_DEG,
             total_mb, biggest))


def main():
    want = os.environ.get("FAC_TYPE", "all").lower()
    types = list(SETS.keys()) if want in ("", "all") else [want]
    for t in types:
        if t not in SETS:
            print("unknown FAC_TYPE %r; choose from %s" % (t, ", ".join(SETS)))
            sys.exit(2)
    if os.environ.get("FAC_MERGE") == "1":
        for t in types:
            merge(t)
        return
    for t in types:
        harvest(t)


if __name__ == "__main__":
    main()
