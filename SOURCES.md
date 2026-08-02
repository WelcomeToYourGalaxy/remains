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

### Western Australia — checked, and refused on two grounds

The WA s.18 regime is the largest harm-permit feed still missing, and it was the
open question left over from the NSW build. Checked 2026-07-31; it stays out.

**It is not open data.** The DPLH spatial layers on data.wa.gov.au (Aboriginal
Cultural Heritage Register DPLH-099, Historic DPLH-098, the retired DPLH-001) sit
behind a licence agreement requiring active acceptance, and download requires a
subscription. Free to *view* through AHIS/ACHIS or Locate is not free to
*redistribute* — the same ground on which the projects harvester rejected a DWER
layer.

**And it is a site register**, which this harvester refuses on its own terms. DPLH
states that the exact location and extent of some places are withheld and replaced
with a shaded region of at least 4 km² "to preserve confidentiality". The custodian
has already decided those locations should not be published precisely. Republishing
them — or even mirroring the buffered version — overrides a judgement that was not
ours to make.

**The distinction that keeps NSW in and WA out** is worth stating plainly, because
it is the whole design in one comparison. NSW AHIP records are **permits**: an
accountable actor, a decision, a date, published as open data under CC-BY. WA's
equivalent is a register of **places**. A permit register names who authorised harm;
a site register names where the dead are. This map carries the first and refuses the
second.

If DPLH ever publishes s.18 **consent decisions** as an open list — applicant, date,
outcome, without site geometry — that is squarely in scope and should be added. It is
the decisions that belong here, never the places.

### NAGPRA notice families — now twelve, not four

The Federal Register fetcher queried four notice families, all of which announce a
**completed** decision. Eight more were added:

| Family | Kind | Why |
|---|---|---|
| Receipt of a Request for Repatriation (3 wordings) | `holding` | The **pending** queue. A map of completed returns shows the system working; the pending side shows what is stuck, which is usually the story. |
| Deaccession / Notice of Transfer (3 wordings) | `transfer` | Remains **leaving a collection without going home** — sold, or moved between institutions. |
| Corrections to Inventory / Intent (2) | `review` | How a wrong affiliation finding gets undone. Few in number, disproportionately important. |

`transfer` is a new kind, posture **watch**, deliberately separate from
`reinterment`: a transfer is not a return, and merging them would report custody
changes as redress.

**Eight of the twelve are unverified** — the Federal Register was unreachable from
the machine that wrote them, so the exact notice wording could not be confirmed. The
harvester now prints a per-family tally and names any family that kept nothing, so
run 1 tells you which wordings are wrong. Neither failure mode can corrupt the data:
the title check already rejects anything that is not that notice, so a bad phrase
yields zero rather than noise.

### The federation bug, and why the run looked green

The 2026-07-31 federation run reported success on GitHub and harvested **nothing
from 1,236 portals**. Two separate defects:

**The registry stores tuples, not dicts.** Entries look like
`('https://datos.gob.cl', 'Chile', 'cl')`. `_portal_url_country()` handled `str`
and `dict` only, so every portal raised `'tuple' object has no attribute 'get'` and
all three fetchers died on their first item. Now handles tuples, lists, dicts and
bare strings, prefixes a missing scheme, and returns `("", "")` for anything
unrecognised rather than raising — one odd entry must not kill a sweep. Verified
against the real registry: **1,236 of 1,236 entries unpack, across 154 countries.**

**A shard that crashed exited 0.** `_run()` catches per-source exceptions so one
failure cannot kill a harvest — correct in general, wrong here, because when *all*
fetchers crash the shard writes an empty part file and reports success. Zero rows
from a working sweep is a finding; zero rows from three exceptions is a bug, and a
green tick was hiding the difference. A shard that harvested nothing **and** had
every fetcher raise now prints the failing sources and exits 1. `_run()` also prints
a traceback, which is what turns this class of failure into a five-minute diagnosis.

What the registry actually reaches, once it runs: France 216, Indonesia 148,
Thailand 94, Argentina 87, Brazil 67, Germany 35, Mexico 34, Italy 22, Greece 21,
Spain 18, Ukraine 17, Vietnam 13, Costa Rica 13, Ecuador 12, Bolivia 10, Ethiopia
10, Colombia 8, Guatemala 8, Nepal 8, Philippines 8, Chile 7, Peru 4 — **89% of the
1,236 portals are non-Anglophone.**

