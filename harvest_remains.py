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
import json, sys, os, re, time, datetime, urllib.request, urllib.parse, urllib.error, gzip

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
    "removed-ground": ("Burial ground recorded removed", "harm",  True),
    "mass-grave":    ("Forensic recovery",            "redress",  True),
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
    "excavation": 2, "mass-grave": 3, "discovery": 2, "review": 2,
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
def _get_json(url):
    """GET JSON with ONE retry on transient failures (timeouts, 5xx, 429)."""
    last = None
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
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


def _get_text(url, limit=4000000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
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
_NAGPRA_QUERIES = [
    ("Notice of Inventory Completion", "repatriation"),
    ("Notice of Intent to Repatriate", "repatriation"),
    ("Notice of Intended Disposition", "disposition"),
    ("Notice of Transfer of Control", "reinterment"),
]
# "City ST" or "City, ST" at the end of a NAGPRA notice title.
_NAG_PLACE_RE = re.compile(r",\s*([A-Za-z .'\u2019\-]{2,40}?),?\s+([A-Z]{2})\s*$")


def fetch_nagpra_notices(days=1095, per_page=100, max_pages=12):
    """NAGPRA notices published in the Federal Register."""
    out = []
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    for term, kind in _NAGPRA_QUERIES:
        for page in range(1, max_pages + 1):
            parts = [("conditions[term]", '"%s"' % term),
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
            parts = [("conditions[term]", '"%s"' % term),
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
    import csv, io
    rdr = csv.DictReader(io.StringIO(text))
    return list(rdr)


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
        print("  ceqanet fields: %s" % ", ".join(list(rows[0].keys())[:16]))
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
        nm = tags.get("name") or tags.get("old_name") or "Former burial ground"
        life = next((k for k, _ in _OSM_REMOVED_TAGS if k in tags), "was:landuse")
        rec = {"name": str(nm)[:150], "kind": "removed-ground", "posture": "harm",
               "trigger": "development", "country": "", "region": "",
               "count": None, "held_by": "", "actor": "",
               "status": "Recorded in OpenStreetMap as " + life.split(":")[0],
               "url": "https://www.openstreetmap.org/%s/%s" % (el.get("type"), el.get("id")),
               "date": "", "deadline": "", "source": "osm_removed_burial_grounds"}
        _place(rec, lat, lng, GEO_AREA)             # gate coarsens
        rec["desc"] = ("Burial ground mapped by OpenStreetMap contributors as no "
                       "longer present. Community-recorded, not an official "
                       "register. " + GEO_NOTE[rec["geo"]])[:200]
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
        if c:
            got.append((c[0], c[1], f.get("properties") or {}))
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


def _portal_url_country(p):
    if isinstance(p, str):
        return p, ""
    return (p.get("url") or ""), (p.get("country") or p.get("cc") or "")


def fetch_ckan_remains(per_ds=400):
    """CKAN + DKAN portals -> burial / cemetery / excavation datasets with GeoJSON."""
    reg = _fed_registries()
    if not reg:
        return []
    out, end = [], _fed_budget()
    for proto, lister in (("ckan", None), ("dkan", None)):
        for p in _fed_shard(list(reg.get(proto) or [])):
            if time.time() > end:
                _flag("%s_remains: budget passed" % proto); break
            base, country = _portal_url_country(p)
            if not base:
                continue
            for term in _REMAINS_TERMS[:12]:
                for ds in _ckan_datasets(base, term, rows=20):
                    title = str(ds.get("title") or ds.get("name") or "")
                    blob = title + " " + str(ds.get("notes") or "")
                    if _is_site_register(blob):
                        _REFUSED[0] += 1
                        continue                    # policy refusal, not a miss
                    if not _is_remains(blob):
                        continue
                    for res in (ds.get("resources") or []):
                        if "geojson" not in str(res.get("format") or "").lower():
                            continue
                        ru = res.get("url") or ""
                        if not ru.startswith("http"):
                            continue
                        for lat, lng, pr in _geojson_points(ru, per=per_ds):
                            out.append(_fed_record(lat, lng, pr, title,
                                                   ds.get("url") or base, country,
                                                   "ckan_remains"))
                        break
            time.sleep(0.2)
    return out


def fetch_ods_remains(per_ds=400):
    """OpenDataSoft portals -- French, Swiss, Dutch and municipal catalogues carry
    the densest cemetery and burial-register coverage in Europe."""
    reg = _fed_registries()
    if not reg or "ods" not in reg:
        return []
    out, end = [], _fed_budget()
    for p in _fed_shard(list(reg["ods"])):
        if time.time() > end:
            _flag("ods_remains: budget passed"); break
        base, country = _portal_url_country(p)
        if not base:
            continue
        for term in _REMAINS_TERMS[:10]:
            for ds in _ods_datasets(base, term, rows=20):
                if _is_site_register(ds["title"] + " " + ds["notes"]):
                    _REFUSED[0] += 1
                    continue                        # policy refusal, not a miss
                if not _is_remains(ds["title"] + " " + ds["notes"]):
                    continue
                for lat, lng, pr in _geojson_points(ds["geojson"], per=per_ds):
                    out.append(_fed_record(lat, lng, pr, ds["title"], ds["page"],
                                           country, "ods_remains"))
        time.sleep(0.2)
    return out


def fetch_geonode_remains(per_ds=400):
    """GeoNode portals -- the main route into African, Latin American and Asian
    national spatial-data infrastructures."""
    reg = _fed_registries()
    if not reg or "geonode" not in reg:
        return []
    out, end = [], _fed_budget()
    for p in _fed_shard(list(reg["geonode"])):
        if time.time() > end:
            _flag("geonode_remains: budget passed"); break
        base, country = _portal_url_country(p)
        if not base:
            continue
        for term in _REMAINS_TERMS[:8]:
            for ds in _geonode_datasets(base, term, rows=15):
                if _is_site_register(ds["title"] + " " + ds["notes"]):
                    _REFUSED[0] += 1
                    continue                        # policy refusal, not a miss
                if not _is_remains(ds["title"] + " " + ds["notes"]):
                    continue
                for lat, lng, pr in _geojson_points(ds["geojson"], per=per_ds):
                    out.append(_fed_record(lat, lng, pr, ds["title"], ds["page"],
                                           country, "geonode_remains"))
        time.sleep(0.2)
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
    The s.18 register and the Aboriginal Heritage Inquiry System both exist and
    are the WA equivalent of NSW's AHIP. Not shipped: DPLH's spatial licence has
    previously required written permission for reuse (harvest_projects.py rejected
    a DWER layer on the same ground). VERIFY THE LICENCE FIRST."""
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
        _RUN_FLAGS.append("%s FAILED: %s" % (name, e))
        print("  %-28s FAILED: %s" % (name + ":", e))
        return []


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
    zero = sorted(k for k, v in _SRC_COUNTS.items() if v == 0)
    if zero:
        print("  ZERO-YIELD (review): " + ", ".join(zero))
    print("  geocoder calls used: %d / %d" % (_GEO_CALLS[0], _GEO_MAX))
    print("  refused on policy:   %d dataset(s) that are site registers, not events"
          % _REFUSED[0])
    if _RUN_FLAGS:
        print("  FLAGS (%d):" % len(_RUN_FLAGS))
        for m in _RUN_FLAGS:
            print("    - " + m)
    print("=== END DIAGNOSTIC ===\n")


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

    # anti-wipe
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
        return

    print("MODE: daily refresh (all sources except federations)")
    items = []
    items += _run("nagpra_notices", fetch_nagpra_notices)
    items += _run("us_burial_reviews", fetch_us_burial_reviews)
    items += _run("nsw_ahip", fetch_nsw_ahip)
    items += _run("ceqanet_burials", fetch_ceqanet_burials)
    items += _run("uk_burial_planning", fetch_uk_burial_planning)
    items += _run("osm_removed_burial_grounds", fetch_osm_removed_burial_grounds)
    # pending roster -- each returns [] and documents why
    for nm, fn in (("wa_section18", fetch_wa_section18),
                   ("qld_chmp", fetch_qld_chmp),
                   ("spain_fosas", fetch_spain_fosas),
                   ("ireland_excavation", fetch_ireland_excavation),
                   ("eamena", fetch_eamena),
                   ("colombia_ubpd", fetch_colombia_ubpd),
                   ("mexico_cnb", fetch_mexico_cnb),
                   ("nps_nagpra_grid", fetch_nps_nagpra_grid),
                   ("uk_moj_licences", fetch_uk_moj_licences),
                   ("icmp", fetch_icmp)):
        items += _run(nm, fn)
    items += _carry_sources(_is_fed, "federation sources")
    _finish(items)


if __name__ == "__main__":
    main()
