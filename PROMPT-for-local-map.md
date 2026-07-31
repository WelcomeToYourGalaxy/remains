# Prompt for the local-map chat

Paste this as-is.

---

Four defects in `WelcomeToYourGalaxy/local-map`, found by auditing the repo against
the sibling `remains` repo. Fix all four.

**1. `projects.yml` can silently lose a whole harvest, and the other three
workflows can too.**

`projects.yml` pushes with a bare `git push` — no retry. `projects_federations.yml`,
`projects_osm.yml` and `wire.yml` retry with `git merge -X ours FETCH_HEAD`, which
does **not** help: `-X ours` resolves conflicting hunks *inside* a file and does
nothing for a modify/delete **tree** conflict. So if `projects.json` is ever deleted
or moved on the remote while a job is rebuilding it, every retry hits the identical
`CONFLICT (modify/delete)` and the job exits 1, discarding the harvest. This exact
failure cost the `remains` repo a completed 1,887-record run.

These jobs regenerate their file wholesale, so there is nothing to merge. Replace the
commit step in **all four** workflows with: take the remote as it is, drop the freshly
built file on top, commit that.

```yaml
      - name: Commit
        env:
          FILES: "projects.json projects.json.gz"   # per workflow
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          if [ -z "$(git status --porcelain -- $FILES)" ]; then
            echo "No change in $FILES -- nothing to commit."; exit 0
          fi
          KEEP="$(mktemp -d)"
          for f in $FILES; do [ -f "$f" ] && cp "$f" "$KEEP/$(basename "$f")"; done
          BR="$(git rev-parse --abbrev-ref HEAD)"
          for i in 1 2 3 4 5; do
            git fetch origin "$BR" || true
            git reset --hard "origin/$BR"
            for f in $FILES; do
              [ -f "$KEEP/$(basename "$f")" ] && cp "$KEEP/$(basename "$f")" "$f"
            done
            git add -- $FILES
            if git diff --staged --quiet; then
              echo "identical to remote -- nothing to push"; exit 0
            fi
            git commit -m "chore: refresh $FILES"
            if git push origin HEAD:"$BR"; then echo "pushed"; exit 0; fi
            echo "push race, retry $i"; sleep $((i * 5))
          done
          echo "push failed after 5 retries"; exit 1
```

**2. Delete `harvest_projectS.py`.**

It is a stale 63 KB copy of the 531 KB `harvest_projects.py` — 39 functions against
137, and its own docstring calls itself `harvest_projects.py`. The only difference in
the filename is the capital S, so on macOS or Windows the two collide in a checkout
and git reports the working tree permanently dirty. Confirm nothing references it,
then remove it.

**3. Stop committing `projects.json`, and add a `.gitignore`.**

`projects.json` is committed at **83 MB** alongside the 10.7 MB `projects.json.gz`.
That is past GitHub's 50 MB warning, heading for the 100 MB hard limit, and every
clone pays for it forever — including after it is deleted, because it stays in
history. The map only reads the `.gz`.

- Add `.gitignore` (the repo has none) containing at least `projects.json`,
  `__pycache__/`, `*.pyc`, `fed_part_*.json`, `osm_part_*.json`.
- `git rm --cached projects.json`.
- Make sure the workflows only `git add` the `.gz`.
- To reclaim the history, `git filter-repo --path projects.json --invert-paths` —
  but that rewrites history, so only if nothing else depends on these hashes.

**4. Rename `nojekyll` to `.nojekyll`, and move the stray `wire.json`.**

The file at the repo root is `nojekyll` with no leading dot, so GitHub Pages ignores
it and still runs Jekyll — which means any path starting with an underscore is not
served. Rename to `.nojekyll`.

There is also a 5 KB `wire.json` inside `.github/workflows/`. A data file in the
workflows directory is either a stray or a path bug in whatever wrote it; find out
which and move or delete it. The real one is the 2.2 MB `wire.json` at the root.

---

After the fixes, run each workflow once and confirm the commit step prints `pushed`
or `nothing to push` rather than `push race`.