### South Africa → SAHRIS (discovery fetcher)

The closest non-US analogue to the NSW AHIP feed, and the best answer available to
the coverage problem. SAHRIS holds heritage **cases and permit applications** under
the National Heritage Resources Act 25 of 1999 — including s.38 development cases
and permits for excavation and for disturbance of graves and burial grounds under
s.36.

That is a **decision record, not a site register**, which is exactly the line this
map draws. SAHRA's site inventory is refused for the same reason WA's is; the case
and permit stream is squarely in scope.

SAHRIS runs on Drupal and is publicly accessible, but which JSON path is live could
not be tested from here. So `fetch_sahris()` is a **discovery fetcher** like the NPS
grids: it tries the conventional Drupal paths (`/jsonapi/…`, `/node.json`,
`?_format=json`), accepts the first returning parseable records with recognisable
fields, and prints what it found. Set `SAHRIS_PATHS` to pin it.

Records are plotted **nationally**, at admin precision, unless the record names a
province. SAHRIS carries case geometry that this map does not republish.

### Reading run 1: the zero-yield triage

A zero is not one thing, and the log now says which kind it is:

- **Zero by design** — refused on licence or policy (WA, EAMENA, Spain, Ireland).
  Nothing to do; these will never produce.
- **Zero for want of a credential** — names the missing environment variable and
  where to get it free. Only appears when the key is genuinely absent.
- **Zero unexpectedly** — everything else. A source that *failed* has a network or
  endpoint problem; a source that returned *0 rows* has a filter or query problem.
  A source whose key **is** set and still returned nothing lands here, not in the
  credential group, because it is broken rather than waiting.

This matters more than usual on this build: the machine that wrote the last several
sources could not reach any external host, so eight NAGPRA notice families, the two
API-keyed feeds and seven curated wire feeds are all **schema-verified but not
live-verified**. The triage is how one run converts that into a short, specific fix
list.

### War dead and the disappeared → the wire, not the map

DPAA has no clean public API — its data sits behind a Salesforce-hosted app, and
scraping that would be fragile. But recovery of the war dead and of the disappeared
happens in Papua New Guinea, the Philippines, Vietnam, Bosnia, Guatemala, Colombia,
and **none of it appears in a permit register**, because no permit regime governs it.
Announcements are how it becomes public.

So seven bodies were added to the wire rather than the map: DPAA, CWGC, ICMP, ICRC,
IWGIA, Cultural Survival, Survival International. Twelve curated feeds now. Every URL
is **unverified** — the wire prints an item count per feed on every run, and a feed
reporting 0 across several runs should be deleted from `CURATED`.

### Objections → Regulations.gov comments

**The only structured source of opposition anywhere in the roster.** Every other
feed records what an authority *decided*; this records what someone *said about it*,
on the record, in time to matter. 20 million public comments, each attached to a
docket and a submitter.

`fetch_tribal_comments()` searches comments for NAGPRA, human remains, burial
ground, sacred site, tribal consultation and unmarked graves, then keeps those filed
by a **named** Indigenous, tribal or descendant body.

Two design calls worth arguing with:

**Plotted at the agency, not at the ground.** A comment is directed at a federal
decision-maker, and the decision-maker is the accountable actor. That means these
stack in Washington DC, which looks odd on a map and is nonetheless where the
decision sits. Guessing a location from the docket would invent geography.

**Identified from the title only.** The comment *list* endpoint returns a title but
not the submitter organisation — that needs a per-comment detail request, and at
1,000 requests/hour that would consume the entire budget. Regulations.gov titles
almost always read `Comment submitted by <Organization>`, so the organisation is read
from there. **Comments from individuals are therefore invisible**, which is a real
gap: a tribal member commenting personally will not appear. Only bodies with a name
do.

Verified against the filters: a tribe is kept, a mining corporation is dropped, a law
firm is dropped, an unmapped agency is skipped rather than guessed, and an untitled
comment is dropped.

Needs `REGULATIONS_API_KEY` (free at api.data.gov/signup, added as a repository
secret). Without it the source skips itself with an explanation.

