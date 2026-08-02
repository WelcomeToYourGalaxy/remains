#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wire_remains.py  --  builds wire.json for the Unearthings Wire.

RUN ENVIRONMENT: GitHub Actions (scheduled), NOT the build sandbox.
OUTPUT: wire.json -- a TOP-LEVEL JSON ARRAY of
        {name, title, link, date, sig, snippet, iso, region, topic}
The map checks Array.isArray(...), so the output MUST be an array, not an object.

Sibling of wire_harvest.py in the projects repo. Same two transports, both already
proven in production there:
    * Google News RSS   news.google.com/rss/search?q=...
    * GDELT DOC 2.0     api.gdeltproject.org/api/v2/doc/doc
What changes is the subject. This wire carries ONE topic: the disturbance,
excavation, repatriation and reburial of human remains. Everything else is noise
and gets blocked, however newsworthy.

Dependency: feedparser (pip install feedparser). Falls back to a small built-in
RSS reader if feedparser is unavailable, so a missing dependency degrades instead
of failing the run.

-----------------------------------------------------------------------------
WHY THIS WIRE IS NARROW ON PURPOSE
-----------------------------------------------------------------------------
"Archaeology" as a news topic is mostly discovery-porn: a mosaic, a shipwreck, a
Roman villa. None of that belongs here. The ALLOW vocabulary requires a human-
remains or burial term, and the BLOCK list strips the two failure modes that
otherwise dominate:

  1. Discovery spectacle -- "stunning find", "archaeologists amazed", treasure.
  2. Crime reporting -- murder victims, body found, homicide. Real news, wrong
     map, and it would flood a topic wire about burial grounds with police blotter.

