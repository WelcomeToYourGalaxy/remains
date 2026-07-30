# WelcomeToYourGalaxy/remains — source ledger

Global. Sibling of the Live Projects map, standalone repo. Same rule:
**a source ships only after its URL, its field names and its licence have been
checked live.** Anything unverified sits in the pending table returning zero rows
and saying why, so the gaps stay legible.

Verification pass: **2026-07-29**.

---

## 0. What's in the repo

```
index.html                    the map: four layers, the wire, the lenses, the design notes
harvest_remains.py            unearthings + decisions  -> remains.json.gz
harvest_cemeteries.py         global burial directory  -> remains_local_{cemetery,crematory,mortuary}.json
wire_remains.py               topic news, per region   -> wire.json
lenses.json                   curated resource lenses (hand-compiled, tranche 1)
check_lenses.py               link-checks the lenses   -> lenses_status.json
.github/workflows/
  remains.yml                 daily      07:42 UTC
  remains_federations.yml     Tue + Fri  03:45 UTC, 6 shards + merge
  cemeteries.yml              Sat        04:00 UTC, 24 shards + merge
  wire.yml                    every 6h   :20
  lenses.yml                  Mon        05:30 UTC
```

Every schedule is offset from the projects repo's jobs so the two don't contend for
runners or rate limits.

**No data files ship in this drop.** No invented seed sets. The map shows an honest
empty state naming the workflow to run for each missing file.

### The one cross-repo dependency

`harvest_remains.py` **fetches** the ~2,200-portal registries from
`WelcomeToYourGalaxy/local-map/harvest_projects.py` rather than vendoring a copy —
those lists are live-verified there and change as portals come and go, and a copy
would drift silently. Only the bracketed list literals are read, via
`ast.literal_eval`; the fetched file is never executed, so a tampered sibling cannot
run code here. If the fetch fails, the federation sources skip cleanly and every
other source still runs. Override with `SIBLING_HARVESTER_URL` if that repo moves.

---

## 1. Placement policy (read first)

This map plots **the accountable actor and the decision, not the grave.**

Precise burial coordinates are withheld by archaeologists, descendant communities
and states because publishing them invites desecration. In the United States,
site-location data is exempt from disclosure under **NHPA §304** and **ARPA §9**
for exactly that reason, and NAGPRA notices themselves name only counties of removal.

| Record type | Plotted at | `geo` |
|---|---|---|
| Repatriation / disposition / holding | the institution holding the remains | `exact` |
| Permit to harm a burial site | centre of the permit area | `area` |
| Environmental or planning review | the administrative unit it names | `admin` |
| **Anything that is itself a burial location** | **blurred to ~5 km** | `coarsened` |

Enforcement is not left to each fetcher's good intentions:

1. `_place()` coarsens on the record's **`kind`**, not on what the source passed in.
   Hand it exact coordinates for a `mass-grave` and it blurs them.
2. Unknown kinds **fail safe to coarsened.**
3. `_audit_placement()` re-checks every record before writing and coarsens anything
   that slipped, flagging it in the run log.
4. The map draws coarsened records as a **diffuse halo with no centre**, so the
   imprecision is visible rather than buried in a tooltip.

To overrule: one constant, `COARSE_GRID_DEG`, and one table, `KINDS`.

### The cemetery layer is exempt, and that is not an inconsistency

A working cemetery is a signposted public place with a street address that people need
to find. An archaeological or unmarked burial site is the opposite. So
`harvest_cemeteries.py` takes only tags denoting an established public burial facility
and **refuses** `historic=*`, tombs, tumuli, necropoleis, and every lifecycle prefix
(`was:`, `demolished:`, `removed:`, `abandoned:`). Those last are unearthing *events*
and belong to `harvest_remains.py`, where the blur applies. An element carrying both a
cemetery tag and an archaeological tag is dropped rather than published — tested.

---

## 2. Feeds in production

### Unearthings layer → `remains.json.gz`

| Feed | Route | Licence / notes |
|---|---|---|
| **US NAGPRA notices** | `federalregister.gov/api/v1` | Public domain. All four notice families. Live notices confirmed through **2026-07-22**. |
| **US federal reviews naming burials** | same API, burial vocabulary | State-level placement only; no coordinates in the source. |
| **NSW Aboriginal Heritage Impact Permits** | `mapprod3.environment.nsw.gov.au/arcgis/rest/services/EDP/AHIPS/MapServer` | **CC-BY**, NSW DCCEEW. Quarterly. Coverage 2010-01-04 → **2026-06-30**. Same ArcGIS host the projects harvester already queries. DCCEEW notes some permits are absent from the layer; the AHIP Public Register is authoritative. |
| **California CEQA filings** | `ceqanet.lci.ca.gov/Search/Recent?OutputFormat=CSV` | AB 52 tribal consultation means burial disclosure lands here earlier than federally. |
| **UK planning applications** | `planit.org.uk/api/applics/geojson` | Only the planning half is open; the MoJ licence side is not. |
| **OSM removed burial grounds** | Overpass | ODbL. Lifecycle-prefixed cemetery tags. **Not an events feed**, and labelled as such. |
| **CKAN + DKAN portals** | registries fetched from the projects repo | 37-term multilingual vocabulary, 6 shards, budget-capped. |
| **OpenDataSoft portals** | `/api/v2/catalog/datasets` | Densest cemetery and burial-register coverage in Europe. |
| **GeoNode portals** | `/api/v2/resources` + per-layer WFS | Main route into African, Latin American and Asian national spatial-data infrastructures. |

### Cemetery directory → three flat JSON files