### Litigation → CourtListener

`fetch_courtlistener()` queries the v4 search API for US case law mentioning
burial, repatriation, unmarked graves, Section 106 and ARPA. Cases are where the
rule changes: a permit register tells you a decision was made, a judgment tells you
whether the scheme that produced it survives.

Three deliberate limits:

- **Plotted at the court, not at the ground in dispute.** The court is the
  accountable actor and a judgment is not an unearthing.
- **`kind` is `review`, posture `watch`.** A filed case is an argument, not an
  outcome; the map must not imply a court has ruled when it has not.
- **An unmapped court is skipped, never guessed.** `CL_COURTS` holds the seats of
  the courts that actually produce this litigation. Dropping an unrecognised case
  on a country centroid would make it read as a located event.

Needs `COURTLISTENER_TOKEN` (free, from courtlistener.com, added as a repository
secret). Without it the source skips itself with an explanation rather than
hammering the anonymous endpoint from CI — the free allowance is small and it is
shared with everyone else using the service.

Queries are issued **one per phrase** rather than as a single long `OR`, which is
what the API documentation asks for: computation grows linearly with each `OR`.

### Cross-feed from the Live Projects map

`WelcomeToYourGalaxy/local-map` tracks ~268,000 pre- and post-permit projects
worldwide. `fetch_projects_crossfeed()` reads its `projects.json.gz` and keeps the
ones whose own text announces remains.

**The yield is tens of records, not thousands, and that is the finding.** Running the
full remains vocabulary over all 268k projects returns about 40 candidates, roughly
half of them place-name false positives — "La Grave", "Saint-Nicolas-de-la-Grave". A
project description rarely mentions burials even when the project will disturb them:
disturbance surfaces later, in the environmental statement, the salvage condition or
the stop-work order, not in the title at application time. So this feed catches the
projects that *announce* remains, which is a small subset of those that will
*encounter* them. The rest are only visible through the permit and notice registers
the other fetchers already read.

Because the base is huge and the signal thin, this fetcher does **not** reuse
`_is_remains()`. It requires an explicit multi-word phrase, which is what kills the
place-name matches: "grave" alone is a French village, "unmarked grave" is not. Two
further guards: routine cemetery estate work is rejected outright (an extension into
an empty paddock disturbs nobody), and a *memorial to* a burial ground is filed as
`review`/watch rather than as an accusation of harm.

Verified against the real 268k file: 6 records kept, 8 rejected as estate work, zero
place-name false positives, and the placement audit changes nothing — the gate is
already correct on everything it emits.

### Projects sitting on a burial ground → spatial intersection

`fetch_projects_on_burial_ground()` crosses every located project in the sibling
repo against the burial-ground layer this repo harvests, and flags anything within
`XSPATIAL_METRES` (default 250, env-overridable).

Cost: a naive 268,000 × ~1,000,000 comparison is 2.7 × 10¹¹ distance checks. Instead
the burial points go into a dict keyed by rounded lat/lng cell and each project tests
only the nine cells around it — linear in projects. Measured: 200,000 burial points
against 20,000 projects in 1.2 s.

Three things it is not, and the map says all three:

1. **Not a finding.** Output is `review`/`watch`, impact 2, and the description leads
   with *PROXIMITY FLAG, not a finding*. A resurfacing job beside a churchyard
   disturbs nobody.
2. **Not coverage.** The burial layer is OSM-derived and thin outside western Europe
   and North America. No hit is not no graves.
3. **Blind to the worst cases.** Unmarked burial grounds are absent from every layer —
   that absence is what *unmarked* means. It finds projects near **recorded** graves
   and misses projects over **forgotten** ones. Design note 9.

It returns nothing, with an explanation, until `remains_local_cemetery.json.gz`
exists — then it activates on its own.

### `removed-ground`: what it actually means

The kind label was **"Burial ground removed"** and the default record name was
**"Former burial ground"**, which said two contradictory things at once — one reading
as a removal under way now, the other as ancient history. It is neither.

OpenStreetMap contributors record, using a lifecycle prefix (`was:`, `demolished:`,
`removed:`, `abandoned:`), that a burial ground **was** at this spot and no longer is.
The source says nothing about **when** it went, or about what happened to the people
in it. It could be last year or two centuries ago.

