# JOBWATCH

A self-hosted, zero-cost job-posting watcher. It polls a fixed set of companies'
Applicant Tracking System (ATS) APIs on a schedule, scores each posting against a
rigorous **weighted keyword system (no LLM)**, and posts matches to a Discord
channel via webhook. Runs free on GitHub Actions. Dedup via a SQLite file
committed back to the repo.

Targeted at a **hardware / firmware** and **autonomy / robotics / sensors**
candidate: embedded/firmware, PCB & signal integrity, radar/LiDAR DSP, robotics
perception, and GPU-compute-for-perception (CUDA/Jetson) — **not** ML modeling.
Scope is deliberately narrow: **summer engineering internships**, **electrical /
computer** relevance required (no mechanical/civil/manufacturing/PM roles, ML/
data-science roles excluded by title), located in **NYC or the California Bay
Area / LA area**.

## How it works

1. **Fetch** (`fetch.py`) — adapters for Lever, Greenhouse, Ashby, and Workday
   return a normalized job dict. One dead company logs an error and returns `[]`;
   it never crashes the run.
2. **Score** (`match.py`) — the deterministic engine:
   - **Hard gates** run first: title exclusions (seniority, ML/data-science,
     non-EE/CS disciplines like mechanical/civil, and non-engineering roles like
     management), an **intern-only** gate (title must say intern/internship/
     co-op, plus "N+ years" detection), a **summer** season gate, and a location
     allowlist scoped to NYC + CA Bay Area + LA area.
   - **Weighted scoring**: `core` (5) / `strong` (3) / `supporting` (1) keyword
     categories, with title hits worth 2× body hits and body hits capped per
     category so keyword-stuffed JDs can't dominate. Word-boundary/phrase aware
     (no naive substring), with aliases collapsing to a canonical term.
   - Anything `>= min_score` is **strong**; anything `> 0` and below is
     **partial** (still sent, tagged 🟡). Noise below the domain floor is dropped.
3. **Route** (`match.py` + `config.yaml → routes`) — each match is classified
   into one Discord channel by topic. Classification is deterministic: every
   keyword is owned by exactly one channel, and a job routes to the channel with
   the highest **title-weighted** keyword score (title hits decide first; body
   breaks the rest; ties use the `priority` order). The three channels:
   - **autonomous_driving** — AV / robotics / perception / sensors / planning
   - **hardware** — silicon, RTL/FPGA/ASIC, comp-arch, PCB, RF, signal integrity
   - **software_and_firmware** — firmware / embedded / drivers / motor control
4. **Dedup** (`store.py`) — SQLite `seen(id, first_seen)`.
5. **Notify** (`notify.py`) — one Discord embed per job (batched ≤10), posted to
   the routed channel's webhook, showing Company / Location / Score /
   **Matched terms** (the transparency mechanism).

Everything — companies, keywords, weights, gates — is tunable in `config.yaml`
with no code edits.

## Setup (5 minutes)

1. **Create three Discord webhooks** — one per channel (Server Settings →
   Integrations → Webhooks → New Webhook → copy each URL):
   - autonomous driving systems engineering
   - hardware
   - software and firmware
2. **Push this repo** to GitHub.
3. **Add three secrets**: repo → Settings → Secrets and variables → Actions →
   New repository secret, for each channel:

   | Secret name | Channel |
   |-------------|---------|
   | `DISCORD_WEBHOOK_AUTONOMY` | autonomous driving systems engineering |
   | `DISCORD_WEBHOOK_HARDWARE` | hardware |
   | `DISCORD_WEBHOOK_SWFW` | software and firmware |

   *(No LLM key. `DISCORD_WEBHOOK_URL` is an optional fourth secret — a fallback
   used for any route whose specific secret is unset. The env-var name for each
   channel lives in `config.yaml → routes.channels.<name>.webhook_env`.)*
4. **Seed the DB once** so the first real run doesn't flood you: Actions tab →
   `jobwatch` → Run workflow → mode `seed`. This marks all current matches seen
   without notifying.
5. Done. The two daily crons (08:00 and 16:00 America/Los_Angeles) will now post
   only *new* postings.

## Fixing a wrong company slug

Many slugs in `config.yaml` are marked `(VERIFY)` — best guesses. Every run
prints a per-company job count:

```
  Zoox                      41 jobs
  Nuro                      0 jobs        <-- wrong slug or wrong ATS
  Anduril         [DEFENSE]  120 jobs
```

If a `(VERIFY)` company shows **0 jobs** or an error, open its real careers page,
find the ATS in the URL, and fix the slug:

- `jobs.lever.co/<slug>` → `ats: lever, slug: <slug>`
- `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` →
  `ats: greenhouse, slug: <slug>` (the API host is always `boards-api.greenhouse.io`)
- `jobs.ashbyhq.com/<slug>` → `ats: ashby, slug: <slug>`
- `<tenant>.wdN.myworkdayjobs.com/<site>/...` →
  `ats: workday, tenant: <tenant>, wd_num: N, site: <site>`

## Local usage

```bash
pip install -r requirements.txt

python main.py --dry-run     # fetch + score + print a ranked table; no Discord, no DB write
python main.py --seed        # mark all current matches seen, no notify (first-run setup)
python main.py               # real run: notify Discord + persist (needs DISCORD_WEBHOOK_URL)
```

`--dry-run` is the primary tuning tool: it shows every fresh job with its score,
strong/partial/FAIL status, matched terms, and any hard-fail reason.

Set the webhooks locally with:

```bash
export DISCORD_WEBHOOK_AUTONOMY="https://discord.com/api/webhooks/..."
export DISCORD_WEBHOOK_HARDWARE="https://discord.com/api/webhooks/..."
export DISCORD_WEBHOOK_SWFW="https://discord.com/api/webhooks/..."
```

`--dry-run` prints a `ROUTE` column (AUTO / HW / SW-FW) and a per-route count so
you can see how postings will be split before anything is sent. To re-tune which
channel a keyword feeds, move it between the lists under `routes.channels` in
`config.yaml`.

## Tests

```bash
python -m unittest discover -s tests -v
```

Covers word-boundary correctness, title/body weighting, category weights, the
body cap, all hard gates, the noise floor, end-to-end ranking, determinism, and
the fetch mapping for every adapter (no network).

## Defense companies

Companies tagged `defense: true` in `config.yaml` (Anduril, Shield AI, Saronic,
Chaos Industries, Vannevar Labs, Epirus, True Anomaly, Allen Control Systems)
print a `[DEFENSE]` flag in the run log. Remove any entry you don't want to track.

## Extending

- **New company**: add a line under `companies:` with its ATS + slug.
- **New keyword**: add it under the right category in `keywords:`; aliases are a
  list of surface variants that collapse to the canonical term.
- **New category**: add it under `keywords:` *and* give it a weight in
  `matching.weights` — no code change needed.
- **New ATS**: add an adapter to `fetch.py` following the existing pattern and a
  branch in `fetch_company`. Custom career pages (Apple, Tesla, Waymo) need a
  custom adapter — see the TODO block at the bottom of `config.yaml`.
```
