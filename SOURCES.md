# WelcomeToYourGalaxy/remains — source ledger

Global. Sibling of the Live Projects map, standalone repo. Same rule:
**a source ships only after its URL, its field names and its licence have been
checked live.** Anything unverified sits in the pending table returning zero rows
and saying why, so the gaps stay legible.

Verification pass: **2026-07-29**.

---

## 0. Where the files go

Everything except the workflows sits in the **repo root**. All five workflows go in
**`.github/workflows/`**. The harvesters write their output to the root as well, which
is where `index.html` looks for it — every fetch in the map is relative, so serving the
repo root through Pages needs no path changes.

```
/                                 (repo root)
  index.html                      the map: four layers, the wire, the lenses, the design notes
  harvest_remains.py              unearthings + decisions -> remains.json.gz
  harvest_cemeteries.py           burial directory        -> remains_local_{cemetery,crematory,mortuary}.json.gz
  wire_remains.py                 topic news, per region  -> wire.json
  check_lenses.py                 link-checks the lenses  -> lenses_status.json
  lenses.json                     curated resource lenses (hand-compiled)
  SOURCES.md                      this file
  DEPLOY.md                       setup runbook: permissions, Pages, run order, failure modes
  .nojekyll                       empty file -- see below

  .github/workflows/
    remains.yml                   daily      07:42 UTC
    remains_federations.yml       Tue + Fri  03:45 UTC, 6 shards + merge
    cemeteries.yml                Sat        04:00 UTC, 24 shards + merge
    wire.yml                      every 6h   :20
    lenses.yml                    Mon        05:30 UTC
```

Written by the workflows, committed to the root, not shipped in the drop:
`remains.json.gz`, `remains_local_cemetery.json.gz`, `remains_local_crematory.json.gz`,
`remains_local_mortuary.json.gz`, `wire.json`, `lenses_status.json`.

**All three facility files are gzipped.** A worldwide cemetery sweep is on the order of
half a million to a million features — 45–75 MB of raw JSON, past GitHub's 50 MB warning
and a punishing fetch for a browser that cannot draw anything until the whole file
arrives. Gzip takes it to roughly 11–19 MB, and the map inflates it client-side with the
same dual-path loader it uses for the dot layer. Shard artifacts stay uncompressed —
they are transient and never pushed. The merge job warns if a compressed file passes
45 MB, which is the point to split that set by continent.

### `.nojekyll`

Add an **empty file named `.nojekyll`** to the root. GitHub Pages only recognises the
name with the leading dot — the `local-map` repo has one called `nojekyll` without it,
which does nothing. Without `.nojekyll`, Pages runs the content through Jekyll, which
skips files and directories beginning with `_` and can interfere with how `.gz` assets
are served.

### First run

See **DEPLOY.md**. The one thing that must happen before any workflow runs:
**Settings → Actions → General → Workflow permissions → Read and write**. Every
workflow commits its output, and on a read-only repo a run will harvest for hours and
then fail at the push.

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

### Administrative units → fetched live, nothing stored

Every government level worldwide, from **`WelcomeToYourGalaxy/cgaz-boundaries`** —
the same set and the same code path the Live Projects map uses, so the two atlases
behave identically.

| Level | Source | File |
|---|---|---|
| Country | world-atlas@2 `countries-50m.json` (topojson, UN numeric ids → ISO3) | CDN |
| ADM1 | CGAZ | `ISO3.geojson` |
| ADM2 | CGAZ | `ISO32.geojson` |
| ADM3 | CGAZ | `ISO33.geojson` |
| ADM4 | CGAZ | `ISO34.geojson` |
| large ADM3/ADM4 | CGAZ, sharded | `ISO3<digit>_<n>.geojson` |

Nothing is harvested or committed for this — the map fetches per country, per
level, on demand and caches in memory. Files run from 13 KB (Andorra) to 7 MB
(a single US ADM4 shard), which is exactly why they are fetched only when you
drill into them.

Three things carried over from the projects map because they are already solved
there:

- **Shard tables.** `ADM3_PARTS` and `ADM4_PARTS` enumerate which countries are
  split and into how many parts. Twenty countries have sharded ADM4; the US has
  six parts, Russia eight.