So the label is now **"Burial ground no longer there"**, unnamed records are
**"Unnamed burial ground"** rather than "Former", and the description opens *Already
gone* and states plainly that the date is unknown and the fate of the people in it is
unrecorded.

### How `removed-ground` is drawn

These are **ground marks**, not event marks: a small hollow square in the facility
idiom with a faint slash, the size of a cemetery mark rather than an impact-scaled
dot.

The reason is proportion. On the live harvest, 317 of 1,896 records are
`removed-ground` — 17% of the map. Drawn as impact-scaled dots they looked exactly
like live permit applications, so a map that is mostly *things that already happened
at an unrecorded date* read as a map of *things happening now*. A burial ground that
vanished decades ago is standing context; there is nothing to intervene in.

**The blur is unchanged.** This is purely how the mark is drawn — `removed-ground` is
still coarsened by the placement gate like every other kind that identifies a burial
location. Making a mark look quieter must never make it more precise, and there is a
test asserting the render branch touches nothing but drawing.

### Resources group by intention, not by lens

The lenses are a **research taxonomy** — they answer "what kind of source is this".
Intention answers "what am I trying to do", which is the question somebody opening a
unit actually has. Six intentions now regroup all 172 entries across the twelve
lenses:

| Intention | Entries |
|---|---|
| Find out who is holding them | 27 |
| Get them home | 18 |
| Stop a disturbance | 52 |
| Change the rule itself | 27 |
| Find and identify the dead | 26 |
| Get help and check the work | 22 |

The regrouping is real rather than a rename. **"Stop a disturbance" gathers four
lenses into one place** — the regulator who issued the permit, the consultation duty
that was owed, the instrument it was issued under, and the injunction that could halt
it. Three of those live in different lenses; they are one job.

The lens is still shown as a chip on every entry, so nothing is hidden — you can see
that a given item came from *remedies* rather than *regulators* if that matters.

### The harvest runs its sources in parallel

Sources are independent and almost entirely I/O-bound: each spends its life waiting
on somebody else's server. Run sequentially the harvest costs the **sum** of every
source's latency; run concurrently it costs roughly the **slowest one**.

**Nothing is dropped, no query narrowed, no budget cut** — identical work,
overlapped. Output order is fixed to source order regardless of completion order, so
two runs of the same data produce the same file. Measured on a harness: 5.9× faster.

Two details worth knowing. `contextlib.redirect_stdout` swaps `sys.stdout`
process-wide, so with real threads one source's redirect captures another's output;
logs are routed through a per-thread buffer instead and printed in source order, or
the per-source diagnostic — this harvester's main debugging tool — would come out
shuffled. And `HARVEST_WORKERS` is deliberately modest at 6: several of these are
small government servers, and this harvester should not be why one falls over. Set it
to 1 to go sequential when debugging a single source.

### Per-jurisdiction how-to guides → `guides.json`

Nine guides, keyed to the same service areas as the resources, so they surface when
you open that unit — and shown **above** the resources. A list of offices tells you
who exists; a guide tells you which window is open and how long you have. The second
is what somebody opening a unit in a hurry needs first.

| Jurisdiction | Statutory hook |
|---|---|
| USA (federal projects) | NAGPRA 43 CFR Part 10; NHPA §106, 36 CFR Part 800 |
| USA (getting ancestors back) | NAGPRA inventories, summaries, notices |
| New South Wales | NPW Act 1974 s.86–s.90, AHIP |
| Western Australia | Aboriginal Heritage Act 1972 s.17–s.18 |
| England | Burial Act 1857 s.25 |
| Scotland | Burial and Cremation (Scotland) Act 2016 |
| Aotearoa New Zealand | Heritage NZ Pouhere Taonga Act 2014 s.42–s.48 |
| Canada | Provincial statutes; Impact Assessment Act |
| España | Ley 20/2022 de Memoria Democrática |

**Nine, not seventy-six.** The projects map ships 76 country guides; this ships nine,
because a guide exists only where the lens work already gave a *verified* statutory
hook. A thin guide that names the wrong office is worse than no guide — somebody acts
on it and loses a window.

Deadlines are called out separately and in the harm colour, because deadlines are what
kill these: 21 days on an English planning objection, 30 days on a NAGPRA claim window,
30 days of statutory protection after a discovery on federal land.

