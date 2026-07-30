# Live Global Unearthings Map

A worldwide map of **unearthings and decisions about unearthed human remains** —
repatriation notices, permits authorising harm to burial grounds, environmental reviews
that admit graves are in the way, burial grounds recorded as removed — plus a directory
of the world's cemeteries, a news wire narrowed to this subject, and a set of curated
lenses pointing at the institutions that hold, regulate, recover and litigate.

Part of [Welcome to Your Galaxy](https://welcometoyourgalaxy.com). Sibling of the
[Live Global Project Map](https://github.com/WelcomeToYourGalaxy/local-map), which maps
what is about to be built. This one maps whose dead are in the way.

Every mark links back to a primary public record. Nothing is invented.

---

## This map does not plot graves

That is the whole design, so it goes first.

Precise burial locations are withheld by archaeologists, descendant communities and
states because publishing them invites desecration. In the United States, site-location
data is exempt from disclosure under **NHPA §304** and **ARPA §9** for exactly that
reason, and NAGPRA notices themselves name only counties of removal, never coordinates.

So the unit of this map is **the accountable actor and the decision**, not the grave:

| Record | Plotted at |
|---|---|
| A repatriation notice | the institution holding the remains |
| A permit to harm a burial site | the centre of the permit area |
| An environmental or planning review | the administrative unit it names |
| **Anything that is itself a burial location** | **blurred to about 5 km** |

The blur is enforced by a gate keyed to the *record type*, not to the good intentions of
whoever wrote the fetcher: hand it exact coordinates for a mass grave and it blurs them
anyway. Unknown record types fail safe to blurred, and an audit pass re-checks every
record before the file is written. Blurred records are drawn as a **halo with no
centre**, so the imprecision is visible on the map rather than buried in a footnote.

Two things follow that look like inconsistencies and are not:

- **The cemetery directory is not blurred.** A working cemetery is a signposted public
  place with a street address that people need to find. Its harvester refuses every
  archaeological tag and every lifecycle prefix, so archaeological and unmarked burial
  sites never enter it.
- **Registers of site locations are refused entirely.** Not blurred — refused, before a
  point is read. Blurring hides one grave in the noise; it does not protect a register
  of thousands, and for *wāhi tapu* and comparable sacred places that disclosure is the
  harm.

The full reasoning, including how to overrule any of it, is in
[SOURCES.md](SOURCES.md) and in the map's own **Design notes** panel.

---

## What's here

| File | Does |
|---|---|
| `index.html` | The map. Self-contained, no build step. |
| `harvest_remains.py` | Unearthings and decisions → `remains.json.gz` |
| `harvest_cemeteries.py` | Global burial directory from OSM → `remains_local_*.json.gz` |
| `wire_remains.py` | Topic news, per region → `wire.json` |
| `check_lenses.py` | Link-checks the lenses → `lenses_status.json` |
| `lenses.json` | 156 curated resources across 12 lenses |
| `SOURCES.md` | Every source: verified, pending, or refused, with reasons |
| `DEPLOY.md` | Setup: permissions, Pages, run order, failure modes |

Harvesters are pure standard library and run on GitHub Actions, not locally. The one
third-party import (`feedparser`, in the wire) is optional and degrades to a built-in
parser.

**Start with [DEPLOY.md](DEPLOY.md).** The one thing that must happen first: set
**Settings → Actions → General → Workflow permissions → Read and write**, or every run
will do its work and then fail at the push.

---

## Sources

Verified and in production: US NAGPRA notices and federal burial reviews (Federal
Register API), NSW Aboriginal Heritage Impact Permits (CC-BY, ArcGIS), California CEQA
filings, UK planning applications, OpenStreetMap, and burial and cemetery datasets found
across CKAN, DKAN, OpenDataSoft and GeoNode government portals in the languages those
portals publish in.

A source ships only after its URL, its field names and its licence have been checked
live. Anything unverified returns zero rows and prints why, so the gaps stay legible
instead of disappearing. Ten such registers are named in `SOURCES.md` — Western
Australia's s.18 consents, Spain's Mapa de Fosas, Ireland's excavation licences, EAMENA,
Colombia's UBPD, Mexico's CNB, the MoJ licence list, ICMP and others — each with what
specifically blocks it.

---

## What the numbers can and cannot tell you

- **No coverage percentage is claimed.** No dataset holds the true global count of
  unearthings, so any figure would be invented.
- **Counts of individuals are never estimated.** A number appears only where a source
  states one. Absence means the source was silent, never that the number is small.
- **Coverage follows statutory regimes.** Countries with repatriation law and
  heritage-permit systems generate records; countries without them generate silence.
  **Sparse regions on this map are sparse in the records, not necessarily in the
  ground.** The same caution applies to the wire: an empty wire for a region is a gap in
  reporting.
- **Direction is separate from size.** A repatriation and a permit to destroy are both
  here. `posture` carries direction — harm, watch, redress, unlawful — and `impact`
  carries magnitude only. A large repatriation is a large event, not a bad one.
- **One cause is invisible to this map by construction.** Every register harvested is a
  consent or compliance regime: someone applied, someone decided, someone filed. Erosion,
  permafrost thaw and wildfire unearth remains with no permit, no applicant and no
  accountable party, so nothing records them. That is not a missing feed and adding one
  would not fix it.

---

## Attribution

- Cemetery, crematorium, mortuary and removed-burial-ground layers: © OpenStreetMap
  contributors, **ODbL 1.0**.
- NSW Aboriginal Heritage Impact Permit boundaries: NSW Department of Climate Change,
  Energy, the Environment and Water, **CC-BY**. Some permits are absent from the spatial
  layer; the AHIP Public Register is authoritative.
- Basemap imagery: © Esri.
- US federal records are public domain.
- Per-source licences and caveats are in `SOURCES.md`.

We acknowledge the descendant communities whose ancestors appear in these records as
line items in someone else's inventory. This map exists to make that legible, and it is
built to avoid becoming another way to disturb them.