Items are geo-tagged to a country and, where the text names one, a subnational
region, so the map can key the wire to whatever the reader has selected.
"""

import json, os, re, sys, time, datetime, calendar, html, unicodedata
import urllib.request, urllib.parse

try:
    import feedparser
    _HAVE_FP = True
except Exception:
    feedparser = None
    _HAVE_FP = False

CONTACT = os.environ.get("CONTACT", "wheelock.chris@gmail.com")
UA = {"User-Agent": "remains-map-wire/1.0 (+%s)" % CONTACT}
WIRE_OUT = "wire.json"
MAX_AGE_DAYS = int(os.environ.get("WIRE_MAX_AGE_DAYS", "90"))
BUDGET_MIN = int(os.environ.get("WIRE_BUDGET_MIN", "80"))
PER_QUERY = int(os.environ.get("WIRE_PER_QUERY", "20"))

_NET = {"gnews_ok": 0, "gnews_empty": 0, "gdelt_ok": 0, "gdelt_fail": 0,
        "feed_ok": 0, "feed_empty": 0}
_FLAGS = []


def _flag(m):
    _FLAGS.append(m)
    print("  [flag] " + m)


# ---------------------------------------------------------------------------
# TOPIC VOCABULARY
# ---------------------------------------------------------------------------
def _fold(s):
    """Lowercase and strip accents so multilingual terms match reliably."""
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


# An item must contain at least one of these to be on-topic.
ALLOW = [
    # English
    "human remains", "skeletal remains", "ancestral remains", "burial ground",
    "burial site", "burial grounds", "unmarked grave", "unmarked graves",
    "mass grave", "mass graves", "exhumation", "exhumed", "disinterment",
    "reburial", "reburied", "repatriation", "repatriate", "repatriated",
    "nagpra", "ancestral burial", "graveyard", "cemetery relocation",
    "cemetery removal", "grave desecration", "desecration of graves",
    "funerary objects", "remains returned", "return of remains",
    "residential school burial", "unmarked burial", "ossuary", "necropolis",
    "potter's field", "burial place", "grave robbing", "grave looting",
    "looted graves", "tomb looting", "bones returned", "ancestors returned",
    # Spanish / Portuguese
    "restos humanos", "fosa comun", "fosas comunes", "exhumacion", "exhumaciones",
    "cementerio", "sepultura", "sepulturas", "restos oseos", "osamentas",
    "restos mortais", "cemiterio", "exumacao", "vala comum",
    "desaparecidos", "restitucion de restos",
    # French
    "restes humains", "fosse commune", "fosses communes", "exhumation",
    "cimetiere", "sepulture", "ossements", "restitution des restes",
    # German / Dutch
    "menschliche uberreste", "massengrab", "graberfeld", "friedhof",
    "exhumierung", "gebeine", "ruckfuhrung menschlicher",
    "menselijke resten", "massagraf", "begraafplaats", "opgraving",
    # Italian
    "resti umani", "fossa comune", "cimitero", "esumazione",
    # Nordic / Polish / Ukrainian / Russian
    "kvarlevor", "massgrav", "gravplats", "menneskelige levninger",
    "szczatki ludzkie", "masowy grob", "cmentarz", "ekshumacja",
    "ludski zalyshky", "masove pokhovannia", "ostanki",
    # Turkish / Arabic / Indonesian / Japanese / Chinese
    "insan kalintilari", "toplu mezar", "mezarlik",
    "rufat bashariya", "maqbara jamaeia",
    "kerangka manusia", "kuburan massal", "pemakaman",
    "ikotsu", "iseki", "yizhi", "muzang", "wanrenkeng",
]
ALLOW = [_fold(a) for a in ALLOW]

# Discovery spectacle + crime reporting: the two things that would otherwise
# swamp this wire. Blocked even when an ALLOW term is present.
BLOCK = [
    "stunning discovery", "incredible discovery", "amazing discovery",
    "archaeologists stunned", "archaeologists amazed", "shocked archaeologists",
    "treasure hoard", "gold hoard", "shipwreck", "dinosaur", "fossil",
    "mummy curse", "aliens", "ancient aliens", "lost city of",
    "murder victim", "murder suspect", "homicide", "manslaughter",
    "serial killer", "missing woman", "missing man", "missing teen",
    "body found in", "body discovered in a", "police said the body",
    "cold case", "coroner ruled", "autopsy revealed", "died by suicide",
    "pet cemetery", "cemetery vandalised car", "zombie", "halloween",
    "video game", "netflix", "tv series", "movie review", "box office",
    "horoscope", "recipe", "football", "premier league", "nba", "nfl",
]
BLOCK = [_fold(b) for b in BLOCK]


def matches(text):
    t = _fold(text)
    return any(a in t for a in ALLOW)


def blocked(text):
    t = _fold(text)
    return any(b in t for b in BLOCK)


# Significance tiers -- what the map surfaces first. Not a truth claim, a triage.
_SIG_HIGH = [_fold(x) for x in (
    "mass grave", "fosa comun", "fosas comunes", "massengrab", "fossa comune",
    "unmarked grave", "residential school", "desecration", "grave robbing",
    "looted", "bulldozed", "destroyed the burial", "halted", "stop-work",
    "injunction", "lawsuit", "court ordered", "repatriated", "returned to",
    "reburial", "reburied", "exhumation began", "identified the remains")]


def _sig(text):
    t = _fold(text)
    return 2 if any(x in t for x in _SIG_HIGH) else 1


# Which face of the subject an item shows -- lets the map filter the wire the same
# way it filters the dots.
# Topic order matters: the FIRST rule that matches wins, so the most specific
# subjects go first. "opposition" and "trade" both sit above "development" and
# "desecration" because a tribe objecting to a pipeline is an opposition story that
# happens to mention a pipeline, not a development story -- and a seizure of
# trafficked remains is a trade story that happens to mention looting.
_TOPIC_RULES = (
    # WHO IS SAYING NO. This is the topic with the most cultural power in the
    # subject and the least structured data behind it: opposition is a speech act
    # by a named party, and it lives in comment dockets, interventions, court
    # filings and press -- never in the permit record. See the note in
    # harvest_remains.py on why a project record cannot tell you who opposes it.
    #
    # Both halves must be present. "Tribe" alone catches a tribe's own housing
    # project; "opposes" alone catches every planning row on earth. The pairing is
    # what makes it a claim about ancestors rather than about development.
    ("opposition", (
        # objection verbs
        "oppose", "opposes", "opposed", "opposition", "objection", "objects to",
        "protest", "blockade", "occupation", "injunction", "sued", "lawsuit",
        "intervene", "halt work", "stop-work", "stop work order",
        "cease and desist", "walked off", "refuse consent", "withhold consent",
        "se opone", "oposicion", "recurso", "demanda",
    )),
    ("trade", (
        # the live market in human remains: auctions, listings, seizures
        "sold at auction", "auction of human", "auctioned", "auction house",
        "listed for sale", "for sale online", "ebay", "etsy", "skull collector",
        "bone trade", "trade in human remains", "seized", "seizure", "confiscat",
        "customs", "border force", "repatriated after seizure", "smuggl",
        "interpol", "stolen works of art", "illicit trade", "black market",
        "trafficking in human remains",
    )),
    ("redress", ("repatriat", "reburial", "reburied", "returned to", "restitucion",
                 "ruckfuhrung", "restitution", "identified the remains",
                 "handed back", "brought home")),
    ("conflict", ("mass grave", "mass graves", "fosa comun", "fosas comunes",
                  "massengrab", "fossa comune", "vala comum", "masowy grob",
                  "unmarked grave", "unmarked graves", "unmarked burial",
                  "residential school", "boarding school", "indian school",
                  "war crime", "genocide", "disappeared", "desaparecidos",
                  "atrocity", "forensic team", "icmp",
                  "ground-penetrating radar", "ground penetrating radar")),
    ("development", ("construction", "developer", "bulldoz", "pipeline", "highway",
                     "housing", "subdivision", "quarry", "mine", "excavator",
                     "planning application", "permit")),
    ("desecration", ("desecrat", "vandal", "grave robbing", "looted", "looting",
                     "trafficking", "stolen remains", "sold at auction")),
    ("institution", ("museum", "university", "collection", "held by", "inventory",
                     "curator", "smithsonian", "anatomy")),
)


# A named Indigenous or community body. Required alongside an objection verb for
# the "opposition" topic to fire -- see the comment on _TOPIC_RULES.
_OBJECTOR_TERMS = (
    "tribe", "tribal", "tribes", "first nation", "first nations", "band council",
    "pueblo", "nation of", "indigenous", "aboriginal", "traditional owner",
    "traditional custodian", "native american", "native hawaiian", "iwi", "hapu",
    "maori", "sami", "saami", "adivasi", "quilombola", "comunidad indigena",
    "pueblo indigena", "thpo", "historic preservation officer", "elders",
    "land council", "native title", "registered aboriginal party",
    "descendant community", "descendants of", "ancestral",
)
# Ancestors must actually be at stake. Opposition to a mine over water quality is
# a real fight, but it is not this map's fight.
_ANCESTOR_TERMS = (
    "burial", "burials", "grave", "graves", "cemetery", "remains", "ancestor",
    "ancestors", "ancestral", "sacred site", "sacred sites", "wahi tapu",
    "songline", "interment", "human remains", "bones", "funerary",
)


def _has(text, terms):
    return any(x in text for x in terms)


def _topic(text):
    t = _fold(text)
    for name, terms in _TOPIC_RULES:
        if not _has(t, terms):
            continue
        if name == "opposition":
            # needs a named objector AND ancestors at stake, or it is not this
            # topic -- otherwise every planning dispute lands here
            if not (_has(t, _OBJECTOR_TERMS) and _has(t, _ANCESTOR_TERMS)):
                continue
        return name
    return "other"


# ---------------------------------------------------------------------------
# GEO-TAGGING
# ---------------------------------------------------------------------------
_COUNTRY = {
 "US": ["united states", "u.s.", "usa", "american"], "CA": ["canada", "canadian"],
 "MX": ["mexico", "mexican"], "GT": ["guatemala"], "SV": ["el salvador"],
 "HN": ["honduras"], "NI": ["nicaragua"], "CR": ["costa rica"], "PA": ["panama"],
 "CO": ["colombia", "colombian"], "VE": ["venezuela"], "EC": ["ecuador"],
 "PE": ["peru", "peruvian"], "BO": ["bolivia"], "CL": ["chile", "chilean"],
 "AR": ["argentina", "argentine", "argentinian"], "UY": ["uruguay"],
 "PY": ["paraguay"], "BR": ["brazil", "brazilian"], "CU": ["cuba"],
 "DO": ["dominican republic"], "HT": ["haiti"], "JM": ["jamaica"],
 "GB": ["united kingdom", "britain", "british", "england", "scotland", "wales",
        "northern ireland"], "IE": ["ireland", "irish"],
 "FR": ["france", "french"], "DE": ["germany", "german"], "ES": ["spain", "spanish"],
 "PT": ["portugal", "portuguese"], "IT": ["italy", "italian"],
 "NL": ["netherlands", "dutch"], "BE": ["belgium", "belgian"],
 "SE": ["sweden", "swedish"], "NO": ["norway", "norwegian"], "FI": ["finland"],
 "DK": ["denmark", "danish"], "IS": ["iceland"], "PL": ["poland", "polish"],
 "CZ": ["czech"], "SK": ["slovakia"], "AT": ["austria", "austrian"],
 "CH": ["switzerland", "swiss"], "GR": ["greece", "greek"], "RO": ["romania"],
 "HU": ["hungary"], "BG": ["bulgaria"], "HR": ["croatia"], "SI": ["slovenia"],
 "RS": ["serbia"], "BA": ["bosnia", "herzegovina"], "ME": ["montenegro"],
 "MK": ["north macedonia"], "AL": ["albania"], "XK": ["kosovo"],
 "UA": ["ukraine", "ukrainian"], "RU": ["russia", "russian"], "BY": ["belarus"],
 "LT": ["lithuania"], "LV": ["latvia"], "EE": ["estonia"], "MD": ["moldova"],
 "TR": ["turkey", "turkish", "turkiye"], "CY": ["cyprus"],
 "IL": ["israel", "israeli"], "PS": ["palestine", "palestinian", "gaza", "west bank"],
 "LB": ["lebanon"], "SY": ["syria", "syrian"], "IQ": ["iraq", "iraqi"],
 "IR": ["iran", "iranian"], "JO": ["jordan"], "SA": ["saudi arabia"],
 "AE": ["united arab emirates", "uae"], "YE": ["yemen"], "OM": ["oman"],
 "KW": ["kuwait"], "QA": ["qatar"], "AM": ["armenia"], "AZ": ["azerbaijan"],
 "GE": ["georgia"], "AF": ["afghanistan"], "PK": ["pakistan"],
 "IN": ["india", "indian"], "BD": ["bangladesh"], "NP": ["nepal"],
 "LK": ["sri lanka"], "CN": ["china", "chinese"], "TW": ["taiwan"],
 "JP": ["japan", "japanese"], "KR": ["south korea", "korean"],
 "KP": ["north korea"], "MN": ["mongolia"], "KZ": ["kazakhstan"],
 "UZ": ["uzbekistan"], "ID": ["indonesia", "indonesian"], "MY": ["malaysia"],
 "SG": ["singapore"], "TH": ["thailand", "thai"], "VN": ["vietnam", "vietnamese"],
 "KH": ["cambodia", "cambodian"], "LA": ["laos"], "MM": ["myanmar", "burma"],
 "PH": ["philippines", "filipino"], "TL": ["timor"],
 "AU": ["australia", "australian"], "NZ": ["new zealand", "aotearoa"],
 "PG": ["papua new guinea"], "FJ": ["fiji"], "SB": ["solomon islands"],
 "VU": ["vanuatu"], "NC": ["new caledonia"], "PF": ["french polynesia"],
 "ZA": ["south africa", "south african"], "NA": ["namibia"], "BW": ["botswana"],
 "ZW": ["zimbabwe"], "ZM": ["zambia"], "MZ": ["mozambique"], "AO": ["angola"],
 "MW": ["malawi"], "TZ": ["tanzania"], "KE": ["kenya", "kenyan"],
 "UG": ["uganda"], "RW": ["rwanda", "rwandan"], "BI": ["burundi"],
 "ET": ["ethiopia"], "ER": ["eritrea"], "SO": ["somalia"], "SS": ["south sudan"],
 "SD": ["sudan", "sudanese"], "TD": ["chad"], "NE": ["niger"],
 "NG": ["nigeria", "nigerian"], "GH": ["ghana"], "CI": ["ivory coast",
 "cote d'ivoire"], "SN": ["senegal"], "ML": ["mali"], "BF": ["burkina faso"],
 "GN": ["guinea"], "SL": ["sierra leone"], "LR": ["liberia"], "CM": ["cameroon"],
 "CD": ["democratic republic of congo", "drc"], "CG": ["republic of congo"],
 "GA": ["gabon"], "CF": ["central african republic"], "MG": ["madagascar"],
 "MA": ["morocco", "moroccan"], "DZ": ["algeria"], "TN": ["tunisia"],
 "LY": ["libya"], "EG": ["egypt", "egyptian"],
}
_GLOBAL_HINT = ["united nations", " un ", "unesco", "european union", "icmp",
                "international criminal court", "worldwide", "global"]

# Subnational regions worth resolving: federations and the places where burial
# fights are routinely datelined by state or province.
_REGION = {
 "US": ["alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
        "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
        "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
        "new mexico", "new york", "north carolina", "north dakota", "ohio",
        "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
        "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
        "washington", "west virginia", "wisconsin", "wyoming"],
 "CA": ["ontario", "quebec", "british columbia", "alberta", "manitoba",
        "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
        "prince edward island", "yukon", "nunavut", "northwest territories"],
 "AU": ["new south wales", "victoria", "queensland", "western australia",
        "south australia", "tasmania", "northern territory",
        "australian capital territory"],
 "MX": ["jalisco", "veracruz", "guerrero", "michoacan", "tamaulipas", "sinaloa",
        "chihuahua", "coahuila", "nuevo leon", "sonora", "oaxaca", "chiapas",
        "guanajuato", "zacatecas", "durango", "colima", "morelos", "puebla"],
 "BR": ["amazonas", "para", "mato grosso", "bahia", "minas gerais", "sao paulo",
        "rio de janeiro", "parana", "pernambuco", "maranhao", "roraima", "acre"],
 "ES": ["andalusia", "andalucia", "catalonia", "cataluna", "galicia",
        "castile and leon", "castilla y leon", "extremadura", "aragon",
        "valencia", "basque country", "navarre", "asturias", "cantabria",
        "la rioja", "murcia", "madrid", "castilla-la mancha", "balearic",
        "canary islands"],
 "GB": ["england", "scotland", "wales", "northern ireland"],
 "DE": ["bavaria", "saxony", "brandenburg", "hesse", "thuringia",
        "north rhine-westphalia", "lower saxony", "baden-wurttemberg",
        "mecklenburg", "schleswig-holstein", "rhineland-palatinate", "saarland"],
 "IN": ["kerala", "tamil nadu", "karnataka", "maharashtra", "gujarat", "rajasthan",
        "uttar pradesh", "bihar", "west bengal", "odisha", "assam", "punjab",
        "madhya pradesh", "jharkhand", "chhattisgarh", "telangana",
        "andhra pradesh", "kashmir"],
 "ZA": ["gauteng", "kwazulu-natal", "western cape", "eastern cape", "limpopo",
        "mpumalanga", "free state", "north west", "northern cape"],
 "AR": ["buenos aires", "cordoba", "santa fe", "mendoza", "salta", "jujuy",
        "tucuman", "chaco", "neuquen", "chubut", "santa cruz"],
 "NZ": ["auckland", "wellington", "canterbury", "otago", "waikato",
        "bay of plenty", "northland", "hawke's bay", "taranaki", "southland"],
}
# Canonical display name per matched alias. This is not cosmetic: the map keys its
# region filter on this string, so "Andalucia" and "Andalusia" resolving to two
# different regions would split one place into two. Every local-language spelling
# in _REGION must fold to the same canonical name as its English form.
_TITLECASE_FIX = {"new south wales": "New South Wales", "kwazulu-natal": "KwaZulu-Natal",
                  "north rhine-westphalia": "North Rhine-Westphalia",
                  "hawke's bay": "Hawke's Bay", "cote d'ivoire": "Côte d'Ivoire",
                  "castilla-la mancha": "Castilla-La Mancha",
                  "andalucia": "Andalusia", "andalusia": "Andalusia",
                  "cataluna": "Catalonia", "catalonia": "Catalonia",
                  "castilla y leon": "Castile and León",
                  "castile and leon": "Castile and León",
                  "balearic": "Balearic Islands", "michoacan": "Michoacán",
                  "nuevo leon": "Nuevo León", "para": "Pará",
                  "maranhao": "Maranhão", "sao paulo": "São Paulo",
                  "parana": "Paraná", "tucuman": "Tucumán",
                  "neuquen": "Neuquén", "cordoba": "Córdoba",
                  "baden-wurttemberg": "Baden-Württemberg",
                  "northwest territories": "Northwest Territories",
                  "prince edward island": "Prince Edward Island",
                  "australian capital territory": "Australian Capital Territory",
                  "bay of plenty": "Bay of Plenty"}


def _titlecase(s):
    return _TITLECASE_FIX.get(s, " ".join(w.capitalize() for w in s.split()))


def _word_in(needle, hay):
    return re.search(r"(?<![a-z])" + re.escape(needle) + r"(?![a-z])", hay) is not None


def _geo_tag(text):
    """-> (iso2 or None, region or ''). Region is matched first so a state name
    also resolves its country. Ambiguity resolves to nothing rather than a guess."""
    t = _fold(text)
    for iso, regions in _REGION.items():
        for r in regions:
            if _word_in(r, t):
                return iso, _titlecase(r)
    hits = []
    for iso, aliases in _COUNTRY.items():
        if any(_word_in(a.strip(), t) for a in aliases):
            hits.append(iso)
    if len(hits) == 1:
        return hits[0], ""
    if not hits and any(g in t for g in _GLOBAL_HINT):
        return None, ""
    if len(hits) > 1:
        return hits[0], ""          # first match wins; the map shows the country only
    return None, ""


# ---------------------------------------------------------------------------
# TRANSPORTS
# ---------------------------------------------------------------------------
def _http(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_TAG_RE = re.compile(r"<[^>]+>")


def clean(s):
    return _TAG_RE.sub("", html.unescape(str(s or ""))).strip()


def _parse_rss(raw):
    """Minimal RSS/Atom reader for when feedparser is unavailable."""
    out = []
    for m in re.finditer(r"<(item|entry)\b.*?</\1>", raw, re.S | re.I):
        blk = m.group(0)

        def pick(*tags):
            for tg in tags:
                mm = re.search(r"<%s[^>]*>(.*?)</%s>" % (tg, tg), blk, re.S | re.I)
                if mm:
                    return clean(mm.group(1))
                mm = re.search(r'<%s[^>]*href="([^"]+)"' % tg, blk, re.I)
                if mm:
                    return mm.group(1)
            return ""
        out.append({"title": pick("title"), "link": pick("link", "id"),
                    "summary": pick("description", "summary", "content"),
                    "published": pick("pubDate", "published", "updated")})
    return out


def _feed_items(url):
    try:
        raw = _http(url)
    except Exception as e:
        print("    feed failed: %s (%s)" % (url[:70], str(e)[:50]))
        return []
    if _HAVE_FP:
        try:
            d = feedparser.parse(raw)
            return [{"title": e.get("title", ""), "link": e.get("link", ""),
                     "summary": e.get("summary", ""),
                     "published": e.get("published", e.get("updated", "")),
                     "parsed": e.get("published_parsed") or e.get("updated_parsed")}
                    for e in (d.entries or [])]
        except Exception:
            pass
    return _parse_rss(raw.decode("utf-8", "replace"))


def _when(it):
    p = it.get("parsed")
    if p:
        try:
            return datetime.datetime.utcfromtimestamp(calendar.timegm(p)).date().isoformat()
        except Exception:
            pass
    s = str(it.get("published") or "")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+(\d{4})", s)
    if m:
        mo = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
              "oct", "nov", "dec"].index(m.group(2).lower()[:3]) + 1
        return "%s-%02d-%02d" % (m.group(3), mo, int(m.group(1)))
    return ""


def _too_old(date_iso):
    if not date_iso:
        return False                 # undated items are kept; the map can filter
    try:
        d = datetime.date.fromisoformat(date_iso)
    except Exception:
        return False
    return (datetime.date.today() - d).days > MAX_AGE_DAYS


# --- Google News RSS --------------------------------------------------------
_GNEWS_LOCALE = {
    "US": ("en-US", "US"), "GB": ("en-GB", "GB"), "CA": ("en-CA", "CA"),
    "AU": ("en-AU", "AU"), "NZ": ("en-NZ", "NZ"), "IE": ("en-IE", "IE"),
    "ZA": ("en-ZA", "ZA"), "IN": ("en-IN", "IN"), "MX": ("es-419", "MX"),
    "ES": ("es", "ES"), "AR": ("es-419", "AR"), "CO": ("es-419", "CO"),
    "CL": ("es-419", "CL"), "PE": ("es-419", "PE"), "GT": ("es-419", "GT"),
    "BR": ("pt-BR", "BR"), "PT": ("pt-PT", "PT"), "FR": ("fr", "FR"),
    "DE": ("de", "DE"), "AT": ("de", "AT"), "CH": ("de", "CH"),
    "IT": ("it", "IT"), "NL": ("nl", "NL"), "BE": ("nl", "BE"),
    "PL": ("pl", "PL"), "UA": ("uk", "UA"), "RU": ("ru", "RU"),
    "TR": ("tr", "TR"), "GR": ("el", "GR"), "SE": ("sv", "SE"),
    "NO": ("no", "NO"), "DK": ("da", "DK"), "FI": ("fi", "FI"),
    "ID": ("id", "ID"), "JP": ("ja", "JP"), "KR": ("ko", "KR"),
    "PH": ("en-PH", "PH"), "IL": ("he", "IL"), "EG": ("ar", "EG"),
}
# Query per language. Kept short: Google News drops long boolean strings.
_GNEWS_Q = {
    "en": '("human remains" OR "burial ground" OR "mass grave" OR repatriation OR exhumation)',
    "es": '("restos humanos" OR "fosa comun" OR exhumacion OR cementerio)',
    "pt": '("restos mortais" OR "vala comum" OR exumacao OR cemiterio)',
    "fr": '("restes humains" OR "fosse commune" OR exhumation OR sepulture)',
    "de": '("menschliche Überreste" OR Massengrab OR Exhumierung OR Gräberfeld)',
    "it": '("resti umani" OR "fossa comune" OR esumazione OR cimitero)',
    "nl": '("menselijke resten" OR massagraf OR opgraving OR begraafplaats)',
    "pl": '("szczątki ludzkie" OR "masowy grób" OR ekshumacja OR cmentarz)',
    "uk": '("людські залишки" OR "масове поховання" OR ексгумація)',
    "ru": '("человеческие останки" OR "массовое захоронение" OR эксгумация)',
    "tr": '("insan kalıntıları" OR "toplu mezar" OR mezarlık)',
    "el": '("ανθρώπινα λείψανα" OR "ομαδικός τάφος")',
    "sv": '("mänskliga kvarlevor" OR massgrav OR gravplats)',
    "no": '("menneskelige levninger" OR massegrav)',
    "da": '("menneskelige rester" OR massegrav)',
    "fi": '("ihmisen jäänteet" OR joukkohauta)',
    "id": '("kerangka manusia" OR "kuburan massal" OR pemakaman)',
    "ja": '(人骨 OR 遺骨 OR 集団墓地)',
    "ko": '(유해 OR 집단매장지 OR 봉환)',
    "he": '("שרידים אנושיים" OR "קבר אחים")',
    "ar": '("رفات بشرية" OR "مقبرة جماعية")',
}


def _gnews(iso):
    hl, gl = _GNEWS_LOCALE.get(iso, ("en-US", "US"))
    lang = hl.split("-")[0]
    q = _GNEWS_Q.get(lang, _GNEWS_Q["en"])
    ceid = "%s:%s" % (gl, lang)
    url = ("https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q + " when:%dd" % min(MAX_AGE_DAYS, 90), "hl": hl, "gl": gl,
         "ceid": ceid}))
    items = _feed_items(url)
    if items:
        _NET["gnews_ok"] += 1
    else:
        _NET["gnews_empty"] += 1
    # Stamp the language. This wire queries 39 locales in 21 languages, and the
    # language was previously recoverable only by guessing from the source label.
    # A reader who wants to know what the Spanish-language press is reporting --
    # a different set of stories, not a translation of the English ones -- needs
    # this to be a field.
    out = items[:PER_QUERY]
    for it in out:
        it["_lang"] = lang
    return out, "Google News (%s)" % gl


# --- GDELT DOC 2.0 ---------------------------------------------------------
_GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_QUERIES = [
    '"human remains" (repatriation OR reburial OR excavation)',
    '"burial ground" (development OR construction OR permit)',
    '"mass grave" (exhumation OR forensic OR identified)',
    '"unmarked graves" (school OR survey OR search)',
    '"ancestral remains" (museum OR university OR returned)',
]


def _gdelt(query, days=30, maxrec=40):
    url = _GDELT + "?" + urllib.parse.urlencode(
        {"query": query, "mode": "ArtList", "format": "json",
         "maxrecords": maxrec, "timespan": "%dd" % days, "sort": "DateDesc"})
    try:
        raw = _http(url, timeout=45)
        data = json.loads(raw.decode("utf-8", "replace"))
        _NET["gdelt_ok"] += 1
    except Exception as e:
        _NET["gdelt_fail"] += 1
        print("    gdelt failed: %s" % str(e)[:60])
        return []
    out = []
    for a in (data.get("articles") or []):
        sd = str(a.get("seendate") or "")
        date = "%s-%s-%s" % (sd[0:4], sd[4:6], sd[6:8]) if len(sd) >= 8 else ""
        out.append({"title": a.get("title", ""), "link": a.get("url", ""),
                    "summary": "", "published": date, "_date": date,
                    "_src": a.get("domain") or "GDELT"})
    return out


# --- curated feeds ---------------------------------------------------------
# UNVERIFIED BY DESIGN: these are publications that cover this beat, but their RSS
# paths are not live-checked in this build. The harvester reports which returned
# nothing, and the ones that come back empty on the first real run should be
# deleted from this list rather than left in to look thorough.
# Curated feeds. The five originals are general-interest outlets that cover this
# subject occasionally; the additions below are the BODIES THAT DO THE WORK, and
# they are here for one reason -- geographic reach.
#
# Recovery of the war dead and of the disappeared happens in Papua New Guinea, the
# Philippines, Vietnam, Bosnia, Guatemala, Colombia. None of it appears in a permit
# register, because no permit regime governs it. Announcements are how it becomes
# public, so the wire is the right place for it, not the map.
#
# EVERY URL BELOW IS UNVERIFIED. The machine that wrote them could not reach any
# external host, including feeds already working in production. The wire prints the
# item count per feed on every run: a feed reporting 0 across several runs either
# moved or never existed, and should be deleted from this list. That check is the
# verification -- do not assume these work because they are written down.
CURATED = [
    ("ProPublica", "https://www.propublica.org/feeds/propublica/main"),
    ("The Conversation", "https://theconversation.com/articles.atom"),
    ("Hyperallergic", "https://hyperallergic.com/feed/"),
    ("ICIJ", "https://www.icij.org/feed/"),
    ("Anthropology News", "https://www.anthropology-news.org/feed/"),
    # --- war dead: recoveries worldwide, announced case by case ---------------
    ("DPAA", "https://www.dpaa.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=3&max=50"),
    ("CWGC", "https://www.cwgc.org/rss/news/"),
    # --- the disappeared: forensic recovery outside any permit regime ---------
    ("ICMP", "https://www.icmp.int/feed/"),
    ("ICRC", "https://www.icrc.org/en/rss/news"),
    # --- Indigenous rights bodies that report disturbance and repatriation -----
    ("IWGIA", "https://www.iwgia.org/en/rss"),
    ("Cultural Survival", "https://www.culturalsurvival.org/rss.xml"),
    ("Survival International", "https://www.survivalinternational.org/rss"),
]


# ---------------------------------------------------------------------------
def _mk(name, it, topic_default=None):
    title = clean(it.get("title"))
    if not title:
        return None
    snippet = clean(it.get("summary"))[:280]
    blob = title + " " + snippet
    if blocked(blob) or not matches(blob):
        return None
    date = it.get("_date") or _when(it)
    if _too_old(date):
        return None
    iso, region = _geo_tag(blob)
    rec = {"name": it.get("_src") or name, "title": title[:220],
           "link": it.get("link") or "", "date": date,
           "sig": _sig(blob), "snippet": snippet,
           "topic": topic_default or _topic(blob)}
    if it.get("_lang"):
        rec["lang"] = it["_lang"]
    if iso:
        rec["iso"] = iso
    if region:
        rec["region"] = region
    return rec


def collect():
    end = time.time() + BUDGET_MIN * 60
    items, seen = [], set()

    def add(rec):
        if not rec or not rec.get("link"):
            return
        k = rec["link"].split("?")[0].rstrip("/").lower()
        if k in seen:
            return
        seen.add(k); items.append(rec)

    only = os.environ.get("WIRE_ONLY_ISO", "").upper().strip()
    isos = [only] if only else list(_GNEWS_LOCALE.keys())

    print("== Google News, %d locales ==" % len(isos))
    for iso in isos:
        if time.time() > end:
            _flag("budget passed during Google News (stopped at %s)" % iso); break
        got, label = _gnews(iso)
        n0 = len(items)
        for it in got:
            add(_mk(label, it))
        print("  %-3s %-26s +%d" % (iso, label, len(items) - n0))
        time.sleep(1.1)

    print("== GDELT, %d queries ==" % len(_GDELT_QUERIES))
    for q in _GDELT_QUERIES:
        if time.time() > end:
            _flag("budget passed during GDELT"); break
        n0 = len(items)
        for it in _gdelt(q):
            add(_mk("GDELT", it))
        print("  %-52s +%d" % (q[:52], len(items) - n0))
        time.sleep(1.0)

    print("== curated feeds, %d ==" % len(CURATED))
    for name, url in CURATED:
        if time.time() > end:
            _flag("budget passed during curated feeds"); break
        got = _feed_items(url)
        n0 = len(items)
        for it in got:
            add(_mk(name, it))
        kept = len(items) - n0
        if kept:
            _NET["feed_ok"] += 1
        else:
            _NET["feed_empty"] += 1
            _flag("curated feed kept nothing: %s -- delete it if this repeats" % name)
        print("  %-22s fetched %-4d kept %d" % (name, len(got), kept))
        time.sleep(0.8)

    return items


def main():
    t0 = time.time()
    items = collect()
    # newest first, high-significance ahead of low within the same day
    items.sort(key=lambda r: (r.get("date") or "", r.get("sig") or 0), reverse=True)

    # anti-wipe: never replace a healthy wire with an empty one
    if len(items) < 5 and os.path.exists(WIRE_OUT):
        try:
            prior = json.load(open(WIRE_OUT, encoding="utf-8"))
            if isinstance(prior, list) and len(prior) > len(items):
                print("wire thin (%d) < existing (%d) -- keeping existing wire.json"
                      % (len(items), len(prior)))
                return
        except Exception:
            pass

    with open(WIRE_OUT, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, separators=(",", ":"))

    by_iso, by_topic, placed = {}, {}, 0
    for r in items:
        if r.get("iso"):
            placed += 1
            by_iso[r["iso"]] = by_iso.get(r["iso"], 0) + 1
        by_topic[r.get("topic", "other")] = by_topic.get(r.get("topic", "other"), 0) + 1
    print("\n=== WIRE DIAGNOSTIC ===")
    print("  items written:      %d" % len(items))
    print("  geo-tagged:         %d (%d countries)" % (placed, len(by_iso)))
    print("  by topic:           %s" % by_topic)
    print("  transports:         %s" % _NET)
    print("  elapsed:            %.1f min" % ((time.time() - t0) / 60.0))
    if _FLAGS:
        print("  FLAGS (%d):" % len(_FLAGS))
        for m in _FLAGS:
            print("    - " + m)
    top = sorted(by_iso.items(), key=lambda kv: -kv[1])[:12]
    print("  busiest countries:  %s" % ", ".join("%s=%d" % kv for kv in top))
    print("=== END ===")


if __name__ == "__main__":
    main()
