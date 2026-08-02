#!/usr/bin/env python3
"""
harvest_remains.py  --  builds remains.json.gz for the Live Unearthings map.

SIBLING OF harvest_projects.py. Same run environment, same output contract, same
honesty rules. Where that harvester answers "what is about to be built here",
this one answers "whose dead are being disturbed, by whom, under what authority".

RUN ENVIRONMENT: GitHub Actions (scheduled), NOT the build sandbox. The sandbox
network is locked to package registries; this script needs open-web access, so it
runs in your repo's Actions runner (like harvest_projects.py and wire_harvest.py).

-----------------------------------------------------------------------------
PLACEMENT POLICY  --  READ THIS BEFORE ADDING A SOURCE
-----------------------------------------------------------------------------
This map must not become a looting index. Precise grave coordinates are withheld
by archaeologists, tribes and states for good reason: in the US, site-location
data is exempt from disclosure under NHPA s.304 and ARPA s.9 precisely because
publishing it invites desecration.

So the unit of this map is the ACCOUNTABLE ACTOR AND THE DECISION, not the grave:
  * a repatriation notice is plotted at the INSTITUTION that holds the remains
  * a heritage-harm permit is plotted at the PERMIT AREA centroid
  * an environmental review is plotted at the ADMIN UNIT it names
  * anything that is itself a burial LOCATION is snapped to a coarse grid
    (COARSE_GRID_DEG, ~5 km) and stamped geo="coarsened"

_place() enforces this. It is a fail-safe gate: a source cannot publish a precise
burial point by accident, because the gate coarsens on `kind`, not on the source's
good intentions. If you add a source, pick the right `kind` and the gate does the
rest. Do not bypass it.

-----------------------------------------------------------------------------
RECORD SCHEMA (each fetcher returns a list of these)
-----------------------------------------------------------------------------
  name      short title of the event/decision
  kind      taxonomy below -- drives the placement gate and the map's colour
  posture   harm | watch | redress | unlawful   (see POSTURE)
  trigger   what is driving it: development, extraction, infrastructure,
            research, law, conflict, erosion, looting, unknown
  country   country name;  region  sub-national unit (state/province) if known
  lat,lng   placement, after _place()
  geo       exact | area | admin | coarsened   -- how to read the dot
  count     number of individuals, ONLY when the source states one
  held_by   institution or agency holding/controlling the remains
  actor     the party doing the disturbing (developer, agency, permit holder)
  status    the source's own status wording, normalised lightly
  url       deep link to the primary record
  date      publication/decision date (ISO)
  deadline  comment/claim deadline (ISO) when the source gives one
  desc      <=200 chars of prose, including any placement caveat
  source    fetcher key -- must match the _run() name
  impact    1-5, from rate_remains()

Output is remains.json.gz  ({"_meta": {...}, "records": [...]}).
"""
import math
import os
import datetime as dt
import gzip
import json
import io
import contextlib
import ssl
import traceback, sys, os, re, time, datetime, urllib.request, urllib.parse, urllib.error, gzip

UA = "galaxy-remains-harvester (contact: wheelock.chris@gmail.com)"
TIMEOUT = 30

REMAINS_GZ = "remains.json.gz"
REMAINS_PLAIN = "remains.json"

# The portal registries this harvester borrows live in the projects repo. See
# _fed_registries() for why they are fetched rather than copied.
SIBLING_HARVESTER_URL = ("https://raw.githubusercontent.com/WelcomeToYourGalaxy/"
                         "local-map/main/harvest_projects.py")
_REG_CACHE = {"done": False, "reg": None}


# ---------------------------------------------------------------------------
# OUTPUT IO  (gzip only -- see harvest_projects.py for why)
# ---------------------------------------------------------------------------
def _remains_exists():
    return os.path.exists(REMAINS_GZ) or os.path.exists(REMAINS_PLAIN)


