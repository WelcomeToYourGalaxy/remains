# DEPLOY — WelcomeToYourGalaxy/remains

Order matters in one place only: **Actions write permission must be set before you run
any workflow**, or every run will do its work and then fail at the push.

---

## 1. Put the files in place

```
/                                repo root
  README.md
  index.html
  weebly-embed.html               paste-in embed for the Weebly site
  harvest_remains.py
  harvest_cemeteries.py
  wire_remains.py
  check_lenses.py
  lenses.json
  SOURCES.md
  DEPLOY.md
  .nojekyll                      empty file, leading dot required

  .github/workflows/
    remains.yml
    remains_federations.yml
    cemeteries.yml
    wire.yml
    lenses.yml
```

Commit and push to `main`.

Nothing else is needed — the harvesters are pure standard library, and the one
third-party import (`feedparser`, in the wire) is optional and installed by its own
workflow.

---

## 2. Give Actions permission to push — do this before running anything

**Settings → Actions → General → Workflow permissions → Read and write permissions →
Save.**

Every workflow here commits its output back to the repo. The workflows already declare
`permissions: contents: write`, but that declaration can only narrow what the repo
allows, never widen it. On a repo left at the default *read-only* setting, a run will
harvest for hours, print a correct diagnostic, and then die on `git push` with a 403.

---

## 3. Turn on Pages

**Settings → Pages → Source: Deploy from a branch → Branch: `main`, folder: `/ (root)`
→ Save.**

The site appears at `https://welcometoyourgalaxy.github.io/remains/` within a minute or
two. It will show the honest empty state until the first harvest lands — that is
correct, not a failure.

`.nojekyll` matters here. Without it Pages runs the tree through Jekyll, which skips
paths beginning with `_` and can interfere with how `.gz` assets are served. The name
needs the leading dot; `local-map` has one called `nojekyll` that does nothing.

---

## 4. Run the workflows, in this order

**Actions → pick the workflow → Run workflow → Run workflow.**

| # | Workflow | Runtime | What you get | Skippable? |
|---|---|---|---|---|
| 1 | **Harvest live unearthings** | 20–60 min | `remains.json.gz` — the dot layer. **The map works from here.** | No |
| 2 | **Check lens links** | 3–6 min | `lenses_status.json` — lens badges stop saying "unverified" | No |
| 3 | **Harvest unearthings wire** | 15–40 min | `wire.json` — the wire panel | No |
| 4 | **Harvest unearthings portal federations** | 2–5 h, 6 shards | global depth on the dot layer | Yes, for now |
| 5 | **Harvest cemeteries** | 2–3 h, 24 shards | the three facility files | Yes, for now |

Reload the map after each. Then leave them on their schedules — daily for the dots, six-hourly for the wire, twice-weekly for the federations, Saturdays for the cemeteries, Mondays for the link check.

### What "working" looks like on run 1

Open the run log and read the diagnostic block at the end of **Harvest live
unearthings**. It prints a per-source row count, then a `ZERO-YIELD (review)` line.

Expect these to be zero, by design — they are the documented pending roster and each
prints its own reason: `wa_section18`, `qld_chmp`, `spain_fosas`, `ireland_excavation`,
`eamena`, `colombia_ubpd`, `mexico_cnb`, `nps_nagpra_grid`, `uk_moj_licences`, `icmp`.

`nps_nagpra_grid` is the exception: it is no longer a stub but a **discovery fetcher**.
Read what it prints. Either it says `FOUND Inventory -> ...` and the source is live, or
it tells you to open the network tab on `apps.cr.nps.gov/nagprapublic/Home/Inventory`
and set `NPS_GRID_PATHS` in `remains.yml` to the URL the grid actually calls. No code
change needed either way.

Investigate if any of these is zero: `nagpra_notices`, `nsw_ahip`,
`us_burial_reviews`. Those are the verified feeds; a zero there means an endpoint moved
or a field name changed, not that there was nothing to find.

The diagnostic also prints `refused on policy: N dataset(s) that are site registers,
not events`. A non-zero number there is the system working — those are registers of site
locations the harvester declined to publish. See section 3a of `SOURCES.md`.

`nagpra_notices` should be the largest single source. If `geocoder calls used` equals its
budget, raise `GEO_BUDGET` in `remains.yml` — institution placement will have fallen
back to state centroids for the overflow.

---

## 5. Embed it in the Weebly site