Each guide ends with what the procedure will not do for you — §106 is a process
requirement, not a preservation requirement; an AHIP register is a record of permitted
destruction; Ley 20/2022 changed the duty without by itself changing the pace.

### The `reform` lens — changing the law, not fighting one incident under it

`remedies` answers "how do I stop this dig". `law` lists the instruments. Neither
answers the larger question: the rule itself is the problem, so how is it changed?

23 entries covering four different crafts:

- **Regional human rights courts** — Inter-American Court and Commission, African
  Commission, ECtHR. The Inter-American system produced the strongest Indigenous
  land and cultural-rights jurisprudence anywhere, and it is cited well outside the
  Americas.
- **UN treaty bodies** — individual complaints, plus the two fast routes that matter
  here: CERD early warning and urgent action, which does **not** require exhausting
  domestic remedies, and Article 30 urgent actions on the disappeared.
- **Strategic litigation practice** — organisations that choose a case for its
  precedent rather than its client, and the databases that tell you whether the
  argument has been run before. Includes the risk nobody advertises: a badly chosen
  case that loses sets the rule against everyone who comes after.
- **Legislative tracking** — Congress.gov, UK Parliament bills, NCSL. Amendments to
  NAGPRA, NHPA and the Burial Act regime all pass through these, with short comment
  windows.

Also flagged: **CalNAGPRA**, California's statute, which reaches institutions the
federal act does not and sets harder deadlines — the working proof that a state can
legislate above the federal floor.

18 entries carry a verified URL; 5 are search strings where I was not confident
enough to publish a link.

### Coverage is Anglophone, and the reason is structural

Of the records currently held, **about 83% are Australia, the United States and the
United Kingdom** — New South Wales alone is roughly half the map. Continental Europe
is thin; Latin America, Africa and Asia are close to absent.

Sources were built against **registers**, and registers of burial disturbance exist
mainly where a settler state built a permitting bureaucracy for it. NSW alone
outweighs every non-Anglophone country combined — not because more happens there, but
because NSW publishes an ArcGIS layer of every permit.

The correction is not a better crawler. It is a different class of source: the news
wire (already 21 languages across 39 locales), court filings, NGO reporting and UN
submissions — none of which require a state to have built a database first. The
portal federations are the largest already-built lever and have not yet completed a
run; they are where Spain, France, Italy, Brazil, Mexico and Indonesia would come
from. Design note 11.

### Forensic recovery, in full

Forensic exhumation runs on the opposite logic to heritage archaeology: heritage asks
whose ancestors these are and where they belong; forensic asks who killed them and
whether it can be proven — digging as evidence-gathering, under chain of custody, for a
court or a truth commission.

**Both are in scope, and single cases are included.** They carry their own kind,
`forensic-case` / *Individual forensic recovery*, so they can be shown with mass graves
or filtered apart. Collapsing them would hide both — a hundred single cases in one
district is a pattern a mass-grave-only map would never surface.

`_is_individual_case()` no longer excludes; it **labels**. It runs in `_finish()`, so it
applies to every source current or future, and it tests the record's shape rather than
its subject: a plural or group noun overrides, so "missing person case leads
investigators to a mass grave" stays `mass-grave` while "remains identified as missing
woman" becomes `forensic-case`. Set `EXCLUDE_INDIVIDUAL_CASES=1` to return to
group-only.

Individual cases are blurred like every other recovery location and sit at the lowest
scale floor, so one case never outweighs a grave holding forty people.

This also re-opens the coroner and unidentified-remains registers — NamUs and its
equivalents — which had been left off the roster only because of the group-only rule.

### Plain-language glossary

28 specialist terms — repatriation, disposition, provenance, deaccession, NAGPRA,
THPO, Section 106, AHIP, MNI, wāhi tapu, posture, coarsened, ADM1, proximity flag and
the rest — each with a definition written for someone who has never read a
repatriation notice. Shown on hover or tap wherever the word appears, and listed in
full under **Plain-language glossary** in the map key.

One rule enforced by test: **no term is defined using another term from the list.** A
glossary that explains "repatriation" with "funerary objects" has not explained
anything.

### The cemetery sweep: small jobs, not longer ones