def _load_remains():
    if os.path.exists(REMAINS_GZ):
        with gzip.open(REMAINS_GZ, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(REMAINS_PLAIN, encoding="utf-8") as f:
        return json.load(f)


def _dump_remains(out):
    with gzip.open(REMAINS_GZ, "wt", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    if os.path.exists(REMAINS_PLAIN):
        try:
            os.remove(REMAINS_PLAIN)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# TAXONOMY
# ---------------------------------------------------------------------------
# kind -> (human label, default posture, coarsen?)
#   coarsen=True means the record IS a burial location and must be blurred.
KINDS = {
    "repatriation":  ("Repatriation notice",          "redress",  False),
    "disposition":   ("Intended disposition",         "redress",  False),
    "reinterment":   ("Transfer or reinterment",      "redress",  False),
    "holding":       ("Institution holding remains",  "watch",    False),
    "harm-permit":   ("Permit to harm burial site",   "harm",     False),
    "excavation":    ("Licensed excavation",          "harm",     True),
    "exhumation":    ("Exhumation / cemetery removal", "harm",    True),
    "removed-ground": ("Burial ground no longer there", "harm",  True),
    "mass-grave":    ("Mass-grave recovery",          "redress",  True),
    # One named person, one recovery. Kept distinct from mass-grave so the map can
    # show them together or apart -- they are the same field of work at a very
    # different scale, and conflating them would hide both.
    "forensic-case": ("Individual forensic recovery",  "redress",  True),
    # Remains leaving a collection WITHOUT going home: deaccessioned, sold, or moved
    # between institutions. Kept apart from `reinterment` because a transfer is not
    # a return, and a map that merged them would report custody changes as redress.
    "transfer":      ("Deaccession or transfer",       "watch",    False),
    "discovery":     ("Inadvertent discovery",        "watch",    True),
    "looting":       ("Illicit disturbance",          "unlawful", True),
    "review":        ("Review flagging burials",      "watch",    False),
}

POSTURE = {
    "harm":     "Authorises or carries out disturbance",
    "watch":    "Undecided, disclosed, or under review",
    "redress":  "Returning, reburying, or recovering for descendants",
    "unlawful": "Disturbance without lawful authority",
}

TRIGGERS = ("development", "extraction", "infrastructure", "research",
            "law", "conflict", "erosion", "looting", "unknown")

# geo values, weakest to strongest claim about where the dot is
GEO_EXACT, GEO_AREA, GEO_ADMIN, GEO_COARSE = "exact", "area", "admin", "coarsened"

# ~0.05 deg latitude is about 5.5 km. Wide enough that a dot cannot be walked to.
COARSE_GRID_DEG = 0.05


def _coarsen(lat, lng):
    """Snap to the centre of a COARSE_GRID_DEG cell. Deterministic (so the dot
    does not wander between harvests) and lossy in both axes."""
    g = COARSE_GRID_DEG
    return (round((lat // g) * g + g / 2.0, 4),
            round((lng // g) * g + g / 2.0, 4))


def _place(rec, lat, lng, geo):
    """THE PLACEMENT GATE. Writes lat/lng/geo onto rec, coarsening whenever the
    record's kind is a burial location. Returns rec for chaining."""
    if lat is None or lng is None:
        rec["lat"] = rec["lng"] = None
        return rec
    lat, lng = float(lat), float(lng)
    _, _, must_coarsen = KINDS.get(rec.get("kind"), ("", "watch", True))
    if must_coarsen and geo in (GEO_EXACT, GEO_AREA):
        lat, lng = _coarsen(lat, lng)
        geo = GEO_COARSE
    rec["lat"] = round(lat, 4)
    rec["lng"] = round(lng, 4)
    rec["geo"] = geo
    return rec


GEO_NOTE = {
    GEO_EXACT:  "Plotted at the named institution or address.",
    GEO_AREA:   "Plotted at the centre of the permit or project area.",
    GEO_ADMIN:  "Plotted at the centre of the administrative unit named -- not the site.",
    GEO_COARSE: "Location deliberately blurred to about 5 km. This map does not "
                "publish grave coordinates.",
}


# ---------------------------------------------------------------------------
# IMPACT RATING  (1 .. 5)
# ---------------------------------------------------------------------------
# Scale is "how many of the dead are affected, and how irreversibly". A permit to
# destroy outranks a review of the same site; a return outranks neither -- posture
# carries that distinction, impact only carries magnitude.
_KIND_FLOOR = {
    "harm-permit": 3, "exhumation": 3, "removed-ground": 3, "looting": 3,
    "excavation": 2, "mass-grave": 3, "forensic-case": 1, "discovery": 2, "review": 2,
    "transfer": 2,
    "repatriation": 2, "disposition": 2, "reinterment": 2, "holding": 2,
}


def _count_score(n):
    if not n:
        return 0
    try:
        n = float(n)
    except (TypeError, ValueError):
        return 0
    if n >= 500: return 5
    if n >= 100: return 4
    if n >= 20:  return 3
    if n >= 5:   return 2
    return 1


def rate_remains(r):
    floor = _KIND_FLOOR.get(r.get("kind"), 2)
    cs = _count_score(r.get("count"))
    if cs:
        return max(1, min(5, max(floor, cs)))
    return max(1, min(5, floor))


# ---------------------------------------------------------------------------
# HELPERS  (same contract as harvest_projects.py so behaviour matches)
# ---------------------------------------------------------------------------
# apps.cr.nps.gov serves an INCOMPLETE certificate chain -- it omits the
# intermediate, so OpenSSL cannot build a path and every read dies with
# "unable to get local issuer certificate". Confirmed on the 2026-07-30 run. It is
# a misconfiguration on their end; nothing local fixes it, and this module is
# deliberately stdlib-only, so certifi is not available to swap in.
#
# The narrow, documented concession: for hosts in this set ONLY, retry once with
# verification off, and say so loudly in the log every time it happens. These are
# public read-only government tables fetched anonymously -- nothing is sent, so
# there are no credentials to leak. The residual risk is being served forged
# public data, which would surface as an implausible yield in the diagnostic.
# Every other host on the internet keeps full verification.
_TLS_BROKEN_HOSTS = {"apps.cr.nps.gov"}


def _unverified_ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _tls_fallback_ctx(url, err):
    """Return an unverified context iff this host is allowlisted AND the failure
    really was a chain-building failure. Otherwise None, and the error stands."""
    try:
        host = urllib.parse.urlparse(url).netloc.split(":")[0].lower()
    except Exception:
        return None
    if host not in _TLS_BROKEN_HOSTS:
        return None
    if "CERTIFICATE_VERIFY_FAILED" not in str(err):
        return None
    print("  [tls] %s served an incomplete chain -- retrying WITHOUT verification "
          "(allowlisted host, public read-only data)" % host)
    return _unverified_ctx()


def _get_json(url):
    """GET JSON with ONE retry on transient failures (timeouts, 5xx, 429)."""
    last = None
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
            except urllib.error.URLError as e:
                ctx = _tls_fallback_ctx(url, e.reason)
                if ctx is None:
                    raise
                with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429 or e.code >= 500:
                if attempt == 0:
                    time.sleep(2.0); continue
            raise
        except Exception as e:
            last = e
            if attempt == 0:
                time.sleep(2.0); continue
            raise
    raise last


def _get_bytes(url, limit=40000000):
    """Raw bytes, for gzipped payloads. Same TLS fallback as the text fetcher."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=max(TIMEOUT, 120)) as r:
            return r.read(limit)
    except urllib.error.URLError as e:
        ctx = _tls_fallback_ctx(url, e.reason)
        if ctx is None:
            raise
        with urllib.request.urlopen(req, timeout=max(TIMEOUT, 120), context=ctx) as r:
            return r.read(limit)


def _get_text(url, limit=4000000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read(limit).decode("utf-8", "replace")
    except urllib.error.URLError as e:
        ctx = _tls_fallback_ctx(url, e.reason)
        if ctx is None:
            raise
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            return r.read(limit).decode("utf-8", "replace")


def _first(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _iso_date(v):
    if not v:
        return ""
    s = str(v)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(1)), int(m.group(2)))
    if s.isdigit() and len(s) == 13:          # epoch ms (ArcGIS)
        try:
            return datetime.datetime.utcfromtimestamp(int(s) / 1000).date().isoformat()
        except Exception:
            return ""
    return ""


def _epoch_ms(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    try:
        return datetime.datetime.utcfromtimestamp(n / 1000.0).date().isoformat()
    except Exception:
        return ""


def _geom_center(geom):
    t = (geom or {}).get("type"); c = (geom or {}).get("coordinates")
    if not c:
        return None
    if t == "Point" and len(c) >= 2:
        try:
            return (float(c[1]), float(c[0]))
        except Exception:
            return None
    pts = []

    def collect(x):
        if isinstance(x, (list, tuple)):
            if len(x) >= 2 and isinstance(x[0], (int, float)) and isinstance(x[1], (int, float)):
                pts.append((float(x[1]), float(x[0])))
            else:
                for i in x:
                    collect(i)
    collect(c)
    if not pts:
        return None
    return (sum(a for a, _ in pts) / len(pts), sum(b for _, b in pts) / len(pts))


def _arcgis_query_all(base_url, layer=0, page=2000, max_pages=30, label="",
                      where="1=1"):
    """Query an ArcGIS layer with resultOffset paging -- returns ALL features."""
    feats = []
    for pg in range(max_pages):
        q = base_url.rstrip("/") + "/%d/query?" % layer + urllib.parse.urlencode({
            "where": where, "outFields": "*", "f": "geojson", "outSR": "4326",
            "resultRecordCount": page, "resultOffset": pg * page})
        try:
            gj = _get_json(q)
        except Exception as e:
            if pg == 0:
                print("  %s query failed: %s" % (label, e))
            break
        if not isinstance(gj, dict) or gj.get("error"):
            if pg == 0 and isinstance(gj, dict):
                print("  %s error: %s" % (label, str(gj.get("error"))[:140]))
            break
        got = gj.get("features") or []
        feats += got
        if len(got) < page:
            break
        time.sleep(0.4)
    return feats


# --- US state centroids (approximate geographic centres) --------------------
STATE_CENTROID = {
    "Alabama": (32.79, -86.83), "Alaska": (64.00, -152.00), "Arizona": (34.17, -111.93),
    "Arkansas": (34.90, -92.44), "California": (37.18, -119.47), "Colorado": (38.99, -105.55),
    "Connecticut": (41.62, -72.73), "Delaware": (38.99, -75.51), "Florida": (28.63, -82.45),
    "Georgia": (32.64, -83.44), "Hawaii": (20.29, -156.37), "Idaho": (44.39, -114.66),
    "Illinois": (40.06, -89.36), "Indiana": (39.89, -86.28), "Iowa": (42.07, -93.50),
    "Kansas": (38.49, -98.38), "Kentucky": (37.53, -85.30), "Louisiana": (31.07, -92.00),
    "Maine": (45.37, -69.24), "Maryland": (39.06, -76.80), "Massachusetts": (42.26, -71.81),
    "Michigan": (44.35, -85.41), "Minnesota": (46.28, -94.31), "Mississippi": (32.74, -89.68),
    "Missouri": (38.37, -92.48), "Montana": (47.05, -109.63), "Nebraska": (41.53, -99.81),
    "Nevada": (39.36, -116.63), "New Hampshire": (43.68, -71.58), "New Jersey": (40.19, -74.67),
    "New Mexico": (34.42, -106.11), "New York": (42.95, -75.53), "North Carolina": (35.54, -79.36),
    "North Dakota": (47.45, -100.47), "Ohio": (40.29, -82.79), "Oklahoma": (35.59, -97.49),
    "Oregon": (43.94, -120.56), "Pennsylvania": (40.87, -77.80), "Rhode Island": (41.68, -71.56),
    "South Carolina": (33.92, -80.90), "South Dakota": (44.44, -100.23),
    "Tennessee": (35.86, -86.35), "Texas": (31.48, -99.33), "Utah": (39.31, -111.67),
    "Vermont": (44.07, -72.67), "Virginia": (37.52, -78.85), "Washington": (47.38, -120.45),
    "West Virginia": (38.64, -80.62), "Wisconsin": (44.64, -89.99), "Wyoming": (42.99, -107.55),
    "District of Columbia": (38.90, -77.02), "Puerto Rico": (18.22, -66.43),
    "Guam": (13.44, 144.79), "American Samoa": (-14.27, -170.13),
    "Northern Mariana Islands": (15.10, 145.70), "U.S. Virgin Islands": (18.34, -64.90),
}
_ST_ABBR = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
    "GU": "Guam", "AS": "American Samoa", "MP": "Northern Mariana Islands",
    "VI": "U.S. Virgin Islands",
}

# --- budgeted Nominatim geocoder (same politeness budget as harvest_projects) -
_GEO_CACHE = {}
_GEO_CALLS = [0]
_GEO_MAX = int(os.environ.get("GEO_BUDGET", "200"))


def _geocode(q, cc="us"):
    """One Nominatim hit per unique query, hard-capped per run. Returns (lat,lng)
    or None. Never guesses: a miss is a miss and the caller falls back to admin."""
    key = (q or "").strip().lower() + "|" + cc
    if not key.strip("|"):
        return None
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    if _GEO_CALLS[0] >= _GEO_MAX:
        return None
    _GEO_CALLS[0] += 1
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({
        "q": q, "format": "json", "limit": 1, "countrycodes": cc})
    try:
        time.sleep(1.05)                      # Nominatim: max 1 req/sec
        data = _get_json(url)
        if data:
            hit = (float(data[0]["lat"]), float(data[0]["lon"]))
            _GEO_CACHE[key] = hit
            return hit
    except Exception:
        pass
    _GEO_CACHE[key] = None
    return None


# ---------------------------------------------------------------------------
# VOCABULARY  --  what counts as "impacting remains"
# ---------------------------------------------------------------------------
# Deliberately narrower than a general heritage filter: "archaeology" alone is not
# enough, because most archaeology involves no burials. A record must name human
# remains, a burial, a grave, a cemetery, or a statutory burial regime.
_REMAINS_RE = re.compile(
    r"\b("
    r"human remains|skeletal remains|ancestral remains|osteolog|"
    r"burial(?:s|\sground|\ssite|\splace)?|reburial|interment|reinterment|disinterment|"
    r"grave(?:s|yard|site)?|gravesite|cemeter(?:y|ies)|churchyard|necropolis|ossuar|"
    r"tomb(?:s|stone)?|barrow|tumul(?:us|i)|mound\sgroup|kurgan|crypt|catacomb|"
    r"exhumation|exhume|mass\sgrave|funerary\sobject|"
    r"nagpra|repatriat|unmarked\sgrave|potter'?s\sfield|"
    r"fosa(?:s)?\scomun(?:e|es)?|cementerio|sepultura|"
    r"s\u00e9pulture|cimeti\u00e8re|ossements|"
    r"gr\u00e4berfeld|friedhof|gebeine|"
    r"begraafplaats|grafveld"
    r")\b", re.I)

# Things that use burial vocabulary but are not an unearthing event.
_REMAINS_DENY_RE = re.compile(
    r"\b("
    r"burial\sat\ssea\spermit|carbon\sburial|carbon\ssequestration|"
    r"cable\sburial|pipeline\sburial|burial\sdepth|buried\sutilit\w*|"
    r"landfill\scell|waste\sburial|animal\sburial|livestock\sburial|"
    r"pet\scemeter\w*|cemetery\smaintenance|cemetery\smowing|lawn\scare|"
    r"grave\sdigger\svacancy|cemetery\sfee\sschedule|burial\splot\ssales"
    r")\b", re.I)


# ---------------------------------------------------------------------------
# SINGLE-CASE EXCLUSION
# ---------------------------------------------------------------------------
# This map is about MULTI-PERSON incidents and decisions taken over groups of the
# dead: a burial ground cleared, a collection inventoried, a mass grave opened, a
# community's ancestors returned. It is NOT a missing-persons register.
#
# An individual forensic case -- one named person, one coroner's file, one body
# recovered and identified -- is somebody's private grief with a live investigation
# attached. Plotting it would put a named death on a public map for no gain to
# anyone, and would bury the pattern this map exists to show under thousands of
# individual files. It is also why the coroner and unidentified-remains registers
# were left off the source roster deliberately, not by oversight.
#
# The test is the SHAPE of the record, not its subject. "Remains of a missing hiker
# identified" is one case. "Remains of at least 47 individuals" is this map's business.
_SINGLE_CASE = (
    "missing person", "missing hiker", "missing walker", "missing woman",
    "missing man", "missing girl", "missing boy", "cold case",
    "coroner's inquest", "coroners inquest", "inquest into the death",
    "murder victim identified", "identified as missing", "body found in",
    "remains identified as", "named as the victim",
    "persona desaparecida", "caso individual", "vermisste person",
)
_MULTI_HINT = (
    "individuals", "remains of at least", "a minimum of", "mass grave", "mass graves",
    "burial ground", "cemetery", "collection", "inventory", "ancestors",
    "fosa comun", "fosas comunes", "massengrab",
)


# Default OFF: individual cases are IN SCOPE. The classifier is kept because it is
# still useful -- it now LABELS rather than excludes, so a single recovery gets the
# `forensic-case` kind and can be filtered apart from group events on the map. Set
# EXCLUDE_INDIVIDUAL_CASES=1 to go back to group-only.
EXCLUDE_INDIVIDUAL_CASES = os.environ.get("EXCLUDE_INDIVIDUAL_CASES", "") == "1"


def _is_individual_case(text):
    """True if this reads as ONE person's case rather than a decision about a group."""
    t = (text or "").lower()
    if not any(x in t for x in _SINGLE_CASE):
        return False
    # An explicit plural or group noun overrides: a story can mention a missing
    # person and still be about a mass grave.
    return not any(x in t for x in _MULTI_HINT)


def _is_remains(text):
    t = text or ""
    if _REMAINS_DENY_RE.search(t):
        return False
    return bool(_REMAINS_RE.search(t))


# ---------------------------------------------------------------------------
# POLICY REFUSAL: site registers are not events, and must not be published here
# ---------------------------------------------------------------------------
# Discovered while evaluating New Zealand (2026-07): ArchSite holds ~80,000 recorded
# archaeological site locations, and NZ district councils publish "Archaeological &
# Waahi Tapu Sites" layers on CKAN. The federation sweep, searching for burial and
# cemetery terms, would find exactly those layers.
#
# The blurring gate is NOT sufficient protection for them. Coarsening one grave to a
# 5 km cell hides it in the noise; coarsening a register of thousands of sites still
# discloses which cells contain them, and for wahi tapu -- sacred places, held
# deliberately un-public by the communities they belong to -- that disclosure is the
# harm. A register is also not an unearthing: nothing has happened, nobody has
# applied, there is no decision and no accountable actor. It fails this map's own
# definition of a record twice over.
#
# So these are refused at DATASET level, before any point is read. This is the first
# category rejected because the data should not be published rather than because it
# could not be obtained.
_SITE_REGISTER_RE = re.compile(
    r"("
    r"wahi\s*tapu|wāhi\s*tapu|waahi\s*tapu|"          # NZ sacred places
    r"site\s+record(?:ing)?\s+scheme|archsite|"
    r"sacred\s+site|secret\s+site|restricted\s+site|"
    r"archaeolog\w*\s+(?:site|sites)\s+(?:register|registry|inventory|schedule|list|layer|dataset)|"
    r"(?:register|registry|inventory|schedule)\s+of\s+archaeolog|"
    r"heritage\s+(?:site|sites)\s+(?:register|registry|inventory|schedule)|"
    r"known\s+archaeolog\w*\s+sites|recorded\s+archaeolog\w*\s+sites|"
    r"sitios\s+arqueológicos|patrimonio\s+arqueológico\s+inventario|"
    r"carte\s+archéologique|fundstellen|denkmalliste|"
    r"tumuli\s+(?:register|inventory)|barrow\s+(?:register|inventory)"
    r")", re.I)


def _is_site_register(text):
    """True if this dataset is a REGISTER OF SITE LOCATIONS rather than a record of
    something being done. Refused on policy, not on access. See the note above."""
    return bool(_SITE_REGISTER_RE.search(text or ""))


_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
            "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
            "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_MNI_RE = re.compile(
    r"(?:at\sleast|minimum\sof|total\sof|representing)\s+"
    r"([\d,]+|[a-z\-]+)\s+"
    r"(?:individual|person|people|set(?:s)?\sof\shuman\sremains)", re.I)


def _mni(text):
    """Number of individuals, ONLY when the source states one. Never estimated."""
    m = _MNI_RE.search(text or "")
    if not m:
        return None
    raw = m.group(1).replace(",", "").strip().lower()
    if raw.isdigit():
        try:
            return int(raw)
        except ValueError:
            return None
    return _WORDNUM.get(raw)


# ---------------------------------------------------------------------------
# SOURCE 1 -- US NAGPRA notices  [VERIFIED 2026-07: federalregister.gov API v1]
# ---------------------------------------------------------------------------
# The Federal Register is the statutory publication venue for every NAGPRA notice.
# Free API, no key, same endpoint harvest_projects.py already uses in production.
#
# Four notice families, each a different moment in the life of unearthed remains:
#   Notice of Inventory Completion  -- an institution has inventoried remains it
#                                      holds and identified who they belong to
#   Notice of Intent to Repatriate  -- agreement to transfer control
#   Notice of Intended Disposition  -- remains newly removed from Federal or
#                                      tribal land are to be disposed of
#   Notice of Transfer/Reinterment  -- physical return or reburial
#
# Plotted at the HOLDING INSTITUTION (the accountable party), never at the place
# the remains were taken from -- notices name removal counties in prose and this
# harvester keeps those in `desc` without mapping them. See PLACEMENT POLICY.
# Every NAGPRA notice family the Federal Register publishes, not just the four
# that announce a completed decision. The additions matter for different reasons:
#
#  * "Receipt of a Request" / "Request for Repatriation" is the PENDING side. A map
#    of completed returns shows the system working; the pending queue shows what is
#    stuck, which is usually the story. Filed as `holding` -- the institution still
#    has them.
#  * "Deaccession" and "Notice of Transfer" cover remains LEAVING a collection
#    without going home: sold, transferred between institutions, or otherwise moved.
#    A transfer is not a return, and conflating them would flatter the record.
#  * Corrections are how a wrong affiliation finding gets undone. They are small in
#    number and disproportionately important.
_NAGPRA_QUERIES = [
    ("Notice of Inventory Completion", "repatriation"),
    ("Notice of Intent to Repatriate", "repatriation"),
    ("Notice of Intended Disposition", "disposition"),
    ("Notice of Transfer of Control", "reinterment"),
    # pending, not completed -- the institution still holds them
    ("Receipt of a Request for Repatriation", "holding"),
    ("Request for Repatriation", "holding"),
    ("Notice of Receipt of Request", "holding"),
    # leaving a collection, but not going home
    ("Notice of Deaccession", "transfer"),
    ("Deaccession of Human Remains", "transfer"),
    ("Notice of Transfer", "transfer"),
    # the record correcting itself
    ("Correction to Notice of Inventory Completion", "review"),
    ("Correction to Notice of Intent to Repatriate", "review"),
]
# "City ST" or "City, ST" at the end of a NAGPRA notice title.
_NAG_PLACE_RE = re.compile(r",\s*([A-Za-z .'\u2019\-]{2,40}?),?\s+([A-Z]{2})\s*$")


def fetch_nagpra_notices(days=1095, per_page=100, max_pages=12):
    """NAGPRA notices published in the Federal Register.

    Eight of the twelve families below are NEW and unverified against live results
    -- the Federal Register was unreachable from the machine that wrote them, so
    the exact notice wording could not be confirmed. The per-family tally printed at
    the end of this function is how you find out: a family reporting API hits but
    keeping 0 records has the wrong wording, and one reporting 0 hits does not exist
    under that name. Both are one-line fixes to _NAGPRA_QUERIES; neither can corrupt
    the data, because the title check already rejects anything that is not that
    notice."""
    out = []
    per_family = {}
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    for term, kind in _NAGPRA_QUERIES:
        for page in range(1, max_pages + 1):
            # Term is passed UNQUOTED. harvest_projects.py has queried this API in
            # production for a long time with a bare phrase, and quoting it was an
            # unverified change on my part. An unquoted search returns a superset,
            # and the title check below narrows it to exactly the notices we want --
            # so dropping the quotes can only help.
            parts = [("conditions[term]", term),
                     ("conditions[type][]", "NOTICE"),
                     ("conditions[publication_date][gte]", since),
                     ("per_page", per_page), ("page", page), ("order", "newest")]
            for f in ("title", "abstract", "agencies", "publication_date",
                      "html_url", "comments_close_on", "document_number"):
                parts.append(("fields[]", f))
            url = ("https://www.federalregister.gov/api/v1/documents.json?"
                   + urllib.parse.urlencode(parts))
            try:
                data = _get_json(url)
            except Exception as e:
                if page == 1:
                    print("  nagpra %s failed: %s" % (term, e))
                break
            if page == 1:
                print("  nagpra %-32s api reports %s hit(s)"
                      % (term, (data or {}).get("count", "?")))
            rows = data.get("results") or []
            if not rows:
                break
            for d in rows:
                title = (d.get("title") or "").strip()
                if term.lower() not in title.lower():
                    continue                       # full-text hit, not a notice
                body = " ".join(filter(None, [title, d.get("abstract")]))
                inst, region, city = "", "", ""
                m = _NAG_PLACE_RE.search(title)
                if m:
                    city = m.group(1).strip()
                    region = _ST_ABBR.get(m.group(2), "")
                    head = title.split(":", 1)[-1]
                    inst = _NAG_PLACE_RE.sub("", head).strip(" ,;")
                else:
                    inst = title.split(":", 1)[-1].strip(" ,;")
                per_family[term] = per_family.get(term, 0) + 1
                rec = {"name": title[:150], "kind": kind,
                       "posture": KINDS[kind][1], "trigger": "law",
                       "country": "United States", "region": region,
                       "count": _mni(body), "held_by": inst[:120], "actor": inst[:120],
                       "status": "Notice published; claims window may be open",
                       "url": d.get("html_url"),
                       "date": _iso_date(d.get("publication_date")),
                       "deadline": _iso_date(d.get("comments_close_on")),
                       "source": "nagpra_notices"}
                # Placement: geocode the institution's city, else state centroid.
                latlng, geo = None, GEO_ADMIN
                if city and region:
                    latlng = _geocode("%s, %s" % (city, region), "us")
                    if latlng:
                        geo = GEO_EXACT
                if latlng is None and region in STATE_CENTROID:
                    latlng = STATE_CENTROID[region]
                    geo = GEO_ADMIN
                if latlng is None:
                    continue                       # no defensible placement
                _place(rec, latlng[0], latlng[1], geo)
                where = (city + ", " + region) if city and region else (region or "US")
                rec["desc"] = ("%s. Held at %s. %s"
                               % (KINDS[kind][0], where, GEO_NOTE[rec["geo"]]))[:200]
                rec["impact"] = rate_remains(rec)
                out.append(rec)
            if len(rows) < per_page:
                break
            time.sleep(0.3)
    # Which families are actually producing. Eight of the twelve are unverified;
    # this line is how you tell a wrong phrase from a family that does not exist.
    for term, _k in _NAGPRA_QUERIES:
        n = per_family.get(term, 0)
        if n == 0:
            print("  nagpra family kept NOTHING: %r -- wrong wording, or no such "
                  "notice family" % term)
    print("  nagpra: kept %d notice(s) across %d of %d families"
          % (len(out), sum(1 for t, _ in _NAGPRA_QUERIES if per_family.get(t)),
             len(_NAGPRA_QUERIES)))
    return out


# ---------------------------------------------------------------------------
# SOURCE 2 -- US federal reviews that flag burials  [VERIFIED path]
# ---------------------------------------------------------------------------
# Same Federal Register API. NEPA/NHPA notices are where a project first admits
# on the record that it will hit graves. These are the ones worth watching before
# a permit exists. Plotted at STATE level only -- notices carry no coordinates.
_FR_REVIEW_TERMS = ["human remains", "burial ground", "unmarked graves",
                    "Native American graves", "cemetery relocation",
                    "archaeological human remains"]


def _detect_state(text):
    hits = [s for s in STATE_CENTROID
            if re.search(r"\b" + re.escape(s) + r"\b", text or "")]
    return hits[0] if len(hits) == 1 else None     # only place if unambiguous


def _trigger_from_text(text):
    t = (text or "").lower()
    if re.search(r"\bmin(e|ing)|quarry|oil|gas|coal|lithium|drill", t):
        return "extraction"
    if re.search(r"highway|pipeline|transmission|rail|airport|bridge|reservoir|dam\b", t):
        return "infrastructure"
    if re.search(r"subdivision|housing|development|commercial|resort|campus", t):
        return "development"
    if re.search(r"erosion|sea.level|coastal|flood|storm|thaw|permafrost", t):
        return "erosion"
    if re.search(r"conflict|war\b|atrocit|genocide|disappear", t):
        return "conflict"
    if re.search(r"loot|illicit|traffick", t):
        return "looting"
    return "unknown"


def fetch_us_burial_reviews(days=365, per_page=100, max_pages=6):
    """Federal Register notices whose text puts burials in the path of a project."""
    out, seen = [], set()
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    for term in _FR_REVIEW_TERMS:
        for page in range(1, max_pages + 1):
            # Unquoted, for the same reason as the NAGPRA query above; _is_remains()
            # does the narrowing.
            parts = [("conditions[term]", term),
                     ("conditions[type][]", "NOTICE"),
                     ("conditions[publication_date][gte]", since),
                     ("per_page", per_page), ("page", page), ("order", "newest")]
            for f in ("title", "abstract", "agencies", "publication_date",
                      "html_url", "comments_close_on", "document_number"):
                parts.append(("fields[]", f))
            url = ("https://www.federalregister.gov/api/v1/documents.json?"
                   + urllib.parse.urlencode(parts))
            try:
                data = _get_json(url)
            except Exception as e:
                if page == 1:
                    print("  us_burial_reviews %s failed: %s" % (term, e))
                break
            rows = data.get("results") or []
            if not rows:
                break
            for d in rows:
                dn = d.get("document_number")
                if dn in seen:
                    continue
                title = (d.get("title") or "").strip()
                # NAGPRA notices are handled by their own fetcher; don't duplicate.
                if title.lower().startswith("notice of inventory completion") or \
                   title.lower().startswith("notice of intent to repatriate") or \
                   title.lower().startswith("notice of intended disposition"):
                    continue
                body = " ".join(filter(None, [title, d.get("abstract")]))
                if not _is_remains(body):
                    continue
                st = _detect_state(body)
                if not st:
                    continue                       # cannot place it honestly
                seen.add(dn)
                agency = ""
                ags = d.get("agencies") or []
                if ags and isinstance(ags[0], dict):
                    agency = ags[0].get("name") or ""
                rec = {"name": title[:150], "kind": "review", "posture": "watch",
                       "trigger": _trigger_from_text(body),
                       "country": "United States", "region": st,
                       "count": _mni(body), "held_by": "", "actor": agency[:120],
                       "status": "In federal review (comment window may be open)",
                       "url": d.get("html_url"),
                       "date": _iso_date(d.get("publication_date")),
                       "deadline": _iso_date(d.get("comments_close_on")),
                       "source": "us_burial_reviews"}
                lat, lng = STATE_CENTROID[st]
                _place(rec, lat, lng, GEO_ADMIN)
                rec["desc"] = ("Federal notice naming burials or human remains in "
                               "the project area. " + GEO_NOTE[GEO_ADMIN])[:200]
                rec["impact"] = rate_remains(rec)
                out.append(rec)
            if len(rows) < per_page:
                break
            time.sleep(0.3)
    return out


# ---------------------------------------------------------------------------
# SOURCE 3 -- NSW Aboriginal Heritage Impact Permits  [VERIFIED 2026-07]
# ---------------------------------------------------------------------------
# The single cleanest "permit to disturb burials" feed anywhere: every AHIP is a
# legal authorisation under s.90 of the NSW National Parks and Wildlife Act 1974
# to harm Aboriginal objects or a declared Aboriginal Place -- a category that
# includes burials. Published as polygon boundaries.
#
#   Dataset : Aboriginal Heritage Impact Permit Boundaries
#   Endpoint: mapprod3.environment.nsw.gov.au/arcgis/rest/services/EDP/AHIPS/MapServer
#   Licence : Creative Commons Attribution (CC-BY), NSW DCCEEW
#   Updated : quarterly; coverage 2010-01-04 -> 2026-06-30 as of 2026-07-01
#   Note    : DCCEEW states some AHIPs are absent from the layer due to data
#             quality -- the AHIP Public Register is authoritative. Said in desc.
#
# Same ArcGIS host that harvest_projects.py already queries for NSW_MAJOR, so the
# transport is proven in production.
NSW_AHIP = ("https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/"
            "EDP/AHIPS/MapServer")
_NSW_DEAD = ("surrender", "revoke", "revoked", "expired", "lapsed", "cancelled")


def fetch_nsw_ahip(layer=0, max_pages=30):
    out = []
    feats = _arcgis_query_all(NSW_AHIP, layer=layer, page=1000,
                              max_pages=max_pages, label="nsw_ahip")
    shown = False
    for f in feats:
        pr = f.get("properties") or {}
        if not shown and pr:
            print("  nsw_ahip fields: %s" % ", ".join(list(pr.keys())[:18]))
            shown = True
        c = _geom_center(f.get("geometry") or {})
        if not c:
            continue
        permit = str(_first(pr, "AHIPNumber", "AHIP_NUMBER", "PermitNumber",
                            "AHIP_No", "PERMITNO", "AHIPNo") or "").strip()
        status = str(_first(pr, "Status", "STATUS", "PermitStatus",
                            "AHIPStatus") or "").strip()
        holder = str(_first(pr, "Applicant", "APPLICANT", "PermitHolder",
                            "Holder", "ProponentName") or "").strip()
        purpose = str(_first(pr, "Purpose", "PURPOSE", "ActivityType",
                             "Activity", "Description") or "").strip()
        issued = (_iso_date(_first(pr, "DateIssued", "IssueDate", "DATE_ISSUED"))
                  or _epoch_ms(_first(pr, "DateIssued", "IssueDate", "DATE_ISSUED")))
        expiry = (_iso_date(_first(pr, "DateExpiry", "ExpiryDate", "DATE_EXPIRY"))
                  or _epoch_ms(_first(pr, "DateExpiry", "ExpiryDate", "DATE_EXPIRY")))
        # Fail-safe status gate, mirroring the projects harvester: a surrendered or
        # revoked permit authorises nothing, so it is not a live threat.
        if any(d in status.lower() for d in _NSW_DEAD):
            continue
        nm = ("AHIP " + permit) if permit else "Aboriginal Heritage Impact Permit"
        if purpose:
            nm += " \u2014 " + purpose[:70]
        rec = {"name": nm[:150], "kind": "harm-permit", "posture": "harm",
               "trigger": _trigger_from_text(purpose) if purpose else "development",
               "country": "Australia", "region": "New South Wales",
               "count": None, "held_by": "", "actor": holder[:120],
               "status": status or "Issued",
               "url": ("https://www.environment.nsw.gov.au/topics/heritage/"
                       "permits-agreements-aboriginal-places-objects"),
               "date": issued, "deadline": expiry, "source": "nsw_ahip"}
        _place(rec, c[0], c[1], GEO_AREA)
        rec["desc"] = ("Permit to harm Aboriginal objects or places under s.90 "
                       "NPW Act 1974. Source: NSW DCCEEW (CC-BY); the AHIP Public "
                       "Register is authoritative.")[:200]
        rec["impact"] = rate_remains(rec)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# SOURCE 4 -- California CEQA filings naming burials  [VERIFIED path]
# ---------------------------------------------------------------------------
# CEQAnet is the state clearinghouse; harvest_projects.py pulls the same CSV in
# production. California is the one US state where tribal cultural resources get
# their own statutory consultation track (AB 52), so burial disclosure lands here
# earlier and more often than in federal notices.
CEQANET_CSV = "https://ceqanet.lci.ca.gov/Search/Recent?OutputFormat=CSV"
_CEQ_LATLNG_RE = re.compile(r"(-?\d{1,3}\.\d{3,})")


def _csv_rows(text):
    """CSV -> list of dicts, with the DictReader footgun defused.

    csv.DictReader files any fields BEYOND the header row under the key `None`
    (that is `restkey`, which defaults to None). CEQAnet's export has rows with
    unescaped commas in the description, so it trips this on nearly every fetch.

    A None key is harmless to read but poisonous to format. On the 2026-07-30 run
    the line `", ".join(rows[0].keys())` -- a DIAGNOSTIC PRINT, nothing more --
    raised TypeError and _run() swallowed the whole source: zero records from a
    feed that was working, because of a log line.

    So name restkey explicitly and fold the overflow into a real string field,
    leaving no None key lying around for the next formatter to trip over."""
    import csv, io
    rdr = csv.DictReader(io.StringIO(text), restkey="_overflow")
    rows = []
    for r in rdr:
        ov = r.pop("_overflow", None)
        if ov:
            r["_overflow"] = " ".join(str(x) for x in ov if x is not None)
        rows.append({(k if k is not None else "_unnamed"): v for k, v in r.items()})
    return rows


def fetch_ceqanet_burials():
    out = []
    try:
        txt = _get_text(CEQANET_CSV)
    except Exception as e:
        print("  ceqanet_burials failed: %s" % e)
        return out
    try:
        rows = _csv_rows(txt)
    except Exception as e:
        print("  ceqanet_burials unparseable: %s" % e)
        return out
    if rows:
        # str() and the None filter are deliberate -- see _csv_rows.
        print("  ceqanet fields: %s"
              % ", ".join(str(k) for k in list(rows[0].keys())[:16] if k is not None))
    for r in rows:
        blob = " ".join(str(v) for v in r.values() if v)
        if not _is_remains(blob):
            continue
        sch = str(_first(r, "SCH Number", "SCHNumber", "SCH") or "").strip()
        title = str(_first(r, "Title", "Project Title", "ProjectTitle")
                    or "CEQA filing").strip()
        county = str(_first(r, "County", "Counties", "Lead County") or "").strip()
        lead = str(_first(r, "Lead Agency", "LeadAgency", "Agency") or "").strip()
        date = _iso_date(_first(r, "Received", "Date Received", "Posted", "Date"))
        latlng, geo = None, GEO_ADMIN
        la = _first(r, "Latitude", "Lat")
        lo = _first(r, "Longitude", "Long", "Lng")
        if la and lo:
            try:
                latlng = (float(la), float(lo)); geo = GEO_AREA
            except (TypeError, ValueError):
                latlng = None
        if latlng is None and county:
            latlng = _geocode("%s County, California" % county, "us")
            geo = GEO_ADMIN
        if latlng is None:
            latlng = STATE_CENTROID["California"]; geo = GEO_ADMIN
        rec = {"name": title[:150], "kind": "review", "posture": "watch",
               "trigger": _trigger_from_text(blob),
               "country": "United States", "region": "California",
               "count": _mni(blob), "held_by": "", "actor": lead[:120],
               "status": "In CEQA review",
               "url": ("https://ceqanet.lci.ca.gov/" + sch) if sch
                      else "https://ceqanet.lci.ca.gov/",
               "date": date, "deadline": "", "source": "ceqanet_burials"}
        _place(rec, latlng[0], latlng[1], geo)
        rec["desc"] = ("California CEQA filing naming burials or human remains"
                       + (" in " + county + " County. " if county else ". ")
                       + GEO_NOTE[rec["geo"]])[:200]
        rec["impact"] = rate_remains(rec)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# SOURCE 5 -- UK planning applications touching burial ground  [VERIFIED path]
# ---------------------------------------------------------------------------
# PlanIt aggregates UK local-authority planning applications and exposes a GeoJSON
# API that harvest_projects.py already uses. Burial-ground disturbance in England
# and Wales runs through planning plus a Ministry of Justice licence; the planning
# side is the open half. (The MoJ s.25 licence list is not machine-readable -- see
# the PENDING roster at the bottom of this file.)
def fetch_uk_burial_planning(days=365, pg_sz=200, max_pages=8):
    out = []
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    terms = ["burial ground", "human remains", "exhumation", "graveyard", "churchyard"]
    for term in terms:
        for pg in range(1, max_pages + 1):
            params = {"search": term, "start_date": since, "pg_sz": pg_sz,
                      "page": pg, "compress": "on"}
            url = "https://www.planit.org.uk/api/applics/geojson?" + \
                  urllib.parse.urlencode(params)
            try:
                gj = _get_json(url)
            except Exception as e:
                if pg == 1:
                    print("  uk_burial_planning %s failed: %s" % (term, e))
                break
            feats = (gj or {}).get("features") or []
            if not feats:
                break
            for f in feats:
                pr = f.get("properties") or {}
                blob = " ".join(str(v) for v in pr.values() if v)
                if not _is_remains(blob):
                    continue
                c = _geom_center(f.get("geometry") or {})
                if not c:
                    continue
                desc = str(_first(pr, "description", "proposal", "name") or
                           "Planning application").strip()
                auth = str(_first(pr, "area_name", "authority", "council") or "").strip()
                rec = {"name": desc[:150], "kind": "exhumation", "posture": "harm",
                       "trigger": _trigger_from_text(blob),
                       "country": "United Kingdom", "region": auth,
                       "count": _mni(blob), "held_by": "",
                       "actor": str(_first(pr, "applicant", "agent") or "")[:120],
                       "status": str(_first(pr, "app_state", "status") or
                                     "Application lodged"),
                       "url": _first(pr, "link", "url") or "https://www.planit.org.uk/",
                       "date": _iso_date(_first(pr, "start_date", "date_received",
                                                "last_changed")),
                       "deadline": _iso_date(_first(pr, "consultation_end_date")),
                       "source": "uk_burial_planning"}
                _place(rec, c[0], c[1], GEO_AREA)   # gate coarsens: exhumation
                rec["desc"] = ("UK planning application affecting a burial ground. "
                               "A Ministry of Justice licence is also required to "
                               "remove remains. " + GEO_NOTE[rec["geo"]])[:200]
                rec["impact"] = rate_remains(rec)
                out.append(rec)
            if len(feats) < pg_sz:
                break
            time.sleep(0.4)
    return out


# ---------------------------------------------------------------------------
# SOURCE 6 -- burial grounds OpenStreetMap records as removed  [VERIFIED path]
# ---------------------------------------------------------------------------
# NOT an events feed, and labelled as such on the map. OSM's lifecycle prefixes
# (was:, demolished:, removed:, abandoned:) are how mappers record that a burial
# ground that used to be here is gone. That is a real, checkable statement about
# ground that held the dead, contributed by community mappers worldwide -- the
# same provenance class as the OSM construction layer on the projects map.
_OVERPASS = ["https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter"]
_OSM_REMOVED_TAGS = [
    ('was:landuse', 'cemetery'), ('demolished:landuse', 'cemetery'),
    ('removed:landuse', 'cemetery'), ('abandoned:landuse', 'cemetery'),
    ('was:amenity', 'grave_yard'), ('demolished:amenity', 'grave_yard'),
    ('removed:amenity', 'grave_yard'), ('abandoned:amenity', 'grave_yard'),
]


def _overpass(q, label="", timeout=180):
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for ep in _OVERPASS:
        try:
            req = urllib.request.Request(ep, data=body,
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            time.sleep(3.0)
    print("  overpass %s failed: %s" % (label, last))
    return None


def fetch_osm_removed_burial_grounds(cap=6000):
    out = []
    clauses = "".join('nwr["%s"="%s"];' % (k, v) for k, v in _OSM_REMOVED_TAGS)
    q = ("[out:json][timeout:170];(" + clauses + ");out center tags %d;" % cap)
    data = _overpass(q, "removed burial grounds")
    if not data:
        return out
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lng = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lng is None:
            continue
        nm = tags.get("name") or tags.get("old_name") or "Unnamed burial ground"
        life = next((k for k, _ in _OSM_REMOVED_TAGS if k in tags), "was:landuse")
        rec = {"name": str(nm)[:150], "kind": "removed-ground", "posture": "harm",
               "trigger": "development", "country": "", "region": "",
               "count": None, "held_by": "", "actor": "",
               "status": "Recorded in OpenStreetMap as " + life.split(":")[0],
               "url": "https://www.openstreetmap.org/%s/%s" % (el.get("type"), el.get("id")),
               "date": "", "deadline": "", "source": "osm_removed_burial_grounds"}
        _place(rec, lat, lng, GEO_AREA)             # gate coarsens
        # Say plainly that this ALREADY HAPPENED and that the date is unknown.
        # "Burial ground removed" read as though a removal were under way now;
        # "Former burial ground" read as though it were ancient history. It is
        # neither: OSM records that the ground is gone, and says nothing about when
        # or about what happened to the people in it.
        rec["desc"] = ("Already gone. OpenStreetMap contributors record that a burial "
                       "ground was here and no longer is. WHEN it went, and what "
                       "happened to the people in it, is not stated by this source -- "
                       "it could be last year or two centuries ago. Community-recorded, "
                       "not an official register. " + GEO_NOTE[rec["geo"]])[:420]
        rec["impact"] = rate_remains(rec)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# SOURCE 7 -- open-data portal federations, remains vocabulary
# ---------------------------------------------------------------------------
# harvest_projects.py already carries live-verified registries of ~2,200 government
# portals across seven protocols, and the machinery to search them in ~50 languages.
# Duplicating those lists here would guarantee they drift apart, so this harvester
# IMPORTS them from its sibling in the same repo and only swaps the vocabulary.
# If harvest_projects.py is absent the source skips cleanly and says so.
_REMAINS_TERMS = [
    "cemetery", "burial ground", "human remains", "graveyard", "exhumation",
    "archaeological excavation", "burial site", "necropolis",
    "cementerio", "fosas comunes", "sepulturas", "excavacion arqueologica",
    "cimeti\u00e8re", "s\u00e9pultures", "fouille arch\u00e9ologique",
    "friedhof", "gr\u00e4berfeld", "arch\u00e4ologische ausgrabung",
    "begraafplaats", "opgraving", "cimitero", "scavo archeologico",
    "cemit\u00e9rio", "escava\u00e7\u00e3o arqueol\u00f3gica",
    "cmentarz", "wykopaliska", "\u043a\u043b\u0430\u0434\u0431\u0438\u0449\u0435",
    "gravplats", "kirkeg\u00e5rd", "hautausmaa", "mezarl\u0131k",
    "\u0645\u0642\u0628\u0631\u0629", "\u58d3\u5834", "\u5893\u5730",
    "pemakaman", "makam", "\u0936\u094d\u092e\u0936\u093e\u0928",
]


def _fed_registries():
    """Portal registries live in the SIBLING repo (WelcomeToYourGalaxy/local-map),
    inside harvest_projects.py. This repo is standalone, so there is nothing to
    import -- we fetch that file's raw text once and lift the list literals out of
    it with ast.literal_eval.

    Why not vendor the lists here: they are live-verified in the projects repo and
    change as portals come and go. A copy would drift silently, and a drifted
    portal list is exactly the kind of quiet inaccuracy this project refuses.

    Why literal_eval and not exec/import: the fetched text is never executed. Only
    the bracketed literal following each registry name is parsed, and
    ast.literal_eval evaluates nothing but plain data. A tampered sibling file
    cannot run code here.
    """
    if _REG_CACHE["done"]:
        return _REG_CACHE["reg"]
    _REG_CACHE["done"] = True
    url = os.environ.get("SIBLING_HARVESTER_URL", SIBLING_HARVESTER_URL)
    try:
        src = _get_text(url, limit=8000000)
    except Exception as e:
        print("  federations: could not fetch sibling registries (%s) -- skip" % e)
        return None
    import ast
    reg = {}
    for name, proto in (("_CKAN_PORTALS", "ckan"), ("_ODS_PORTALS", "ods"),
                        ("_GEONODE_PORTALS", "geonode"), ("_DKAN_PORTALS", "dkan"),
                        ("_UDATA_PORTALS", "udata")):
        i = src.find(name)
        if i < 0:
            continue
        j = src.find("[", i)
        if j < 0:
            continue
        depth, k = 0, j
        while k < len(src):
            if src[k] == "[":
                depth += 1
            elif src[k] == "]":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        try:
            val = ast.literal_eval(src[j:k + 1])
        except Exception as e:
            _flag("federations: %s did not parse as a literal (%s)" % (name, e))
            continue
        if val:
            reg[proto] = val
            print("  federations: %s -> %d portals" % (proto, len(val)))
    if not reg:
        print("  federations: sibling file exposed no registries -- skip")
        return None
    _REG_CACHE["reg"] = reg
    return reg


def _ckan_datasets(base, term, rows=50):
    url = base.rstrip("/") + "/api/3/action/package_search?" + \
        urllib.parse.urlencode({"q": term, "rows": rows})
    try:
        d = _get_json(url)
    except Exception:
        return []
    return ((d or {}).get("result") or {}).get("results") or []


def _ods_datasets(base, term, rows=30):
    """OpenDataSoft v2 catalog search."""
    url = base.rstrip("/") + "/api/v2/catalog/datasets?" + urllib.parse.urlencode(
        {"where": 'search(*,"%s")' % term.replace('"', ""), "limit": rows})
    try:
        d = _get_json(url)
    except Exception:
        return []
    out = []
    for ds in ((d or {}).get("datasets") or []):
        rec = ds.get("dataset") or ds
        did = rec.get("dataset_id") or ""
        meta = ((rec.get("metas") or {}).get("default") or {})
        if not did:
            continue
        out.append({"title": meta.get("title") or did, "notes": meta.get("description") or "",
                    "geojson": base.rstrip("/") + "/api/v2/catalog/datasets/%s/exports/geojson?limit=-1" % did,
                    "page": base.rstrip("/") + "/explore/dataset/%s/" % did})
    return out


def _geonode_datasets(base, term, rows=30):
    """GeoNode v2 resources search; GeoNode exposes WFS per layer."""
    url = base.rstrip("/") + "/api/v2/resources?" + urllib.parse.urlencode(
        {"search": term, "page_size": rows})
    try:
        d = _get_json(url)
    except Exception:
        return []
    out = []
    for r in ((d or {}).get("resources") or []):
        alt = r.get("alternate") or r.get("name") or ""
        if not alt:
            continue
        out.append({"title": r.get("title") or alt, "notes": r.get("abstract") or "",
                    "geojson": base.rstrip("/") + "/geoserver/wfs?" + urllib.parse.urlencode(
                        {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                         "typeNames": alt, "outputFormat": "application/json",
                         "count": 600, "srsName": "EPSG:4326"}),
                    "page": r.get("detail_url") or base})
    return out


def _dkan_datasets(base, term, rows=30):
    """DKAN speaks the CKAN package_search API."""
    return [{"title": str(ds.get("title") or ds.get("name") or ""),
             "notes": str(ds.get("notes") or ""),
             "resources": ds.get("resources") or [],
             "page": ds.get("url") or base}
            for ds in _ckan_datasets(base, term, rows=rows)]


# GeoJSON is *supposed* to be WGS84 lon/lat, and most portals comply. Some publish
# the file exactly as it left their GIS, in Web Mercator metres (EPSG:3857).
# Bogota's cemetery layer is one: its points arrive as (511404, -8250830), which is
# not a coordinate anywhere on earth. All 64 Colombian records were written with
# those values, so the country reported a record count while showing nothing on the
# map -- the worst kind of failure, because it looks like coverage.
#
# Detected by magnitude: no latitude exceeds 90 and no longitude exceeds 180, so
# anything larger is metres. Converted back if the result is plausible, DROPPED if
# it is not -- a wrongly reprojected grave is worse than a missing one.
_MERC_R = 6378137.0


def _unproject(x, y):
    """Web Mercator metres -> (lat, lng), or None if the result is implausible."""
    try:
        lng = (x / _MERC_R) * 180.0 / math.pi
        lat = (2.0 * math.atan(math.exp(y / _MERC_R)) - math.pi / 2.0) * 180.0 / math.pi
        if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
            return lat, lng
    except Exception:
        pass
    return None


def _fix_coords(lat, lng):
    """Return usable (lat,lng), reprojecting if the numbers are clearly metres."""
    try:
        lat, lng = float(lat), float(lng)
    except Exception:
        return None
    if abs(lat) <= 90.0 and abs(lng) <= 180.0:
        return lat, lng
    # _geom_center yields (lat, lng); in projected data that is (northing, easting).
    fixed = _unproject(lng, lat)
    if fixed:
        _PROJECTED[0] += 1
        return fixed
    return None


_PROJECTED = [0]


def _geojson_points(url, per=600):
    """Read a GeoJSON resource and return (lat,lng,props) tuples."""
    try:
        gj = _get_json(url)
    except Exception:
        return []
    feats = (gj or {}).get("features") if isinstance(gj, dict) else None
    if not feats:
        return []
    got = []
    for f in feats[:per]:
        c = _geom_center(f.get("geometry") or {})
        if not c:
            continue
        fixed = _fix_coords(c[0], c[1])
        if fixed:
            got.append((fixed[0], fixed[1], f.get("properties") or {}))
    return got


def _fed_record(lat, lng, props, ds_title, page_url, country, source):
    blob = " ".join(str(v) for v in props.values() if v)
    nm = (_first(props, "name", "NAME", "nombre", "nom", "naam", "title", "TITLE")
          or ds_title)
    # A cemetery dataset is a facility register, not an unearthing. Classify so the
    # placement gate treats it correctly: anything that reads as an active burial
    # place stays a `holding`-class locator; anything that reads as a dig is an
    # excavation and gets blurred.
    kind = "excavation" if re.search(
        r"excavat|dig\b|fouille|ausgrabung|opgraving|escava|scavo|wykopalisk|"
        r"exhum|fosa|mass\sgrave", (ds_title + " " + blob), re.I) else "removed-ground"
    rec = {"name": str(nm)[:150], "kind": kind, "posture": KINDS[kind][1],
           "trigger": _trigger_from_text(blob + " " + ds_title),
           "country": country, "region": "", "count": _mni(blob),
           "held_by": "", "actor": "",
           "status": "Published in a government open-data portal",
           "url": page_url, "date": "", "deadline": "", "source": source}
    _place(rec, lat, lng, GEO_AREA)                 # gate coarsens both kinds
    rec["desc"] = (("From \u201c%s\u201d. " % ds_title[:70]) + GEO_NOTE[rec["geo"]])[:200]
    rec["impact"] = rate_remains(rec)
    return rec


def _fed_shard(portals):
    k = os.environ.get("FED_SHARD")
    if k in (None, ""):
        return portals
    n = int(os.environ.get("FED_SHARDS", "6"))
    return portals[int(k)::n]


def _fed_budget():
    return time.time() + int(os.environ.get("FED_REMAINS_MAX_MIN", "90")) * 60


# ---------------------------------------------------------------------------
# FEDERATION CONCURRENCY
# ---------------------------------------------------------------------------
# The 2026-08-01 run hit the 200-minute timeout on all six shards. The arithmetic
# says why: 1,236 portals over 6 shards is ~205 portals each, and every portal is
# searched for up to 12 vocabulary terms -- roughly 2,460 HTTP requests per shard
# BEFORE a single GeoJSON is downloaded. At five seconds a request that is 3.4
# hours of searching alone, which is almost exactly how long it ran.
#
# Portals are independent and the work is pure waiting, so it parallelises cleanly
# -- the same argument as the daily sources. FED_WORKERS is deliberately modest,
# and note what it does here: eight in flight means eight DIFFERENT government
# servers, not eight requests at one of them.
FED_WORKERS = max(1, int(os.environ.get("FED_WORKERS", "8")))


def _fed_safe(fn, p):
    """One portal, never allowed to raise.

    A federation sweep touches hundreds of independent servers and some fraction
    will always be down, slow, or serving broken JSON. One bad portal must never
    end the sweep."""
    try:
        return fn(p) or []
    except Exception:
        return []


def _fed_map(portals, fn, label):
    """Run fn over portals concurrently and collect what they return."""
    rows = []
    if FED_WORKERS <= 1:
        for p in portals:
            rows += _fed_safe(fn, p)
        return rows
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=FED_WORKERS) as ex:
        for got in ex.map(lambda p: _fed_safe(fn, p), portals):
            rows += got or []
    return rows


def _portal_url_country(p):
    """Unpack one registry entry into (url, country).

    THE SIBLING REGISTRY STORES TUPLES, NOT DICTS. Entries look like
    ('https://datos.gob.cl', 'Chile', 'cl') -- url, country name, ISO2. This
    function originally handled only str and dict, so every portal raised
    "'tuple' object has no attribute 'get'" and all three federation fetchers
    failed on their first portal. The 2026-07-31 run produced 0 rows from 1,236
    portals for exactly that reason, and it looked like a successful run because
    the shards exited 0 after writing an empty part file.

    Handles every shape the registry might use, and returns ("", "") for anything
    unrecognised rather than raising -- one odd entry must not kill the sweep.

    Some entries carry a bare host with no scheme ('data.ajman.ae'); those get
    https:// prefixed, or the fetchers would build invalid URLs."""
    url = country = ""
    if isinstance(p, str):
        url = p
    elif isinstance(p, (list, tuple)):
        if len(p) >= 1 and isinstance(p[0], str):
            url = p[0]
        if len(p) >= 2 and isinstance(p[1], str):
            country = p[1]
    elif isinstance(p, dict):
        url = p.get("url") or p.get("base") or p.get("host") or ""
        country = p.get("country") or p.get("cc") or p.get("name") or ""
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url, (country or "").strip()


def fetch_ckan_remains(per_ds=400):
    """CKAN + DKAN portals -> burial / cemetery / excavation datasets with GeoJSON."""
    reg = _fed_registries()
    if not reg:
        return []
    end = _fed_budget()

    def one_portal(p):
        # The deadline is checked INSIDE the worker, not only between portals:
        # with a pool in flight, a per-loop check would let every already-queued
        # portal start after the budget had passed. It is checked again between
        # terms, because twelve searches per portal are not free either.
        got = []
        if time.time() > end:
            return got
        base, country = _portal_url_country(p)
        if not base:
            return got
        for term in _REMAINS_TERMS[:12]:
            if time.time() > end:
                break
            for ds in _ckan_datasets(base, term, rows=20):
                title = str(ds.get("title") or ds.get("name") or "")
                blob = title + " " + str(ds.get("notes") or "")
                if _is_site_register(blob):
                    _REFUSED[0] += 1
                    continue                        # policy refusal, not a miss
                if not _is_remains(blob):
                    continue
                for res in (ds.get("resources") or []):
                    if "geojson" not in str(res.get("format") or "").lower():
                        continue
                    ru = res.get("url") or ""
                    if not ru.startswith("http"):
                        continue
                    for lat, lng, pr in _geojson_points(ru, per=per_ds):
                        got.append(_fed_record(lat, lng, pr, title,
                                               ds.get("url") or base, country,
                                               "ckan_remains"))
                    break
        time.sleep(0.2)
        return got

    portals = []
    for proto in ("ckan", "dkan"):
        portals += _fed_shard(list(reg.get(proto) or []))
    out = _fed_map(portals, one_portal, "ckan_remains")
    if time.time() > end:
        _flag("ckan_remains: budget passed")
    return out


def fetch_ods_remains(per_ds=400):
    """OpenDataSoft portals -- French, Swiss, Dutch and municipal catalogues carry
    the densest cemetery and burial-register coverage in Europe."""
    reg = _fed_registries()
    if not reg or "ods" not in reg:
        return []
    end = _fed_budget()

    def one_portal(p):
        got = []
        if time.time() > end:
            return got
        base, country = _portal_url_country(p)
        if not base:
            return got
        for term in _REMAINS_TERMS[:10]:
            if time.time() > end:
                break
            for ds in _ods_datasets(base, term, rows=20):
                if _is_site_register(ds["title"] + " " + ds["notes"]):
                    _REFUSED[0] += 1
                    continue                        # policy refusal, not a miss
                if not _is_remains(ds["title"] + " " + ds["notes"]):
                    continue
                for lat, lng, pr in _geojson_points(ds["geojson"], per=per_ds):
                    got.append(_fed_record(lat, lng, pr, ds["title"], ds["page"],
                                           country, "ods_remains"))
        time.sleep(0.2)
        return got

    out = _fed_map(_fed_shard(list(reg["ods"])), one_portal, "ods_remains")
    if time.time() > end:
        _flag("ods_remains: budget passed")
    return out


def fetch_geonode_remains(per_ds=400):
    """GeoNode portals -- the main route into African, Latin American and Asian
    national spatial-data infrastructures."""
    reg = _fed_registries()
    if not reg or "geonode" not in reg:
        return []
    end = _fed_budget()

    def one_portal(p):
        got = []
        if time.time() > end:
            return got
        base, country = _portal_url_country(p)
        if not base:
            return got
        for term in _REMAINS_TERMS[:8]:
            if time.time() > end:
                break
            for ds in _geonode_datasets(base, term, rows=15):
                if _is_site_register(ds["title"] + " " + ds["notes"]):
                    _REFUSED[0] += 1
                    continue                        # policy refusal, not a miss
                if not _is_remains(ds["title"] + " " + ds["notes"]):
                    continue
                for lat, lng, pr in _geojson_points(ds["geojson"], per=per_ds):
                    got.append(_fed_record(lat, lng, pr, ds["title"], ds["page"],
                                           country, "geonode_remains"))
        time.sleep(0.2)
        return got

    out = _fed_map(_fed_shard(list(reg["geonode"])), one_portal, "geonode_remains")
    if time.time() > end:
        _flag("geonode_remains: budget passed")
    return out


# ---------------------------------------------------------------------------
# PENDING SOURCES  --  built or scoped, NOT shipped until live-verified
# ---------------------------------------------------------------------------
# Same discipline as harvest_projects.py: a source that cannot be verified end to
# end returns [] and says why, rather than shipping a guessed endpoint. Each of
# these is a real register that exists; what is missing is a machine-readable,
# licence-clear route to it. Do not enable one without confirming its URL, its
# field names and its terms.
def fetch_wa_section18():
    """Western Australia s.18 consents to disturb Aboriginal sites (DPLH).

    LICENCE CHECKED 2026-07-31 -- REFUSED, for two independent reasons.

    1. NOT OPEN DATA. The DPLH spatial layers on data.wa.gov.au (Aboriginal
       Cultural Heritage Register DPLH-099, Historic DPLH-098, and the retired
       DPLH-001) sit behind a licence agreement requiring active acceptance, and
       download requires a subscription. They are free to VIEW through AHIS/ACHIS
       or Locate, which is not the same as free to redistribute. That is the same
       ground on which harvest_projects.py rejected a DWER layer.

    2. IT IS A SITE REGISTER, which this harvester refuses on its own terms --
       see _is_site_register(). DPLH itself states that the exact location and
       extent of some places are withheld and replaced with a shaded region of at
       least 4 km-squared "to preserve confidentiality". The custodian has already
       decided these locations should not be published precisely; republishing
       them, or even mirroring the buffered version, overrides a judgement that
       was not ours to make.

    Note the distinction that keeps NSW in and WA out: NSW AHIP records are
    PERMITS -- an accountable actor, a decision, a date -- published as open data
    under CC-BY. WA's equivalent is a register of PLACES. A permit register names
    who authorised harm; a site register names where the dead are. This map
    carries the first and refuses the second.

    If DPLH publishes s.18 CONSENT DECISIONS as an open list -- applicant, date,
    outcome, without site geometry -- that would be squarely in scope and should
    be added. It is the decisions that belong here, never the places."""
    return []


def fetch_qld_chmp():
    """Queensland Cultural Heritage Management Plans register (DSDSATSIP).
    Register is published; no machine-readable endpoint located yet."""
    return []


def fetch_spain_fosas():
    """Spain's state Mapa integrado de localización de personas desaparecidas
    (Ministerio de Política Territorial y Memoria Democrática) -- 2,000+ graves of
    the Civil War and dictatorship, classified exhumada / no intervenida /
    desaparecida / trasladada al Valle de los Caídos.

    VERIFIED 2026-07 as NOT machine-readable: the live search tool is
    georeferenced, but the downloadable open-data release is stale (2017) and
    carries no coordinates at all. There is no endpoint to wire.

    Worth recording, because it settles a question this harvester had left open:
    Ley 20/2022 art.17 makes the maps public AND requires that the zones they
    contain receive "preservación especial". Spain therefore treats these
    locations as needing protection even while publishing them. That is an
    argument FOR the coarsening gate, not against it -- so if this source is ever
    wired, `mass-grave` should keep its blur."""
    return []


def fetch_ireland_excavation():
    """Ireland: National Monuments Service excavation licences + excavations.ie
    reports. Both public; neither confirmed as an API."""
    return []


def fetch_eamena():
    """EAMENA -- Endangered Archaeology in the Middle East and North Africa.
    Records disturbance and looting of archaeological sites including burials,
    with geometry. Access is account-gated; reuse terms need checking."""
    return []


def fetch_colombia_ubpd():
    """Colombia UBPD (Unidad de Búsqueda de Personas dadas por Desaparecidas) --
    prospecting and recovery of the disappeared. datos.gov.co is Socrata, so the
    transport is already proven; the specific dataset ids are not verified."""
    return []


def fetch_mexico_cnb():
    """Mexico: Comisión Nacional de Búsqueda clandestine-grave registry.
    Publication is intermittent and aggregate; no stable endpoint verified."""
    return []


# ---------------------------------------------------------------------------
# NPS National NAGPRA public grids -- DISCOVERY fetcher
# ---------------------------------------------------------------------------
# apps.cr.nps.gov/nagprapublic serves its Inventories and Unclaimed Lists as
# DataTables grids with export buttons, so a JSON endpoint exists behind them. It is
# not documented and was not visible in the rendered page from the build sandbox.
#
# Rather than leave a dead stub or guess a URL into the source, this PROBES at run
# time -- in Actions, which has the open-web access the sandbox lacks. It reads the
# grid page, pulls any ajax/url target out of the markup, adds a short list of
# conventional ASP.NET MVC candidates, and tries each one. A candidate counts as
# found only if it returns JSON whose rows carry recognisable columns. Whatever
# happens is printed, so run 1 either wires the source or tells you exactly what to
# put in _NPS_EXTRA.
#
# This matters because these two tables carry COUNTS OF INDIVIDUALS STILL HELD per
# institution, which nothing else on this map does -- the Federal Register only shows
# remains once an institution has decided to move them. An institution that has never
# filed a notice is invisible everywhere except here.
#
# Records land as kind="holding" (posture "watch"), plotted at the institution. That
# is a category the taxonomy has always defined and no source has filled.
_NPS_BASE = "https://apps.cr.nps.gov/nagprapublic"
_NPS_GRIDS = [("Inventory", "inventories"), ("UnclaimedList", "unclaimed lists")]
# Add a known-good path here (from the browser network tab) to skip discovery.
_NPS_EXTRA = [p for p in os.environ.get("NPS_GRID_PATHS", "").split(",") if p.strip()]
_NPS_AJAX_RE = re.compile(
    r"""["']((?:/|https?://)[^"'<>\s]*?(?:nagprapublic|Home)/[A-Za-z0-9_]*"""
    r"""(?:Data|List|Grid|Json|Get|Load|Read|Table|Search)[A-Za-z0-9_]*)["']""", re.I)


def _nps_candidates(grid):
    """Paths worth trying for one grid, most-likely first. Discovery from the page
    markup comes first; the conventional guesses are only a fallback."""
    found = []
    try:
        html = _get_text(_NPS_BASE + "/Home/" + grid, limit=3000000)
        for m in _NPS_AJAX_RE.finditer(html):
            u = m.group(1)
            if u.startswith("/"):
                u = "https://apps.cr.nps.gov" + u
            if u not in found:
                found.append(u)
        if found:
            print("  nps_nagpra_grid: %s page advertises %d candidate(s)"
                  % (grid, len(found)))
    except Exception as e:
        print("  nps_nagpra_grid: could not read the %s page (%s)" % (grid, e))
    conventional = ["%s/Home/%s%s" % (_NPS_BASE, verb, grid)
                    for verb in ("Get", "Load", "Read", "Search")]
    conventional += ["%s/Home/%s%s" % (_NPS_BASE, grid, suf)
                     for suf in ("Data", "List", "Json", "Grid")]
    conventional += ["%s/api/%s" % (_NPS_BASE, grid)]
    return found + [c for c in conventional if c not in found]


def _nps_rows(payload):
    """DataTables payloads vary: a bare list, or {data:[...]}, or {aaData:[...]}."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "aaData", "Data", "rows", "results", "items"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []


_NPS_COLS = ("museum", "institution", "agency", "holder", "state", "individual",
             "remains", "count", "mni", "funerary", "name")


def _nps_looks_right(rows):
    if not rows or not isinstance(rows[0], dict):
        return False
    keys = " ".join(str(k).lower() for k in rows[0].keys())
    return sum(1 for c in _NPS_COLS if c in keys) >= 2


def _nps_probe(grid):
    for url in _NPS_EXTRA + _nps_candidates(grid):
        for sep in ("?", "&"):
            q = url + (sep if "?" not in url or sep == "&" else "?")
            q += urllib.parse.urlencode({"draw": 1, "start": 0, "length": 5000})
            try:
                rows = _nps_rows(_get_json(q))
            except Exception:
                continue
            if _nps_looks_right(rows):
                print("  nps_nagpra_grid: FOUND %s -> %s (%d rows, cols: %s)"
                      % (grid, q[:110], len(rows),
                         ", ".join(list(rows[0].keys())[:8])))
                return q, rows
            break                    # url reachable but wrong shape; try the next
    return None, []


def fetch_nps_nagpra_grid():
    out = []
    for grid, label in _NPS_GRIDS:
        url, rows = _nps_probe(grid)
        if not rows:
            _flag("nps_nagpra_grid: no endpoint found for %s -- open the network tab "
                  "on %s/Home/%s and set NPS_GRID_PATHS to what the grid calls"
                  % (label, _NPS_BASE, grid))
            continue
        shown = False
        for r in rows:
            if not shown:
                print("  nps %s fields: %s" % (grid, ", ".join(list(r.keys())[:14])))
                shown = True
            inst = str(_first(r, "Museum", "museum", "Institution", "institution",
                              "MuseumName", "Agency", "agency", "Holder",
                              "FederalAgency", "Name", "name") or "").strip()
            if not inst:
                continue
            st_raw = str(_first(r, "State", "state", "StateCode", "ST") or "").strip()
            region = _ST_ABBR.get(st_raw.upper(), st_raw if st_raw in STATE_CENTROID
                                  else "")
            n = None
            for k in ("Individuals", "individuals", "MNI", "NumberOfIndividuals",
                      "HumanRemains", "CountIndividuals", "Count", "Total"):
                v = r.get(k)
                if v not in (None, "", "-"):
                    try:
                        n = int(float(str(v).replace(",", "")))
                        break
                    except (TypeError, ValueError):
                        pass
            rec = {"name": ("%s \u2014 %s" % (inst, label))[:150],
                   "kind": "holding", "posture": KINDS["holding"][1],
                   "trigger": "law", "country": "United States", "region": region,
                   "count": n, "held_by": inst[:120], "actor": inst[:120],
                   "status": ("Reported to National NAGPRA; %s"
                              % ("pending consultation or notice" if grid == "Inventory"
                                 else "reported unclaimed")),
                   "url": "%s/Home/%s" % (_NPS_BASE, grid),
                   "date": "", "deadline": "", "source": "nps_nagpra_grid"}
            latlng, geo = None, GEO_ADMIN
            if region in STATE_CENTROID:
                latlng, geo = STATE_CENTROID[region], GEO_ADMIN
            if latlng is None:
                continue              # no state, no defensible placement
            _place(rec, latlng[0], latlng[1], geo)
            rec["desc"] = (("Reported as held by this institution and not yet "
                            "repatriated. ") + GEO_NOTE[rec["geo"]])[:200]
            rec["impact"] = rate_remains(rec)
            out.append(rec)
    return out


def fetch_uk_moj_licences():
    """England & Wales: Ministry of Justice licences for the removal of human
    remains (Burial Act 1857 s.25). Issued in volume, released only via FOI."""
    return []


def fetch_icmp():
    """ICMP -- International Commission on Missing Persons. Holds the fullest
    record of conflict-grave exhumations; publishes reports, not open data."""
    return []


# ---------------------------------------------------------------------------
# MERGE + WRITE
# ---------------------------------------------------------------------------
def dedup(items):
    seen, out = set(), []
    for p in items:
        key = (round(p["lat"], 3), round(p["lng"], 3),
               (p.get("name") or "").strip().lower()[:40])
        if key in seen:
            continue
        seen.add(key); out.append(p)
    return out


_SRC_COUNTS = {}
_RUN_FLAGS = []
_FAILED = set()
_REFUSED = [0]      # datasets refused on policy (site registers)


def _flag(msg):
    _RUN_FLAGS.append(msg)
    print("  [flag] " + msg)


def _run(name, fn):
    """Run one source in isolation so a single failure can't kill the harvest."""
    try:
        got = fn() or []
        _SRC_COUNTS[name] = _SRC_COUNTS.get(name, 0) + len(got)
        print("  %-28s %d" % (name + ":", len(got)))
        return got
    except Exception as e:
        _SRC_COUNTS.setdefault(name, 0)
        _FAILED.add(name)
        _RUN_FLAGS.append("%s FAILED: %s" % (name, e))
        print("  %-28s FAILED: %s" % (name + ":", e))
        # A crash is not a coverage gap, and the difference matters: the
        # 2026-07-31 federation run failed on its first portal in all three
        # fetchers, wrote an empty part file, and EXITED 0. GitHub showed a green
        # tick on a run that harvested nothing from 1,236 portals. Printing the
        # traceback here is what turns that into a five-minute diagnosis.
        traceback.print_exc()
        return []


def _run_all(sources, workers):
    """Run every source, concurrently where it is safe to.

    Order is preserved in the OUTPUT regardless of completion order, so a run is
    reproducible and two runs of the same data produce the same file. Only the
    waiting overlaps.

    Per-source output is buffered and printed in source order once everything is
    done -- interleaved log lines from six threads would be unreadable, and the
    per-source diagnostic is the main debugging tool this harvester has."""
    if workers <= 1:
        out = []
        for nm, fn in sources:
            out += _run(nm, fn)
        return out
    import concurrent.futures as _cf
    import threading

    # contextlib.redirect_stdout swaps sys.stdout PROCESS-WIDE, so with real
    # threads one source's redirect captures another's output and the logs come
    # out shuffled or missing. Replace sys.stdout ONCE with a router that keeps a
    # separate buffer per thread; each fetcher then writes only to its own.
    class _Router(object):
        def __init__(self, real):
            self.real = real
            self.local = threading.local()
        def write(self, text):
            buf = getattr(self.local, "buf", None)
            (buf.write(text) if buf is not None else self.real.write(text))
        def flush(self):
            self.real.flush()

    buffers = {}
    lock = threading.Lock()
    router = _Router(sys.stdout)

    def one(nm, fn):
        buf = io.StringIO()
        router.local.buf = buf
        try:
            got = _run(nm, fn)
        finally:
            router.local.buf = None
            with lock:
                buffers[nm] = buf.getvalue()
        return nm, got

    results = {}
    t0 = time.time()
    real_stdout = sys.stdout
    sys.stdout = router
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(one, nm, fn) for nm, fn in sources]
        for f in _cf.as_completed(futs):
            try:
                nm, got = f.result()
                results[nm] = got
            except Exception as e:
                _RUN_FLAGS.append("worker crashed: %s" % e)
                traceback.print_exc()
    sys.stdout = real_stdout
    for nm, _fn in sources:
        sys.stdout.write(buffers.get(nm, ""))
    print("  (%d sources on %d workers in %.0fs)"
          % (len(sources), workers, time.time() - t0))
    out = []
    for nm, _fn in sources:
        out += results.get(nm, [])
    return out


def _print_diagnostics():
    if not _SRC_COUNTS:
        return
    print("\n=== DIAGNOSTIC (per-source yields, pre-dedup) ===")
    for nm, n in sorted(_SRC_COUNTS.items(), key=lambda kv: -kv[1]):
        print("  %-30s %7d" % (nm, n))
    total = sum(_SRC_COUNTS.values())
    active = sum(1 for v in _SRC_COUNTS.values() if v > 0)
    print("  " + "-" * 40)
    print("  %-30s %7d" % ("TOTAL (pre-dedup)", total))
    print("  sources reporting:  %d / %d" % (active, len(_SRC_COUNTS)))
    if _PROJECTED[0]:
        print("  reprojected %d point(s) from Web Mercator metres to lat/lng"
              % _PROJECTED[0])
    zero = sorted(k for k, v in _SRC_COUNTS.items() if v == 0)
    if zero:
        print("  ZERO-YIELD (review): " + ", ".join(zero))
    _print_triage(zero)
    print("  geocoder calls used: %d / %d" % (_GEO_CALLS[0], _GEO_MAX))
    print("  refused on policy:   %d dataset(s) that are site registers, not events"
          % _REFUSED[0])
    if _RUN_FLAGS:
        print("  FLAGS (%d):" % len(_RUN_FLAGS))
        for m in _RUN_FLAGS:
            print("    - " + m)
    print("=== END DIAGNOSTIC ===\n")


# A zero is not one thing. Some sources are DELIBERATELY empty (refused on licence
# or policy), some are waiting on a credential you can supply in two minutes, and
# some are genuinely broken. Sorting them here means run 1 tells you what to fix
# instead of leaving you to read 40 lines of log and guess.
_ZERO_EXPECTED = {
    "wa_section18":       "REFUSED on purpose -- not open data, and a site register. "
                          "See the docstring; this will never produce.",
    "spain_fosas":        "REFUSED on purpose -- Ley 20/2022 preservacion especial.",
    "eamena":             "Account-gated, and a site register. Not expected to produce.",
    "ireland_excavation": "Public but copyright rests with report authors. Not shipped.",
    "uk_moj_licences":    "FOI-only; no register exists to read.",
    "queensland_chmp":    "No machine-readable endpoint found.",
    "colombia_ubpd":      "No machine-readable endpoint found yet.",
    "mexico_cnb":         "No machine-readable endpoint found yet.",
    "icmp":               "No machine-readable endpoint found yet.",
}
_ZERO_NEEDS_KEY = {}
# Sources that are down at the far end, not misconfigured here. Checked by hand on
# 2026-08-02; both fail in a browser exactly as they fail for the harvester, so
# there is no path to set and nothing to discover. They stay in the roster because
# the endpoints are correct and will start producing the day the hosts return.
_ZERO_UPSTREAM = {
    "nps_nagpra_grid":
        "UPSTREAM DOWN. apps.cr.nps.gov/nagprapublic returns 404 for every table, "
        "in a browser as well as here, while nps.gov/subjects/nagpra/databases.htm "
        "(updated 2026-06-09) still links to all seven. The NPS app is gone or "
        "moved; the links have not caught up. Nothing to configure -- the Federal "
        "Register families already carry the notices, but not the per-institution "
        "counts of individuals still held, which is what this source is for.",
    "sahris":
        "UPSTREAM DOWN. sahris.sahra.org.za does not resolve. Not a JSON-path "
        "problem: the host itself is unreachable.",
}
_ZERO_DISCOVERY = {}
_ZERO_NEEDS_KEY_REAL = {
    "courtlistener":  ("COURTLISTENER_TOKEN", "courtlistener.com -> Profile -> API"),
    "tribal_comments": ("REGULATIONS_API_KEY", "api.data.gov/signup"),
}
_ZERO_NEEDS_KEY = _ZERO_NEEDS_KEY_REAL


def _print_triage(zero):
    """Sort the zeros into: expected, fixable now, and actually broken."""
    if not zero:
        return
    expected, keys, broken, disc, up = [], [], [], [], []
    for k in zero:
        if k in _ZERO_UPSTREAM:
            up.append(k)
        elif k in _ZERO_DISCOVERY:
            disc.append(k)
        elif k in _ZERO_EXPECTED:
            expected.append(k)
        elif k in _ZERO_NEEDS_KEY and not os.environ.get(_ZERO_NEEDS_KEY[k][0]):
            keys.append(k)
        else:
            # A key-needing source WITH its key set that still returned nothing is
            # not waiting on you -- it is broken, and belongs in the last group.
            broken.append(k)
    if expected:
        print("\n  -- zero BY DESIGN (%d). Nothing to do: --" % len(expected))
        for k in expected:
            print("     %-22s %s" % (k, _ZERO_EXPECTED[k]))
    if keys:
        print("\n  -- zero FOR WANT OF A CREDENTIAL (%d). Two minutes each: --"
              % len(keys))
        for k in keys:
            env, where = _ZERO_NEEDS_KEY[k]
            print("     %-22s %s not set -- get one free at %s" % (k, env, where))
    if up:
        print("\n  -- zero because the SOURCE IS DOWN (%d). Not yours to fix: --"
              % len(up))
        for k in up:
            print("     %-22s %s" % (k, _ZERO_UPSTREAM[k]))
    if disc:
        print("\n  -- zero PENDING DISCOVERY (%d). One browser check each: --" % len(disc))
        for k in disc:
            print("     %-22s %s" % (k, _ZERO_DISCOVERY[k]))
    if broken:
        print("\n  -- zero UNEXPECTEDLY (%d). These should produce and did not: --"
              % len(broken))
        for k in broken:
            print("     %-22s check the per-source line above for an error" % k)
        print("     A source that FAILED has a network or endpoint problem.")
        print("     A source that returned 0 rows has a filter or query problem.")


def _carry_sources(pred, label):
    """Keep the previous harvest's rows for sources this run didn't refresh."""
    if not _remains_exists():
        return []
    try:
        ex = _load_remains()
    except Exception as e:
        print("  carry %s: unreadable (%s)" % (label, e)); return []
    rows = ex.get("records", []) if isinstance(ex, dict) else (ex or [])
    keep = [r for r in rows if pred(r.get("source", ""))]
    print("  carried forward %d rows (%s)" % (len(keep), label))
    return keep


_FED_SOURCES = ("ckan_remains", "ods_remains", "geonode_remains")


def _is_fed(src):
    return src in _FED_SOURCES


def _audit_placement(items):
    """Fail-safe audit: no record whose kind must be coarsened may leave this
    harvester marked exact or area. Anything that slips is coarsened here and
    flagged loudly, because the gate failing silently is the worst outcome."""
    fixed = 0
    for r in items:
        _, _, must = KINDS.get(r.get("kind"), ("", "watch", True))
        if must and r.get("geo") in (GEO_EXACT, GEO_AREA):
            if r.get("lat") is not None and r.get("lng") is not None:
                r["lat"], r["lng"] = _coarsen(r["lat"], r["lng"])
            r["geo"] = GEO_COARSE
            fixed += 1
    if fixed:
        _flag("placement audit coarsened %d record(s) that bypassed the gate" % fixed)
    return items


def _slim(r):
    """Trim each record for wire size: drop empties, round coords, cap prose."""
    q = {}
    for k, v in r.items():
        if v is None or v == "" or v == []:
            continue
        if k in ("lat", "lng"):
            try:
                q[k] = round(float(v), 4)
            except Exception:
                pass
            continue
        if k in ("date", "deadline"):
            q[k] = str(v)[:10]; continue
        if k == "desc":
            q[k] = str(v)[:200]; continue
        q[k] = v
    return q


def _finish(items):
    _print_diagnostics()
    items = [r for r in items
             if r.get("lat") is not None and r.get("lng") is not None]

    # Individual forensic cases are IN SCOPE. Rather than dropping them, tag them so
    # they carry their own kind and can be filtered apart from group events.
    tagged = 0
    for r in items:
        blob = " ".join(str(r.get(k) or "") for k in ("name", "desc"))
        if _is_individual_case(blob):
            if r.get("kind") in ("mass-grave", "discovery", None, ""):
                r["kind"] = "forensic-case"
                r["posture"] = KINDS["forensic-case"][1]
            tagged += 1
    if tagged:
        print("  tagged %d record(s) as individual forensic cases" % tagged)
    if EXCLUDE_INDIVIDUAL_CASES:
        before = len(items)
        items = [r for r in items if r.get("kind") != "forensic-case"]
        print("  EXCLUDE_INDIVIDUAL_CASES=1 -- dropped %d" % (before - len(items)))

    items = _audit_placement(items)
    items = dedup(items)
    items.sort(key=lambda r: -(r.get("impact") or 0))

    # per-source preservation: a source that returns NOTHING keeps its prior rows
    # (a source returning fewer rows may simply have been filtered harder).
    if _remains_exists():
        try:
            ex = _load_remains()
            exl = ex.get("records", []) if isinstance(ex, dict) else (ex or [])
            from collections import defaultdict
            old_by, new_by = defaultdict(list), defaultdict(list)
            for q in exl:
                old_by[q.get("source", "")].append(q)
            for q in items:
                new_by[q.get("source", "")].append(q)
            for src, oldrows in old_by.items():
                if len(oldrows) >= 10 and len(new_by.get(src, [])) == 0:
                    items = [q for q in items if q.get("source") != src] + oldrows
                    print("  [preserve] %s returned nothing (had %d) -- kept prior"
                          % (src or "(none)", len(oldrows)))
        except Exception as e:
            print("  [preserve] skipped: %s" % e)

    # ------------------------------------------------------------------
    # ANTI-WIPE, and the harder rule: NEVER write a zero-record file.
    #
    # The earlier guard only fired when a file already existed, so a FIRST run
    # that produced nothing -- e.g. the federations merge job running before the
    # daily harvest, which carries nothing forward -- happily committed a valid
    # file containing count:0. The map then reported "the record is empty", which
    # is a different and much more alarming statement than "no data file yet".
    #
    # An empty harvest is never news worth committing. Write nothing and say why.
    # ------------------------------------------------------------------
    if not items:
        print("REFUSING TO WRITE: harvest produced 0 records.")
        if _remains_exists():
            print("  an existing %s is left untouched." % REMAINS_GZ)
        else:
            print("  no file written. The map will show its 'no data file yet' state,")
            print("  which is the truth. Check the per-source diagnostic above:")
            print("    * every source FAILED     -> network or endpoint problem")
            print("    * every source returned 0 -> filters or query are wrong")
            print("    * you ran a federations/merge job first -> run the DAILY")
            print("      'Harvest live unearthings' workflow, which is the job that")
            print("      actually populates this file.")
        return
    if len(items) < 4 and _remains_exists():
        try:
            ex = _load_remains()
            exn = ex.get("records", []) if isinstance(ex, dict) else (ex or [])
            if len(exn) > len(items):
                print("harvest thin (%d) < existing (%d) -- keeping existing file"
                      % (len(items), len(exn)))
                return
        except Exception:
            pass

    items = [_slim(r) for r in items]
    by_geo = {}
    by_posture = {}
    for r in items:
        by_geo[r.get("geo", "?")] = by_geo.get(r.get("geo", "?"), 0) + 1
        by_posture[r.get("posture", "?")] = by_posture.get(r.get("posture", "?"), 0) + 1
    meta = {
        "generated": datetime.datetime.utcnow().isoformat() + "Z",
        "count": len(items),
        "sources": ("US NAGPRA notices + federal burial reviews (Federal Register), "
                    "NSW Aboriginal Heritage Impact Permits, California CEQA "
                    "filings, UK planning applications, OpenStreetMap removed "
                    "burial grounds, CKAN open-data portals"),
        "placement_policy": ("Records are plotted at the accountable institution, "
                             "permit area, or administrative unit. Any record that "
                             "is itself a burial location is blurred to about %d km. "
                             "This dataset does not publish grave coordinates."
                             % int(COARSE_GRID_DEG * 111)),
        "geo_breakdown": by_geo,
        "posture_breakdown": by_posture,
        "rating_scale": "1 .. 5 by number of individuals and irreversibility",
        "kinds": {k: v[0] for k, v in KINDS.items()},
        "postures": POSTURE,
    }
    _dump_remains({"_meta": meta, "records": items})
    print("wrote %s with %d records" % (REMAINS_GZ, len(items)))


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CROSS-FEED: projects from the sibling Live Projects map that SAY they involve
# remains.
# ---------------------------------------------------------------------------
# WelcomeToYourGalaxy/local-map tracks ~268,000 pre- and post-permit projects
# worldwide. Some of them are unearthings, and those belong on this map too.
#
# READ THE YIELD HONESTLY. Running the full remains vocabulary across all 268k
# projects returns about 40 candidates, and roughly half of those are place-name
# false positives -- "La Grave", "Saint-Nicolas-de-la-Grave". After a strict filter
# it is TENS of records, not thousands.
#
# That is not a defect in either dataset; it IS the finding. A project description
# rarely mentions burials even when the project will disturb them. Disturbance
# surfaces later -- in the environmental statement, the salvage condition, the
# stop-work order -- not in the title at application time. So this feed catches the
# projects that ANNOUNCE remains, which is a small subset of those that will
# ENCOUNTER them. The rest are only visible through the permit and notice registers
# the other fetchers already read.
#
# Because the base is huge and the signal thin, this does NOT reuse _is_remains().
# It requires an explicit multi-word phrase, which is exactly what kills the
# place-name matches: "grave" alone is a French village, "unmarked grave" is not.
CL_DAYS = int(os.environ.get("CL_DAYS", "1825"))   # five years
PROJECTS_GZ = ("https://raw.githubusercontent.com/WelcomeToYourGalaxy/"
               "local-map/main/projects.json.gz")

# (phrase, kind) -- most specific first, first match wins.
_XFEED_PHRASES = [
    ("ancestral remains",       "reinterment"),
    ("repatriation of remains", "repatriation"),
    ("remains repatriation",    "repatriation"),
    ("human remains",           "discovery"),
    ("skeletal remains",        "discovery"),
    ("unmarked grave",          "discovery"),
    ("unmarked burial",         "discovery"),
    ("mass grave",              "mass-grave"),
    # A memorial TO a burial ground is a commemorative act, not a removal. Kept
    # as `review` so it still appears -- the ground is real and someone is working
    # on it -- but posture stays "watch" rather than accusing anyone of harm.
    ("burial ground memorial",  "review"),
    ("burial ground",           "removed-ground"),
    ("burial site",             "harm-permit"),
    ("exhumation",              "exhumation"),
    ("exhume",                  "exhumation"),
    ("disinterment",            "exhumation"),
    ("burial removal",          "exhumation"),
    ("cemetery relocation",     "exhumation"),
    ("grave relocation",        "exhumation"),
    ("reburial",                "reinterment"),
]
# Looks like a hit, but is routine cemetery estate work rather than an unearthing.
# A cemetery extended into an empty paddock disturbs nobody.
_XFEED_REJECT = (
    "cemetery expansion", "cemetery extension", "expansion of the cemetery",
    "bushfire risk reduction", "cemetery upgrade", "cemetery landscaping",
    "crematorium construction", "new cemetery", "cemetery repairs",
)


def fetch_projects_crossfeed(limit=0):
    """limit=0 means no cap. Same reasoning as XSPATIAL_CAP: a cap truncates the
    scan in file order rather than filtering by anything meaningful."""
    """Projects from the sibling repo whose own text announces remains."""
    out = []
    try:
        raw = _get_bytes(PROJECTS_GZ)
        data = json.loads(gzip.decompress(raw).decode("utf-8", "replace"))
    except Exception as e:
        print("  projects_crossfeed failed: %s" % e)
        return out
    recs = data.get("projects") if isinstance(data, dict) else data
    if not isinstance(recs, list):
        print("  projects_crossfeed: unexpected shape")
        return out
    scanned = rejected = 0
    for r in recs:
        if limit and len(out) >= limit:
            break
        scanned += 1
        blob = " ".join(str(r.get(k) or "") for k in ("name", "desc", "type", "status"))
        low = blob.lower()
        if any(bad in low for bad in _XFEED_REJECT):
            rejected += 1
            continue
        kind = None
        for phrase, k in _XFEED_PHRASES:
            if phrase in low:
                kind = k
                break
        if kind is None:
            continue
        lat, lng = r.get("lat"), r.get("lng")
        if lat is None or lng is None:
            continue
        # Their `precise` flag says whether the point is a real location or an area
        # centroid. Either way _place() coarsens the grave-identifying kinds.
        geo = GEO_EXACT if r.get("precise") else GEO_AREA
        rec = {
            "name": (r.get("name") or "Untitled project")[:150],
            "kind": kind,
            "posture": KINDS[kind][1],
            "trigger": "permit",
            "country": "",
            "region": r.get("state") or "",
            "count": None,
            "held_by": "",
            "actor": r.get("company") or "",
            "status": r.get("status") or "",
            "url": r.get("url") or "",
            "date": r.get("date") or "",
            "deadline": r.get("deadline") or "",
            "desc": ("From the Live Projects map (%s). %s"
                     % (r.get("source") or "unknown source",
                        r.get("desc") or "")).strip()[:600],
            "source": "projects_crossfeed",
        }
        _place(rec, lat, lng, geo)
        rec["impact"] = rate_remains(rec)
        out.append(rec)
    print("  projects_crossfeed: scanned %d, rejected %d as cemetery estate work, "
          "kept %d" % (scanned, rejected, len(out)))
    return out


# ---------------------------------------------------------------------------
# SPATIAL INTERSECTION: projects sitting on top of a known burial ground.
# ---------------------------------------------------------------------------
# The cross-feed above only catches projects whose TEXT announces remains -- six
# out of 268,000. This is the other half of the answer: cross every project
# footprint against the burial-ground layer this repo already harvests, and flag
# the ones close enough that the question has to be asked.
#
# THREE THINGS THIS IS NOT, and the map says all three:
#
# 1. It is not a finding. Output is kind="review", posture="watch". Proximity to a
#    cemetery is a reason to look, not evidence that anyone has been disturbed. A
#    road resurfacing next to a churchyard touches nobody.
#
# 2. It is not coverage. The burial layer is OpenStreetMap-derived and radically
#    incomplete outside western Europe and North America. A project with no hit is
#    not a project with no graves.
#
# 3. It is structurally blind to the worst cases, and this is the important one.
#    Unmarked and undocumented burial grounds are absent from every layer -- that
#    absence is what "unmarked" MEANS. So this method finds projects near RECORDED
#    graves and misses projects over FORGOTTEN ones. The places where a community
#    was never allowed to record its dead are exactly the places this will stay
#    silent about. Design note 11 says so on the map itself.
#
# Cost control: a naive 268,000 x ~1,000,000 comparison is 2.7e11 distance checks.
# Instead the burial points go into a dict keyed by rounded lat/lng cell, and each
# project only tests the nine cells around it -- linear in projects.
XSPATIAL_METRES = int(os.environ.get("XSPATIAL_METRES", "250"))
# 0 = no cap, and that is the default. A cap here does NOT filter by size -- it
# stops the scan mid-file, so whatever happens to sit later in projects.json is
# dropped silently. That reads as "small projects are missing" when really it is
# "everything after record N is missing". Set XSPATIAL_CAP only to debug.
XSPATIAL_CAP = int(os.environ.get("XSPATIAL_CAP", "0"))
_CEM_FILE = "remains_local_cemetery.json.gz"


def _cell(lat, lng, deg):
    return (int(math.floor(lat / deg)), int(math.floor(lng / deg)))


def _metres(lat1, lng1, lat2, lng2):
    """Equirectangular approximation -- ample at a few hundred metres."""
    dlat = (lat2 - lat1) * 111320.0
    dlng = (lng2 - lng1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.sqrt(dlat * dlat + dlng * dlng)


def _load_burial_points():
    """Burial-ground points from the committed facility layer, or None."""
    if not os.path.exists(_CEM_FILE):
        return None
    try:
        with gzip.open(_CEM_FILE, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        print("  xspatial: could not read %s (%s)" % (_CEM_FILE, e))
        return None
    rows = data.get("rows") if isinstance(data, dict) else data
    out = []
    for r in (rows or []):
        try:
            out.append((float(r[0]), float(r[1]), (r[2] if len(r) > 2 else "") or ""))
        except Exception:
            continue
    return out


def fetch_projects_on_burial_ground():
    """Projects within XSPATIAL_METRES of a burial ground in the facility layer."""
    out = []
    pts = _load_burial_points()
    if pts is None:
        print("  xspatial: %s not present yet -- run 'Harvest cemeteries' first. "
              "This source activates on its own once the layer is committed."
              % _CEM_FILE)
        return out
    if not pts:
        print("  xspatial: burial layer is empty; nothing to intersect")
        return out
    deg = XSPATIAL_METRES / 111320.0 * 1.5      # cell a little wider than the radius
    grid = {}
    for (la, ln, nm) in pts:
        grid.setdefault(_cell(la, ln, deg), []).append((la, ln, nm))
    try:
        raw = _get_bytes(PROJECTS_GZ)
        data = json.loads(gzip.decompress(raw).decode("utf-8", "replace"))
    except Exception as e:
        print("  xspatial failed to read projects: %s" % e)
        return out
    recs = data.get("projects") if isinstance(data, dict) else data
    scanned = 0
    for r in (recs or []):
        if XSPATIAL_CAP and len(out) >= XSPATIAL_CAP:
            break
        lat, lng = r.get("lat"), r.get("lng")
        if lat is None or lng is None:
            continue
        scanned += 1
        try:
            lat, lng = float(lat), float(lng)
        except Exception:
            continue
        ci, cj = _cell(lat, lng, deg)
        best = None
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for (la, ln, nm) in grid.get((ci + di, cj + dj), ()):
                    d = _metres(lat, lng, la, ln)
                    if d <= XSPATIAL_METRES and (best is None or d < best[0]):
                        best = (d, nm)
        if best is None:
            continue
        dist, cemname = best
        rec = {
            "name": (r.get("name") or "Untitled project")[:150],
            "kind": "review",
            "posture": KINDS["review"][1],
            "trigger": "proximity",
            "country": "",
            "region": r.get("state") or "",
            "count": None,
            "held_by": "",
            "actor": r.get("company") or "",
            "status": r.get("status") or "",
            "url": r.get("url") or "",
            "date": r.get("date") or "",
            "deadline": r.get("deadline") or "",
            "desc": ("PROXIMITY FLAG, not a finding: this project sits about %d m from "
                     "%s in the burial-ground layer. Nobody has said graves are "
                     "affected -- this is a reason to check the environmental "
                     "statement and the salvage conditions. From the Live Projects "
                     "map (%s)."
                     % (int(round(dist)), (cemname or "a recorded burial ground"),
                        r.get("source") or "unknown source"))[:600],
            "source": "projects_on_burial_ground",
        }
        _place(rec, lat, lng, GEO_EXACT if r.get("precise") else GEO_AREA)
        rec["impact"] = 2
        out.append(rec)
    print("  xspatial: %d burial points, %d located projects tested, %d within %d m"
          % (len(pts), scanned, len(out), XSPATIAL_METRES))
    return out


# ---------------------------------------------------------------------------
# LITIGATION -- CourtListener
# ---------------------------------------------------------------------------
# Cases are where the rule actually changes. A permit register tells you a decision
# was made; a judgment tells you whether the scheme that produced it survives.
#
# Uses the v4 search API. A token is NOT strictly required, but the free
# unauthenticated allowance is small and a token raises it, so this reads
# COURTLISTENER_TOKEN from the environment and says plainly when it is missing
# rather than hammering an anonymous endpoint from CI.
#
# Deliberate limits:
#  * The court is the accountable actor, so a case is plotted at the COURT, not at
#    the burial ground it concerns. A judgment is not an unearthing.
#  * `kind` is "review" and posture "watch". A filed case is an argument, not an
#    outcome, and this map should not imply a court has ruled when it has not.
#  * Court -> coordinates uses a small table of the courts that actually produce
#    this litigation. A case from an unmapped court is skipped rather than dropped
#    onto a country centroid where it would read as a located event.
COURTLISTENER = "https://www.courtlistener.com/api/rest/v4/search/"
CL_QUERIES = [
    "NAGPRA",
    "\"Native American Graves Protection\"",
    "\"repatriation of human remains\"",
    "\"burial ground\" desecration",
    "\"unmarked graves\"",
    "\"cemetery relocation\" injunction",
    "\"Section 106\" \"historic properties\" burial",
    "\"Archaeological Resources Protection Act\"",
]
# Seats of the courts that generate this litigation. Approximate to the courthouse
# city, which is the accountable venue -- not the site in dispute.
CL_COURTS = {
    "scotus": (38.8906, -77.0044), "ca9": (37.7823, -122.4181),
    "ca10": (39.7420, -104.9876), "ca8": (38.6291, -90.1901),
    "ca5": (29.9490, -90.0740), "cadc": (38.8934, -77.0146),
    "ca1": (42.3546, -71.0552), "ca2": (40.7128, -74.0060),
    "ca4": (37.5385, -77.4344), "ca11": (33.7590, -84.3900),
    "cafc": (38.8977, -77.0365), "ca7": (41.8789, -87.6300),
    "ca6": (39.1010, -84.5120), "ca3": (39.9490, -75.1500),
    "azd": (33.4484, -112.0740), "cand": (37.7823, -122.4181),
    "cacd": (34.0537, -118.2428), "caed": (38.5816, -121.4944),
    "dcd": (38.8934, -77.0146), "hid": (21.3069, -157.8583),
    "nmd": (35.0844, -106.6504), "sdd": (44.3683, -100.3510),
    "ndd": (46.8083, -100.7837), "oklahoma": (35.4676, -97.5164),
    "wawd": (47.6062, -122.3321), "ord": (45.5152, -122.6784),
    "akd": (61.2181, -149.9003), "mnd": (44.9778, -93.2650),
    "utd": (40.7608, -111.8910), "nvd": (36.1699, -115.1398),
    "mtd": (46.5891, -112.0391), "wyd": (41.1400, -104.8202),
    "iand": (41.5868, -93.6250), "tennessee": (36.1627, -86.7816),
}


def fetch_courtlistener():
    """US case law mentioning burial, repatriation or grave protection."""
    out = []
    token = os.environ.get("COURTLISTENER_TOKEN", "").strip()
    if not token:
        print("  courtlistener: no COURTLISTENER_TOKEN set. The free tier is small "
              "and anonymous CI polling is rude, so this source is skipped. Get a "
              "token free at courtlistener.com and add it as a repository secret.")
        return out
    since = (dt.date.today() - dt.timedelta(days=CL_DAYS)).isoformat()
    seen = set()
    for q in CL_QUERIES:
        url = COURTLISTENER + "?" + urllib.parse.urlencode(
            {"q": q, "type": "o", "order_by": "dateFiled desc",
             "filed_after": since})
        try:
            data = _get_json_auth(url, {"Authorization": "Token " + token})
        except Exception as e:
            print("  courtlistener %s failed: %s" % (q[:28], e))
            continue
        hits = (data or {}).get("results") or []
        print("  courtlistener %-42s %d hit(s)" % (q[:42], len(hits)))
        for r in hits:
            cid = r.get("cluster_id")
            if not cid or cid in seen:
                continue
            blob = " ".join(str(r.get(k) or "") for k in ("caseName", "suitNature"))
            snips = " ".join((o or {}).get("snippet") or "" for o in (r.get("opinions") or []))
            if not _is_remains(blob + " " + snips):
                continue
            court_id = (r.get("court_id") or "").lower()
            pt = CL_COURTS.get(court_id)
            geo = GEO_EXACT
            if not pt:
                # The 2026-08-02 run returned 22 cases and kept NONE, because
                # CL_COURTS holds ~34 hand-listed courts and US case law comes from
                # hundreds. Dropping an unmapped court threw away the whole source.
                #
                # A case still has an accountable venue even when its coordinates
                # are unknown, so it is placed at national level and marked `admin`
                # -- the same treatment SAHRIS gets. That is honest about not
                # knowing the city while keeping the record. Unmapped court ids are
                # printed so the table can be extended from real data instead of
                # guesswork.
                _CL_UNMAPPED[court_id or "?"] = _CL_UNMAPPED.get(court_id or "?", 0) + 1
                pt = (39.8283, -98.5795)     # geographic centre of the US
                geo = GEO_ADMIN
            seen.add(cid)
            rec = {
                "name": (r.get("caseName") or "Unnamed case")[:150],
                "kind": "review",
                "posture": KINDS["review"][1],
                "trigger": "law",
                "country": "United States",
                "region": r.get("court") or "",
                "count": None,
                "held_by": "",
                "actor": r.get("court") or "",
                "status": r.get("status") or "",
                "url": "https://www.courtlistener.com" + (r.get("absolute_url") or ""),
                "date": r.get("dateFiled") or "",
                "deadline": "",
                "desc": ("Litigation. Plotted at the COURT, not at the ground in "
                         "dispute -- a judgment is not an unearthing. %s%s%s"
                         % (r.get("court") or "",
                            (", docket " + r["docketNumber"]) if r.get("docketNumber") else "",
                            (". " + _strip_marks(snips)) if snips else ""))[:600],
                "source": "courtlistener",
            }
            _place(rec, pt[0], pt[1], geo)
            rec["impact"] = 2
            out.append(rec)
    if _CL_UNMAPPED:
        top = sorted(_CL_UNMAPPED.items(), key=lambda kv: -kv[1])[:12]
        print("  courtlistener: %d case(s) placed nationally, court not in CL_COURTS: %s"
              % (sum(_CL_UNMAPPED.values()),
                 ", ".join("%s x%d" % (k, v) for k, v in top)))
    print("  courtlistener: kept %d case(s)" % len(out))
    return out


_CL_UNMAPPED = {}


def _strip_marks(text):
    """CourtListener wraps matches in <mark>; snippets are plain text here."""
    return re.sub(r"<[^>]+>", "", text or "").replace("\n", " ").strip()


def _get_json_auth(url, headers):
    hdr = {"User-Agent": UA}
    hdr.update(headers or {})
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ---------------------------------------------------------------------------
# TRIBAL AND COMMUNITY OBJECTIONS -- Regulations.gov comments
# ---------------------------------------------------------------------------
# The permit record never tells you who objected. Opposition is a speech act by a
# named party, and for US federal rulemaking it is filed here: 20 million public
# comments, each attached to a docket, each with a submitter.
#
# This is the only structured source of opposition anywhere in the roster. Every
# other feed records what an authority DECIDED; this records what someone SAID
# about it, on the record, in time to matter.
#
# Two design calls worth arguing with:
#
#  * PLOTTED AT THE AGENCY, not at the ground. A comment is directed at a federal
#    decision-maker, and the decision-maker is the accountable actor. That means
#    they stack in Washington DC, which looks odd on a map and is nonetheless
#    where the decision sits. The alternative -- guessing a location from the
#    docket -- would invent geography.
#  * TITLE-ONLY IDENTIFICATION. The comment LIST endpoint returns a title but not
#    the submitter organisation; that needs a per-comment detail request, which at
#    1,000 requests/hour would be the entire budget. Regulations.gov titles almost
#    always read "Comment submitted by <Organization>", so the organisation is read
#    from the title. Comments from individuals are therefore invisible here, which
#    is a real gap: a tribal member commenting personally will not appear.
REGULATIONS_API = "https://api.regulations.gov/v4/comments"
REG_QUERIES = ["NAGPRA", "human remains", "burial ground", "sacred site",
               "tribal consultation", "unmarked graves"]
REG_DAYS = int(os.environ.get("REG_DAYS", "1095"))
# Where the objection lands. Approximate to the agency's headquarters.
REG_AGENCIES = {
    "DOI": (38.8938, -77.0426),   "NPS": (38.8938, -77.0426),
    "BIA": (38.8938, -77.0426),   "BLM": (38.8938, -77.0426),
    "FWS": (38.8938, -77.0426),   "BOR": (38.8938, -77.0426),
    "USACE": (38.8719, -77.0163), "COE": (38.8719, -77.0163),
    "ACHP": (38.9026, -77.0365),  "EPA": (38.8942, -77.0282),
    "FERC": (38.8977, -77.0122),  "USDA": (38.8877, -77.0300),
    "FS": (38.8877, -77.0300),    "DOE": (38.8873, -77.0281),
    "DOT": (38.8757, -77.0028),   "FHWA": (38.8757, -77.0028),
    "HUD": (38.8843, -77.0214),   "DOD": (38.8719, -77.0563),
    "NASA": (38.8830, -77.0163),  "SI": (38.8887, -77.0261),
}


def fetch_tribal_comments(per_page=250):
    """Comments filed by tribes and communities on federal dockets touching burial."""
    out = []
    key = os.environ.get("REGULATIONS_API_KEY", "").strip()
    if not key:
        print("  tribal_comments: no REGULATIONS_API_KEY set. Free key at "
              "api.data.gov/signup; add it as a repository secret. This is the only "
              "structured source of OPPOSITION in the roster -- worth wiring up.")
        return out
    since = (dt.date.today() - dt.timedelta(days=REG_DAYS)).isoformat()
    seen = set()
    for term in REG_QUERIES:
        url = REGULATIONS_API + "?" + urllib.parse.urlencode({
            "filter[searchTerm]": term,
            "filter[postedDate][ge]": since,
            "page[size]": per_page,
            "sort": "-postedDate"})
        try:
            data = _get_json_auth(url, {"X-Api-Key": key})
        except Exception as e:
            print("  tribal_comments %s failed: %s" % (term, e))
            continue
        rows = (data or {}).get("data") or []
        print("  tribal_comments %-24s %d comment(s) returned" % (term, len(rows)))
        for c in rows:
            cid = c.get("id")
            attrs = c.get("attributes") or {}
            title = (attrs.get("title") or "").strip()
            if not cid or cid in seen or not title:
                continue
            # The list endpoint does not carry the submitter organisation, so it
            # is read from the title. The first version required the title to match
            # "Comment submitted by X" and then tested X -- but most regulations.gov
            # titles are not in that form, so the 2026-08-02 run saw 1,302 comments
            # and kept 2.
            #
            # Now: use the "submitted by" name when the title has one, and
            # otherwise test the whole title. A named body usually appears in it
            # either way, and a comment that names no body is still skipped.
            org = _comment_org(title) or title
            if not _is_objector(org):
                continue                      # no community body named anywhere
            agency = (attrs.get("agencyId") or "").upper()
            pt = REG_AGENCIES.get(agency)
            cgeo = GEO_EXACT
            if not pt:
                # Same correction as the courts: an unmapped agency is still a real
                # objection. Place it nationally rather than discarding it.
                _REG_UNMAPPED[agency or "?"] = _REG_UNMAPPED.get(agency or "?", 0) + 1
                pt = (38.8951, -77.0364)      # Washington DC
                cgeo = GEO_ADMIN
            seen.add(cid)
            # When the org came from a whole title rather than a "submitted by"
            # clause, trim it so the record reads as a name and not a sentence.
            label = org.strip()
            for cut in (" objection", " comment", " re:", " regarding", " on the",
                        " on proposed", " to proposed"):
                i = label.lower().find(cut)
                if i > 3:
                    label = label[:i]
                    break
            rec = {
                "name": ("Objection filed: " + label.strip(" ,.;:-"))[:150],
                "kind": "review",
                "posture": KINDS["review"][1],
                "trigger": "objection",
                "country": "United States",
                "region": "",
                "count": None,
                "held_by": "",
                "actor": agency,
                "status": "Comment on the public record",
                "url": "https://www.regulations.gov/comment/" + cid,
                "date": _iso_date(attrs.get("postedDate")),
                "deadline": "",
                "desc": ("A named body objected on the record to a federal action "
                         "touching %s. Plotted at the AGENCY the objection was filed "
                         "with, not at the ground in question -- the agency is the "
                         "accountable actor. Docket comment %s."
                         % (term, cid))[:600],
                "source": "tribal_comments",
            }
            _place(rec, pt[0], pt[1], cgeo)
            rec["impact"] = 2
            out.append(rec)
    if _REG_UNMAPPED:
        top = sorted(_REG_UNMAPPED.items(), key=lambda kv: -kv[1])[:12]
        print("  tribal_comments: %d placed at DC, agency not in REG_AGENCIES: %s"
              % (sum(_REG_UNMAPPED.values()),
                 ", ".join("%s x%d" % (k, v) for k, v in top)))
    print("  tribal_comments: kept %d objection(s) from named bodies" % len(out))
    return out


_REG_UNMAPPED = {}


_COMMENT_BY_RE = re.compile(
    r"comments?\s+(?:submitted\s+)?(?:by|from|of)\s+(.{3,120})$", re.I)


def _comment_org(title):
    """Pull the organisation out of a 'Comment submitted by X' title."""
    m = _COMMENT_BY_RE.search(title.strip().rstrip(".")) 
    if not m:
        return ""
    org = m.group(1).strip(" ,;.")
    # "... by John Smith, Navajo Nation" -> keep the whole thing; the objector
    # test below looks for the body, not the person.
    return org if len(org) > 3 else ""


# A named Indigenous, tribal or descendant body. Deliberately the same shape as the
# wire's objector vocabulary: a personal name alone is not an institution, and this
# map is about bodies with standing.
_OBJECTOR_ORGS = (
    "tribe", "tribal", "tribes", "nation", "band", "pueblo", "rancheria",
    "indian", "native", "indigenous", "first nation", "iwi", "aboriginal",
    "confederated", "council", "thpo", "historic preservation officer",
    "descendant", "traditional", "hawaiian", "alaska native", "village of",
    "community of", "consortium", "intertribal", "inter-tribal",
)


def _is_objector(org):
    o = (org or "").lower()
    return any(t in o for t in _OBJECTOR_ORGS)


# ---------------------------------------------------------------------------
# SOUTH AFRICA -- SAHRIS heritage cases and permits
# ---------------------------------------------------------------------------
# The closest non-US analogue to the NSW AHIP feed, and the reason it is worth the
# effort: SAHRIS holds heritage CASES and PERMIT APPLICATIONS under the National
# Heritage Resources Act 25 of 1999, including s.38 development cases and permits
# for excavation and for the disturbance of graves and burial grounds (s.36).
#
# That is a DECISION record, not a site register -- which is the line this map
# draws. SAHRA's site inventory is refused for the same reason WA's is; the case
# and permit stream is squarely in scope.
#
# SAHRIS runs on Drupal and is publicly accessible. Drupal exposes JSON in several
# conventional ways depending on how Views were configured, and which of them is
# live here could not be tested from the machine that wrote this. So this is a
# DISCOVERY fetcher, like fetch_nps_nagpra_grid: it tries the conventional paths,
# accepts the first that returns parseable records with recognisable fields, and
# prints exactly what it found so the path can be pinned in SAHRIS_PATHS.
SAHRIS_BASE = "https://sahris.sahra.org.za"
SAHRIS_PATHS = [p for p in os.environ.get("SAHRIS_PATHS", "").split(",") if p.strip()] or [
    "/node.json?type=heritage_case",
    "/api/cases?_format=json",
    "/jsonapi/node/heritage_case",
    "/cases/json",
    "/search/node.json",
]
_SAHRIS_FIELDS = ("title", "nid", "type", "created", "changed", "field_", "body")


def fetch_sahris():
    """South African heritage cases and permits mentioning burial or graves."""
    out = []
    found_path = None
    for path in SAHRIS_PATHS:
        url = SAHRIS_BASE + path
        try:
            data = _get_json(url)
        except Exception as e:
            print("  sahris %-34s no (%s)" % (path, str(e)[:40]))
            continue
        rows = _sahris_rows(data)
        if not rows:
            print("  sahris %-34s parsed, but no records in it" % path)
            continue
        blob = json.dumps(rows[0])[:2000].lower()
        hits = sum(1 for f in _SAHRIS_FIELDS if f in blob)
        if hits < 2:
            print("  sahris %-34s records found, fields unrecognised" % path)
            continue
        print("  sahris FOUND -> %s (%d record(s), %d recognisable field(s))"
              % (path, len(rows), hits))
        found_path = path
        for r in rows:
            rec = _sahris_rec(r)
            if rec:
                out.append(rec)
        break
    if found_path is None:
        print("  sahris: none of the conventional Drupal JSON paths answered.")
        print("    Open %s in a browser, watch the network tab while searching"
              % SAHRIS_BASE)
        print("    cases, and set SAHRIS_PATHS to the path that returns JSON.")
        print("    This is the strongest non-US permit feed available; worth the "
              "five minutes.")
    else:
        print("  sahris: kept %d case(s)" % len(out))
    return out


def _sahris_rows(data):
    """Drupal answers in several shapes; accept whichever came back."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "nodes", "results", "rows", "list"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def _sahris_rec(r):
    """One SAHRIS record -> one map record, or None if it is off-topic."""
    if not isinstance(r, dict):
        return None
    node = r.get("node") if isinstance(r.get("node"), dict) else r
    attrs = node.get("attributes") if isinstance(node.get("attributes"), dict) else node
    title = _first_str(attrs, "title", "name", "label")
    if not title:
        return None
    body = json.dumps(attrs)[:4000]
    if not _is_remains(title + " " + body):
        return None
    if _is_site_register(title):
        _REFUSED[0] += 1
        return None
    nid = _first_str(attrs, "nid", "id", "drupal_internal__nid")
    rec = {
        "name": title[:150],
        # A heritage case is a decision in progress, not a completed disturbance.
        "kind": "review",
        "posture": KINDS["review"][1],
        "trigger": "permit",
        "country": "South Africa",
        "region": _first_str(attrs, "field_province", "province") or "",
        "count": _mni(body),
        "held_by": "",
        "actor": "SAHRA",
        "status": _first_str(attrs, "field_case_status", "status") or "",
        "url": (SAHRIS_BASE + "/node/" + nid) if nid else SAHRIS_BASE,
        "date": _iso_date(_first_str(attrs, "created", "changed", "field_date")),
        "deadline": "",
        "desc": ("SAHRIS heritage case or permit application under the National "
                 "Heritage Resources Act 25 of 1999. Plotted at national level "
                 "unless the record names a province -- SAHRIS carries case "
                 "geometry this map does not republish.")[:600],
        "source": "sahris",
    }
    # National placement. Provinces would need a lookup, and guessing one would be
    # worse than admitting the record is national.
    _place(rec, -28.5, 24.7, GEO_ADMIN)
    rec["impact"] = rate_remains(rec)
    return rec


def _first_str(d, *keys):
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list) and v and isinstance(v[0], dict):
            for kk in ("value", "title", "target_id"):
                if isinstance(v[0].get(kk), str) and v[0][kk].strip():
                    return v[0][kk].strip()
        if isinstance(v, dict):
            for kk in ("value", "title"):
                if isinstance(v.get(kk), str) and v[kk].strip():
                    return v[kk].strip()
    return ""

def main():
    if os.environ.get("FED_MERGE") == "1":
        print("MODE: merge federation shard parts")
        import glob as _glob
        items = []
        for f in sorted(_glob.glob("fed_remains_part_*.json")):
            try:
                items += json.load(open(f, encoding="utf-8"))
                print("  merge: read %s" % f)
            except Exception as e:
                print("  merge: %s unreadable: %s" % (f, e))
        if not items:
            print("  merge: no shard data -- keeping prior federation entries")
            items = _carry_sources(_is_fed, "prior federation sources")
        items += _carry_sources(lambda s: not _is_fed(s), "non-federation sources")
        _finish(items); return

    fsh = os.environ.get("FED_SHARD")
    if fsh not in (None, ""):
        k = int(fsh)
        print("MODE: federation shard %s of %s" % (fsh, os.environ.get("FED_SHARDS", "6")))
        items = []
        items += _run("ckan_remains", fetch_ckan_remains)
        items += _run("ods_remains", fetch_ods_remains)
        items += _run("geonode_remains", fetch_geonode_remains)
        with open("fed_remains_part_%d.json" % k, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
        print("wrote fed_remains_part_%d.json with %d rows" % (k, len(items)))
        # A shard that harvested nothing because every fetcher CRASHED must not
        # report success. Zero rows from a working sweep is a finding; zero rows
        # from three exceptions is a bug, and the run has to say so loudly enough
        # that a green tick cannot hide it.
        if not items and _FAILED:
            print("\nSHARD FAILED: every federation fetcher raised (%s)."
                  % ", ".join(sorted(_FAILED)))
            print("This shard harvested nothing. Exiting non-zero so the run is "
                  "not reported as a success.")
            sys.exit(1)
        return

    print("MODE: daily refresh (all sources except federations)")

    # SOURCES RUN IN PARALLEL. They are independent and almost entirely
    # I/O-bound -- each one spends its life waiting on somebody else's server, not
    # computing. Run sequentially, the harvest costs the SUM of every source's
    # latency; run concurrently it costs roughly the SLOWEST one. Nothing is
    # dropped, no query is narrowed, no budget is cut: identical work, overlapped.
    #
    # Threads rather than processes because the work is waiting, and because the
    # fetchers share module state (_SRC_COUNTS, _REFUSED, the geocoder cache) that
    # would have to be marshalled back across a process boundary.
    #
    # HARVEST_WORKERS caps concurrency. It is deliberately modest: these are public
    # registers, several of them small government servers, and this harvester
    # should not be the reason one falls over. Set it to 1 to go back to
    # sequential, which is also the right move when debugging a single source.
    workers = max(1, int(os.environ.get("HARVEST_WORKERS", "6")))
    sources = [
        ("nagpra_notices", fetch_nagpra_notices),
        ("us_burial_reviews", fetch_us_burial_reviews),
        ("nsw_ahip", fetch_nsw_ahip),
        ("ceqanet_burials", fetch_ceqanet_burials),
        ("uk_burial_planning", fetch_uk_burial_planning),
        ("osm_removed_burial_grounds", fetch_osm_removed_burial_grounds),
        # pending roster -- each returns [] and documents why
        ("wa_section18", fetch_wa_section18),
        ("qld_chmp", fetch_qld_chmp),
        ("spain_fosas", fetch_spain_fosas),
        ("ireland_excavation", fetch_ireland_excavation),
        ("eamena", fetch_eamena),
        ("colombia_ubpd", fetch_colombia_ubpd),
        ("mexico_cnb", fetch_mexico_cnb),
        ("nps_nagpra_grid", fetch_nps_nagpra_grid),
        ("uk_moj_licences", fetch_uk_moj_licences),
        ("icmp", fetch_icmp),
        ("projects_crossfeed", fetch_projects_crossfeed),
        ("projects_on_burial_ground", fetch_projects_on_burial_ground),
        ("courtlistener", fetch_courtlistener),
        ("tribal_comments", fetch_tribal_comments),
        ("sahris", fetch_sahris),
    ]
    items = _run_all(sources, workers)
    items += _carry_sources(_is_fed, "federation sources")
    _finish(items)


if __name__ == "__main__":
    main()