- **The antimeridian fix.** A far-eastern unit — Chukotka, Fiji, New Zealand — can
  be a MultiPolygon with pieces on *both* sides of 180° without any single ring
  spanning it, so a per-ring test never fires and the unit renders as a
  globe-spanning streak that also swallows every click behind it.
  `gbUnwrapAntimeridian()` shifts the minority side (by point count) across;
  `gbNormLng()` is its click-side companion.
- **Depth is not uniform.** Some countries publish four levels, some one. A level
  pill greys itself out when a fetch 404s rather than pretending the level exists.

Numeric filenames (`111`, `-994`, …) are geoBoundaries' codes for disputed and
unassigned territories — Abyei, Northern Cyprus, Somaliland and similar. They are
in the drilldown like anywhere else.

**Fetch order:** GitHub Pages first (CDN-cached, and what the projects map runs
on), falling back per file to `raw.githubusercontent.com`, which serves the same
files with `access-control-allow-origin: *`. So the layer still works if Pages is
off or mid-build.

**Opening a unit** shows what the harvest already puts inside it — a real
point-in-polygon test against the record set, holes included, not a bbox
approximation. Curated per-unit resources are the next layer to go on top; until
they exist the panel says so rather than showing an empty list that looks
finished.

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
| **NPS National NAGPRA tables** (`apps.cr.nps.gov/nagprapublic`) | **Now a discovery fetcher rather than a stub** — see below. The endpoint is still undocumented, but the harvester probes for it at run time instead of giving up. |
| **Western Australia s.18 consents** (DPLH) | The WA equivalent of an AHIP. Spatial reuse has previously required written permission — the projects harvester rejected a WA DWER layer on the same ground. **Check the licence before the endpoint.** |
| **Queensland CHMP register** | Published; no machine-readable endpoint located. |
| **Spain — Mapa integrado de localización de personas desaparecidas** | **Verified 2026-07 as not machine-readable.** The live search tool is georeferenced; the downloadable open-data release dates from 2017 and carries no coordinates at all. See below. |
| **Ireland** — National Monuments excavation licences, excavations.ie | Public; neither confirmed as an API. |
| **EAMENA** | MENA disturbance and looting, with geometry. Account-gated; reuse terms unchecked. |
| **Colombia UBPD** | `datos.gov.co` is Socrata so transport is proven; dataset ids unverified. |
| **Mexico CNB** | Clandestine-grave registry. Publication intermittent and aggregate. |
| **England & Wales MoJ licences** (Burial Act 1857 s.25) | Issued in volume; released only on request. |
| **ICMP** | Fullest record of conflict-grave exhumations anywhere. Publishes reports, not data. |

### The NPS grids now probe for themselves

`fetch_nps_nagpra_grid()` was a dead stub. It is now a **discovery fetcher**: it reads
the grid page, pulls any DataTables `ajax` target out of the markup, adds a short list
of conventional ASP.NET MVC candidates, and tries each. A candidate counts as found only
if it returns JSON whose rows carry at least two recognisable columns (museum / agency /
state / individuals). Everything it tries and finds is printed.

This runs in Actions, which has the open-web access the build sandbox lacks — so run 1
either wires the source or tells you exactly what to put in `NPS_GRID_PATHS`. If it
fails, open the network tab on `/Home/Inventory`, copy the URL the grid calls, and set
that env var; no code change needed.

Worth the effort because these two tables are the only place a **count of individuals
still held per institution** exists. The Federal Register shows remains only once an
institution has decided to move them, so an institution that has never filed a notice is
invisible everywhere except here. Records land as `kind="holding"` at state level — a
category the taxonomy has always defined and no source had filled.

---

### Spain settles a question rather than opening one

Spain publishes grave coordinates deliberately, so families can find their dead —
which looked like grounds to exempt it from the blur. But **Ley 20/2022 art. 17** both
mandates the map *and* requires the zones it names to receive *preservación especial*.
Spain publishes and protects at once. So the blur stays even there. Separately, the
data isn't harvestable anyway.

---

---

## 3a. Refused on policy — data that should not be published

Everything in section 3 is a source we cannot yet **get**. This section is the first
category refused because it should not be **published**.

### Registers of site locations

Discovered while evaluating New Zealand: **ArchSite** holds around 80,000 recorded
archaeological site locations and exposes real GeoServices, WMS and WFS endpoints
through an ArcGIS Hub. NZ district councils separately publish *Archaeological & Waahi
Tapu Sites* layers on CKAN. The federation sweep, searching for burial and cemetery
terms, would find exactly those layers.