The 2026-07-31 run died at a 180-minute timeout with a 160-minute budget. The
instinct is to raise the timeout — the hosted-runner ceiling is 360, so 180 was
self-imposed. But a longer job only means **more work at risk** when one eventually
does fail.

The real lever is shard count. Total work is fixed and so is wall clock: 8,352 tiles
take the same time cut into 16 pieces or 64, because the shards run in waves either
way. What changes is the size of **one job**.

| shards | tiles/job | at 18s/tile | at 35s/tile | wall clock |
|---|---|---|---|---|
| 16 | 522 | 157 min | 304 min | ~5 h |
| **64** | **130** | **39 min** | **76 min** | **~5 h** |

At 64 shards no job is close to its 120-minute timeout even at the worst rate
observed, and no single failure costs more than ~1% of the sweep — which GitHub lets
you re-run on its own.

That removes the reason the resume machinery existed. It stays as a **backstop**: a
shard that does fail still banks its tiles. On a healthy run every shard completes,
`done` clears, and none of its side effects apply.

The one failure small jobs do not fix is **silent staleness**: if a shard failed
persistently, `done` would never clear, later runs would keep skipping banked tiles,
and the layer would look healthy while quietly never picking up a new cemetery again.
The sweep now records when it started banking, and a marker older than
`DONE_MAX_AGE_DAYS` (14, i.e. two weekly sweeps) is discarded with a message —
resuming is no longer what is happening, so it restarts.



The 2026-07-31 run was **cancelled at the 180-minute job timeout with a 160-minute
harvest budget**. One slow tile ate the 20-minute margin, the job was killed before
the write, and all 16 shards produced nothing: three hours of querying, zero
artifacts.

Shrinking the budget only trades coverage for the same fragility. So the sweep no
longer has to finish in one run. Every tile a shard completes is banked in
`osm_empty_tiles.json` under `done`, committed with the layer, and skipped next time.
A run that gets a third of the way through banks a third; three runs finish the
sweep. **Progress is monotonic instead of all-or-nothing.** When every shard
completes, `done` clears, so the next sweep starts fresh rather than never
re-checking anything.

Budget is now **110 minutes inside a 165-minute job** — 55 minutes of margin for a
slow tile, the write, gzip and upload.

One trap worth recording: the memo is written **per shard**
(`osm_empty_tiles_part7.json`) and unioned by the merge job. Sixteen shards uploading
the same filename under `merge-multiple: true` collide, last writer wins, and fifteen
shards' progress vanishes — which would have made the whole resumable design a no-op
while appearing to work. Each shard sweeps a disjoint sixteenth of the grid, so the
union is exact.

### The facility layers are tiled, not global files

One global file per type worked at 17,000 cemeteries. It does not work at half a
million: roughly **7 MB gzipped and ~40 MB of JSON parsed on every page load**,
which is slow on a desktop and fatal on a phone.

Any type over 40,000 rows is published as a grid of 10° cells —
`remains_local_cemetery_50_0.json.gz` and so on — described by
`remains_local_manifest.json`. The map reads the manifest once, then fetches only
the cells its viewport touches, and again on every pan or zoom that brings a new
cell into view.

Measured against a simulated 590,000-row sweep: **478 tiles, 7.0 MB total, largest
tile 0.28 MB**. A London view at zoom 9 pulls 2 tiles and 0.54 MB. A mid-Pacific
view pulls **nothing at all** — empty cells are simply absent from the manifest and
are never requested, so there is no 404 probing.

Small types stay single files. Crematoria number about 119 worldwide; a manifest
lookup plus a second request would cost more than the file does. Both shapes are
handled in the map, so the harvester can switch a type between them without any
change on the front end.

`prune_tiles.py` runs after each merge and deletes tiles the new manifest no longer
lists. Without it, a cell that empties out would keep serving its old rows forever —
invisible to readers, because the map only requests what the manifest advertises,
but immortal in git. It only removes files matching the published naming scheme and
absent from the manifest; anything it cannot account for is left alone.

### Museums in the facility layer

A fourth facility type, and a different kind of layer from the other three. Cemeteries,
crematoria and mortuaries receive the dead by arrangement; a museum may be holding
ancestors it was never given permission to hold. It is the potential-holder layer —
the physical counterpart to the `holding` records.