| Set | Tags taken | Output |
|---|---|---|
| cemetery | `landuse=cemetery`, `amenity=grave_yard` | `remains_local_cemetery.json` |
| crematory | `amenity=crematorium` | `remains_local_crematory.json` |
| mortuary | `amenity=mortuary`, `shop=funeral_directors` | `remains_local_mortuary.json` |

ODbL, attributed in the map. Row shape is the projects map's facility contract:
`[lat, lng, name, website, "", address, "", phone]` — so these files drop into the
same facility-layer code path.

Carries the same hardening that fixed the box-shaped gaps on the projects layer:
Overpass answers a silent server-side timeout with HTTP 200 **plus a `remark` field**,
so that is treated as failure and the tile is recursively quarter-split down to a
floor. Cemeteries are far denser than courthouses, so the grid is 2.5° with a
0.3125° floor and 24 shards.

### The wire → `wire.json`

Google News RSS across 39 locales in 21 languages, plus GDELT DOC 2.0 — both
transports already proven in the projects repo's wire. An item must contain a
human-remains or burial term, and two things are blocked outright:

- **discovery spectacle** — "stunning find", treasure hoards, shipwrecks
- **crime reporting** — homicides, missing persons, coroners

Either would swamp a wire about burial grounds. Items are geo-tagged to a country and,
where the text names one, a subnational region; local-language spellings fold to one
canonical name so "Andalucía" and "Andalusia" don't split one place into two regions.
Topic-classified into return / conflict / development / desecration / institutions,
and the map filters on those.

The five entries in `CURATED` are **unverified by design**. The harvester reports which
returned nothing; delete those rather than leaving them in to look thorough.

---

## 3. Pending — real registers, no verified machine route yet

| Register | What blocks it |
|---|---|
| **NPS National NAGPRA tables** (`apps.cr.nps.gov/nagprapublic`) | The Inventories and Unclaimed Lists grids carry **per-institution counts of individuals still held** — nothing else does. DataTables grids with export buttons, so a JSON endpoint exists, but it was not discoverable from the rendered page. **Highest-value gap.** |
| **Western Australia s.18 consents** (DPLH) | The WA equivalent of an AHIP. Spatial reuse has previously required written permission — the projects harvester rejected a WA DWER layer on the same ground. **Check the licence before the endpoint.** |
| **Queensland CHMP register** | Published; no machine-readable endpoint located. |
| **Spain — Mapa integrado de localización de personas desaparecidas** | **Verified 2026-07 as not machine-readable.** The live search tool is georeferenced; the downloadable open-data release dates from 2017 and carries no coordinates at all. See below. |
| **Ireland** — National Monuments excavation licences, excavations.ie | Public; neither confirmed as an API. |
| **EAMENA** | MENA disturbance and looting, with geometry. Account-gated; reuse terms unchecked. |
| **Colombia UBPD** | `datos.gov.co` is Socrata so transport is proven; dataset ids unverified. |
| **Mexico CNB** | Clandestine-grave registry. Publication intermittent and aggregate. |
| **England & Wales MoJ licences** (Burial Act 1857 s.25) | Issued in volume; released only on request. |
| **ICMP** | Fullest record of conflict-grave exhumations anywhere. Publishes reports, not data. |

### Spain settles a question rather than opening one

Spain publishes grave coordinates deliberately, so families can find their dead —
which looked like grounds to exempt it from the blur. But **Ley 20/2022 art. 17** both
mandates the map *and* requires the zones it names to receive *preservación especial*.
Spain publishes and protects at once. So the blur stays even there. Separately, the
data isn't harvestable anyway.

---

## 4. Honest-coverage statement

- **No coverage percentage.** No dataset holds the true global count of unearthings,
  so any figure would be invented. The shape of the sample is described instead.
- **Counts are never estimated.** `_mni()` extracts a number only after an explicit
  "at least / a minimum of / a total of". Absence means the source was silent, never
  that the number is small.
- **Coverage is weighted to statutory regimes.** Countries with repatriation law and
  heritage-permit systems generate records; countries without them generate silence.
  Sparse regions are sparse in the *records*, not necessarily in the ground.
- **The same caution applies to the wire.** An empty wire for a region is a gap in
  reporting, not evidence that nothing is happening — the map says so in place of the
  empty list.
- **`posture` is not a quality score.** A repatriation and a permit to destroy are both
  here. `harm` / `watch` / `redress` / `unlawful` keeps them distinct; `impact` carries
  magnitude only. A large repatriation is a large event, not a bad one.
- **The lenses are curated, and curation rots.** 63 entries across 8 lenses; 56 carry a
  URL, 7 carry a search string instead because no URL was verified — that is the honest
  form, not an omission. `check_lenses.py` verifies the rest weekly and distinguishes
  *dead* from *blocks bots* (403/405 on an automated request is not a dead link). Dead
  links are **greyed out and labelled, not deleted**: a resource that has vanished
  tells you an office closed, which is worth knowing.

---

## 5. Lens tranche 1

| Lens | Entries | What it answers |
|---|---|---|
| Repatriation & return | 10 | Who is obliged to give ancestors back, and how |
| Whom you must consult | 7 | The parties with a legal right to be at the table |
| Heritage regulators & permit registers | 9 | Who issues permission to disturb |
| Forensic recovery & the disappeared | 8 | When the grave is a crime scene |
| The instruments themselves | 10 | The statutes that create duty, offence and standing |
| Records & archives to pry loose | 7 | Where an institution's own account survives |
| Monitoring & imagery | 5 | Watching ground you cannot stand on |
| Watchdogs, press & allies | 7 | Who is already doing this work |

Weighted toward jurisdictions with published regimes. **Absence of a country here is
absence of a published regime, not absence of the problem** — that warning sits in the
file's own `_meta` so it travels with the data.

Run `Check lens links` first; treat its `dead` and `redirect` output as the
tranche-1 punch list.