They are refused, for two independent reasons:

1. **The blurring gate is not sufficient protection for a register.** Coarsening one
   grave to a 5 km cell hides it in the noise. Coarsening a register of thousands of
   sites still discloses *which cells contain them* — and for **wāhi tapu**, sacred
   places held deliberately un-public by the communities they belong to, that
   disclosure is the harm.
2. **A register is not an unearthing.** Nothing has happened, nobody applied, there is
   no decision and no accountable actor. It fails this map's own definition of a record
   twice over.

`_is_site_register()` refuses these at **dataset level**, before a single point is read,
and the check runs *before* the burial vocabulary so a layer that matches both is still
refused. The run diagnostic prints how many datasets were refused this way, so the
refusals are visible rather than silent. Terms covered include wāhi tapu in three
spellings, site recording schemes, ArchSite, sacred and restricted sites, archaeological
site registers and inventories, `carte archéologique`, `Denkmalliste`, `Fundstellen`,
`sitios arqueológicos`, and tumulus and barrow inventories.

ArchSite is also subscription-gated, but that is beside the point — it would be refused
if it were wide open.

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

## 5. Lenses — tranche 3

**156 entries across 12 lenses. 101 carry a URL; 55 carry a search string.**

| Lens | Entries | What it answers |
|---|---|---|
| Who is holding them | 12 | Whether a named institution holds ancestors from a place |
| Repatriation & return | 15 | Who is obliged to give them back, and how |
| Whom you must consult | 14 | The parties with a legal right to be at the table |
| Heritage regulators & permit registers | 18 | Who issues permission to disturb |
| Forensic recovery & the disappeared | 15 | When the grave is a crime scene |
| The instruments themselves | 17 | The statutes that create duty, offence and standing |
| How it actually gets stopped | 12 | Stop-work powers, complaint bodies, litigators, money |
| Competence & standards | 8 | Who is qualified, and what good practice requires |
| Records & archives to pry loose | 15 | Where an institution's own account survives |
| Monitoring & imagery | 9 | Watching ground you cannot stand on |
| Erosion, thaw & fire | 7 | The driver with no permit and no applicant |
| Watchdogs, press & allies | 14 | Who is already doing this work |

Tranche 1 built the eight core lenses. Tranche 2 added *Who is holding them* and *How it
actually gets stopped*, and roughly doubled the rest with non-anglophone depth. Tranche 3
added the two lenses below, plus the NPS Summaries, newspaper-notice and grants tables,
Hawai‘i's burial-council regime (the one US state system that works differently from
every other), and 36 CFR 800.

### Competence & standards

Knowing a permit exists, and knowing how to challenge it, still leaves the question of
**who is qualified to do the work and what competent practice requires**. Without that,
"the consultant's report was inadequate" is an assertion rather than a finding. CIfA and
BABAO publish the standards an accredited practitioner can be held to; the Register of
Professional Archaeologists has a public grievance procedure. Also here: what
ground-penetrating radar can and cannot show — it finds soil disturbance, not bodies, and
that distinction has been central to the residential-school reporting — and the published
guidelines on destructive sampling for ancient DNA, which consumes the remains.

### Erosion, thaw & fire — and the blind spot it names

Every register this map harvests is a **consent or compliance regime**: someone applied,
someone decided, someone filed. Erosion, permafrost thaw and wildfire unearth remains
with **no permit, no applicant and no accountable party**. No register records them, so
the dot layer is structurally blind to them.

This is not a missing feed, and adding one would not fix it — the thing that generates
the records does not exist for this cause. The lens is a partial answer: it points at the
monitoring projects and volunteer networks that do the recording instead (SCAPE/SCHARP,
CITiZAN, the Climate Heritage Network). The map states this as design note 7 so a quiet
coastline reads as the map's limit rather than the ground's, and `_meta.blind_spot` in
`lenses.json` carries the same warning with the data.

The harvester's `trigger` vocabulary has always included `erosion`. Until tranche 3 no
lens supported it, which meant the taxonomy could label a cause it could not help with.

---

Weighted toward jurisdictions with published regimes. **Absence of a country here is
absence of a published regime, not absence of the problem** — that warning sits in the
file's own `_meta` so it travels with the data.

Run `Check lens links` first; treat its `dead` and `redirect` output as the punch list.
