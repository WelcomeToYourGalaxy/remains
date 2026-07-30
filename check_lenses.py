#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_lenses.py  --  live-checks every URL in lenses.json and writes
lenses_status.json, which the map reads to grey out links that no longer resolve.

WHY THIS EXISTS
lenses.json is hand-curated. Curation is the one part of this project a harvester
cannot do, but it is also the part that rots fastest: agencies rename, programs
move, ministries merge. Shipping a curated list and *asserting* every link works
would be the same failure as shipping an unverified data source. So the list is
shipped unverified by design, and this script is what verifies it -- on a schedule,
in public, with the result visible in the map.

The map's behaviour:
    ok        -> normal link
    redirect  -> link shown, with the final URL noted
    dead      -> link greyed out and labelled, NOT silently removed
    unchecked -> small "unverified" marker (no status file yet)

Leaving dead links visible-but-marked is deliberate. A resource that has vanished
is itself information: it tells you an office closed or a program was wound up,
which is worth knowing. Silently deleting it would hide that.

Entries with no "url" (only a "find" string) are reported as "find-only" and are
not failures -- they are the honest form for a resource whose exact URL was never
verified.

Run:
    python check_lenses.py
Tunables (env):
    LENSES_FILE     default lenses.json
    LENSES_STATUS   default lenses_status.json
    CHECK_TIMEOUT   per-request seconds (default 20)
    CHECK_SLEEP     politeness pause between requests (default 0.7)
"""

import json, os, sys, time, datetime
import urllib.request, urllib.error, urllib.parse

LENSES = os.environ.get("LENSES_FILE", "lenses.json")
STATUS = os.environ.get("LENSES_STATUS", "lenses_status.json")
TIMEOUT = int(os.environ.get("CHECK_TIMEOUT", "20"))
SLEEP = float(os.environ.get("CHECK_SLEEP", "0.7"))

UA = ("Mozilla/5.0 (compatible; remains-map-linkcheck/1.0; "
      "+wheelock.chris@gmail.com)")


def _check(url):
    """Return (state, code, final_url, note).

    Tries GET rather than HEAD: a large share of government sites answer HEAD with
    405 or 403 while serving GET perfectly well, so HEAD-only checking would
    condemn working links. Reads only the first bytes, so this stays cheap.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            code = r.getcode()
            final = r.geturl()
            try:
                r.read(2048)
            except Exception:
                pass
            if final.rstrip("/") != url.rstrip("/"):
                return "redirect", code, final, "resolves to a different URL"
            return "ok", code, final, ""
    except urllib.error.HTTPError as e:
        # 401/403/405/429 mean "something is there but it won't answer a bot".
        # That is not a dead link, and marking it dead would be wrong.
        if e.code in (401, 403, 405, 406, 429, 999):
            return "blocked", e.code, url, "server refuses automated requests"
        return "dead", e.code, url, "HTTP %s" % e.code
    except Exception as e:
        return "dead", 0, url, str(e)[:90]


def main():
    if not os.path.exists(LENSES):
        print("%s not found" % LENSES)
        sys.exit(2)
    doc = json.load(open(LENSES, encoding="utf-8"))
    lenses = doc.get("lenses") or []

    results = {}
    tally = {"ok": 0, "redirect": 0, "blocked": 0, "dead": 0, "find-only": 0}
    dead_list, redirects = [], []

    total = sum(len(l.get("items") or []) for l in lenses)
    print("checking %d entries across %d lenses\n" % (total, len(lenses)))

    for lens in lenses:
        print("== %s ==" % lens.get("label", lens.get("key")))
        for it in (lens.get("items") or []):
            name = it.get("name", "?")
            url = it.get("url")
            if not url:
                results[name] = {"state": "find-only", "find": it.get("find", "")}
                tally["find-only"] += 1
                print("  %-56s find-only" % name[:56])
                continue
            state, code, final, note = _check(url)
            rec = {"state": state, "code": code, "url": url}
            if final and final.rstrip("/") != url.rstrip("/"):
                rec["final"] = final
            if note:
                rec["note"] = note
            results[name] = rec
            tally[state] = tally.get(state, 0) + 1
            if state == "dead":
                dead_list.append((name, url, note))
            if state == "redirect":
                redirects.append((name, url, final))
            print("  %-56s %-8s %s" % (name[:56], state,
                                       (note or final if state != "ok" else "")[:60]))
            time.sleep(SLEEP)
        print("")

    out = {"checked": datetime.datetime.utcnow().isoformat() + "Z",
           "tally": tally, "entries": results}
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)

    print("=== LENS CHECK ===")
    for k in ("ok", "redirect", "blocked", "dead", "find-only"):
        print("  %-10s %d" % (k, tally.get(k, 0)))
    if redirects:
        print("\n  REDIRECTS (update lenses.json to the final URL):")
        for n, u, f in redirects:
            print("    %s\n      %s\n      -> %s" % (n, u, f))
    if dead_list:
        print("\n  DEAD (fix or replace -- the map will grey these out):")
        for n, u, note in dead_list:
            print("    %s\n      %s  (%s)" % (n, u, note))
    else:
        print("\n  no dead links")
    print("  wrote %s" % STATUS)
    print("=== END ===")

    # A dead link is a maintenance task, not a build failure: the map degrades
    # gracefully and the workflow should still commit the status file.
    return 0


if __name__ == "__main__":
    sys.exit(main())