An OSM `tourism=museum` tag says nothing about whether that museum holds remains, and
most do not, so the layer is **not a claim** — it is the set of institutions a
researcher would have to ask. Museums draw hollow and dashed rather than solid,
because a solid mark would read as *remains are here*.

The archaeological refusal had to be scoped for this: it rejects any `historic=*`
outright, which is correct for burial grounds but would have deleted most museums,
since a museum in a listed building carries `historic=building`. Museums are now
refused only on burial-site *values* (`historic=tomb`) and lifecycle prefixes.

### Two new wire topics: opposition, and the trade

`opposition` requires **both** a named objector (tribe, first nation, iwi, traditional
owner, THPO, land council, descendant community…) **and** ancestors at stake (burial,
grave, remains, sacred site, wāhi tapu…). Both halves are needed because either alone
misclassifies badly: "tribe" catches a nation's own housing project, "opposes" catches
every planning row on earth. Verified against both traps — "Fort Nelson First Nation
Aggregate Pit" does *not* classify as opposition, and neither does "tribe opposes casino
over water quality".

`trade` covers the live market: auctions and online listings, customs and INTERPOL
seizures, smuggling. Small volume, high signal, and the only feed covering a
present-tense harm rather than a historical one. Sourcing is press-driven rather than
register-driven, so coverage is patchy by nature.

Both sit **above** `development` and `desecration` in the rule order, because a nation
objecting to a pipeline is an opposition story that happens to mention a pipeline.

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

**Opening a unit** does two things. It shows what the harvest puts inside it — a
real point-in-polygon test against the record set, holes included, not a bbox
approximation — and it descends into that unit's own sub-units.

CGAZ carries no parent field; every feature has only a `shapeName`. So children
cannot be selected by attribute. They are selected **geometrically**: fetch the
country's next level and keep the units whose centroid falls inside the polygon you
clicked. That is what turns a flat national file into a real country → state →
county → municipal descent.

### Resources are attached by service area

Every entry in `lenses.json` carries a `serves` list — `GLOBAL`, an ISO3, or
`ISO3/UnitName` for one ADM1 unit. Opening a unit shows the bodies whose remit
covers that jurisdiction, deepest first, then the country, then everywhere.

**The rule is jurisdiction of service, not physical address.** The National NAGPRA
Program has an office in Washington DC and administers the statute for the whole
United States, so it sits at the country level; filing it under DC would be true
about the building and useless about the law. Heritage NSW serves one state and
sits in New South Wales alone. A body serving three states is listed in all three
— the Sámi Parliaments appear under Norway, Sweden and Finland.

Same model as the projects map's per-unit tree, expressed as a field on the entry
rather than a second nested file, so there is one source of truth and nothing to
keep in sync.

**Museums are the deliberate exception**, flagged `located: true` and labelled
*located here* in the panel. A collection is not a service area — it is a place
holding ancestors taken from elsewhere. Pretending it "serves" the jurisdiction it
stands in would misdescribe the thing this map exists to make legible.

Current spread: 40 distinct service areas across 156 entries — 47 global, 30 at US
federal level, and ten ADM1 units (New South Wales, Western Australia, Victoria,
England, Scotland, Wales, Hawaii, Alaska, Illinois, Massachusetts). **All ten were
verified to match CGAZ's `shapeName` exactly**; `GB_ALIAS` reconciles any that
don't as entries are added.

### The wire → `wire.json`

The region menu lists **every one of the 151 countries the wire can tag**, named in
full, not only the ones with an article in the current batch. It used to build its
options from the articles themselves, so a country with nothing that day disappeared
from the list and read as *not covered* rather than *nothing today* — and it labelled
each option with the bare ISO code.

Countries with no items are kept and grouped separately (`Nothing right now`) rather
than hidden, because "no items" and "not in scope" are different facts and the reader
should be able to tell which one they're looking at. An article tagged with a code the
table doesn't know gets its own group instead of being silently dropped.

The name table came from the projects repo's `_WIRE_REGIONS` (212 countries, ISO3)
reduced through its `_A2TO3` — but that pair only covers 102 of the 151 codes this
wire emits, so the remaining 49 were completed by hand.



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

## 5. Lenses — tranche 4

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