Paste **`weebly-embed.html`** into a single Weebly **Embed Code** element. Nothing
to upload, nothing hosted anywhere but your own repo, and you never paste it again
— the map updates itself on every push.

The one line to check is the branch:

```js
var REPO = "https://raw.githubusercontent.com/WelcomeToYourGalaxy/remains/main/";
```

### Why it isn't just the file, or just an iframe to Pages

Pasting `index.html` straight into Weebly breaks in two ways that are easy to
misdiagnose:

1. **Relative data paths.** The map fetches `remains.json.gz`, `wire.json`,
   `lenses.json` and the cemetery files as relative paths. On a Weebly page those
   resolve against `yoursite.weebly.com` and 404 — you get the shell, no data, and
   no visible reason. The embed injects a `<base href>` pointing at the repo, so
   every relative fetch inside the map resolves to GitHub. **The map's own code is
   untouched**, so it still works unchanged when served from Pages, where those
   same relative paths already resolve correctly.
2. **CSS bleed.** The map sets `html`/`body` to `100vh` with `overflow:hidden` and
   positions eight fixed panels. Dropped into a Weebly page that fights the theme
   and can swallow the rest of it. The embed runs the map inside an iframe via
   `srcdoc`, so it gets its own document — its CSS cannot reach your site and your
   theme cannot reach it.

It reads the map over `raw.githubusercontent.com`, which sends
`access-control-allow-origin: *`, so this works cross-origin from Weebly and does
**not** need Pages enabled. The repo must be public — a private repo cannot be
read this way.

### Sizing

`HEIGHT_VH` is a percentage of the browser window (88 by default) with a
`MIN_PX` floor of 560. Give it real height: the panels run floor to ceiling.
Below ~1180 px wide the map key and entry box drop out; below ~860 px the wire and
lenses drop too and the filters stay. Both are handled inside the map, so the
embed needs no breakpoints of its own.

### If the frame shows a message instead of a map

It tells you which of these it is: the file isn't committed to that branch yet,
the branch in the snippet isn't your default branch, or the repo is private. It
also offers a direct link so you can confirm by eye.

## 6. Things that will go wrong, and what they mean

**Everything harvests, then `git push` fails 403.** Step 2 was skipped.

**Federation sources return nothing and the log says it could not fetch the sibling
registries.** `harvest_remains.py` reads the ~2,200-portal lists from
`local-map/harvest_projects.py` at run time. If that repo is renamed or made private,
set `SIBLING_HARVESTER_URL` in `remains.yml`. Every other source is unaffected — that
is why the fetch is allowed to fail quietly.

**A cemetery shard times out.** Expected. Overpass answers a silent server-side timeout
with HTTP 200 *plus* a `remark` field; the harvester treats that as failure and
quarter-splits the tile. `fail-fast: false` means one bad shard cannot take the others
down, and the merge job runs `if: always()`.

**The merge job warns a facility file is over 45 MB compressed.** Split that set by
continent before it reaches GitHub's 100 MB hard limit. The warning fires early on
purpose.

**A facility layer says "no file yet".** Run 5 hasn't happened. The layers load lazily
on toggle and a missing file is a normal state.

**The wire flags a curated feed that kept nothing.** Delete it from `CURATED` in
`wire_remains.py`. Those five are unverified by design; a feed that stays empty is
decoration.

**The link check reports redirects.** Not failures, but each is a one-line
`lenses.json` edit so the map links straight through instead of bouncing. Government
agencies reorganise constantly — expect a few every few months.

**A lens entry shows "gone".** Kept and struck through deliberately. An office that
closed is information. Replace it when you know what replaced it.

---

## 7. First real maintenance pass

In rough order of value:

1. **Work the link-check output** — redirects into `lenses.json`, then anything `gone`.
2. **Confirm or supply the NPS NAGPRA endpoint.** The fetcher now probes for it, so
   check run 1's log first. If it found nothing, open the network tab on
   `apps.cr.nps.gov/nagprapublic/Home/Inventory` and paste the grid's URL into
   `NPS_GRID_PATHS`. This is worth doing early: it carries per-institution counts of
   individuals still held, which nothing else does, and it fills the `holding` record
   type that currently has no source.
3. **Check the WA DPLH spatial licence.** If reuse is permitted, s.18 consents are the
   second-largest harm-permit feed after NSW.
4. **Prune `CURATED`** once the wire has run a few times.
