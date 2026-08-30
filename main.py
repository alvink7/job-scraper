"""JOBWATCH orchestrator + CLI.

Flow:
  1. Load config.
  2. fetch_company for each; accumulate; print per-company counts.
  3. Dedup via store.is_new(id).
  4. Score each fresh job with match.Matcher.
  5. Split: hard_fail (logged, never sent), noise (dropped), results (sent).
  6. Notify Discord (unless --dry-run), sorted by score desc.
  7. On success, mark ALL fresh jobs seen, then commit.

CLI:
  --dry-run       do everything except notify + persist; print a table
  --seed          mark all current fresh matches seen WITHOUT notifying
  --config PATH   default config.yaml
  --db PATH       default seen.db (or config.store.db_path)
Env:
  DISCORD_WEBHOOK_URL  required for real runs
"""

import argparse
import os
import sys
from collections import Counter, defaultdict

import yaml

# Ensure UTF-8 output (job titles/URLs contain em dashes, etc.). On Windows the
# console/file defaults to cp1252, which crashes on those; GH Actions is already
# UTF-8. reconfigure() exists on Python 3.7+.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import fetch
import grad_degree
import match as match_mod
import notify as notify_mod
from store import Store


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_all(config):
    all_jobs = []
    companies = config.get("companies", []) or []
    print(f"Fetching {len(companies)} companies...")
    for c in companies:
        jobs = fetch.fetch_company(c)
        name = c.get("name", "?")
        tag = " [DEFENSE]" if c.get("defense") else ""
        print(f"  {name:<24}{tag:<11} {len(jobs)} jobs")
        all_jobs.extend(jobs)
    print(f"Total fetched: {len(all_jobs)}")
    return all_jobs


ROUTE_LABELS = {
    "autonomous_driving": "AUTO",
    "hardware": "HW",
    "software_and_firmware": "SW/FW",
}


def rlabel(route):
    return ROUTE_LABELS.get(route, (route or "?")[:6])


def channel_webhooks(config):
    """Map channel name -> webhook URL (env var, falling back to _URL)."""
    routes = config.get("routes", {}) or {}
    fallback = os.environ.get("DISCORD_WEBHOOK_URL", "")
    out = {}
    for name, spec in (routes.get("channels") or {}).items():
        env = (spec or {}).get("webhook_env", "")
        out[name] = os.environ.get(env, "") or fallback
    return out


def route_summary(results):
    if not results:
        return "  by route: (none)"
    rc = Counter(j.get("route") for j in results)
    return "  by route: " + ", ".join(f"{rlabel(r)}={n}" for r, n in rc.items())


def print_table(scored):
    """Print a ranked table of every fresh job (for --dry-run tuning)."""
    def sort_key(j):
        # sent jobs first (by score desc), then hard-failed
        failed = 1 if j.get("hard_fail") else 0
        return (failed, -j.get("score", 0))

    rows = sorted(scored, key=sort_key)
    print("\n" + "=" * 100)
    print(f"{'SCORE':>5}  {'STATUS':<8} {'ROUTE':<6} {'COMPANY':<18} TITLE")
    print("=" * 100)
    for j in rows:
        hf = j.get("hard_fail", "")
        if hf:
            status = "FAIL"
        elif j.get("partial"):
            status = "partial"
        else:
            status = "strong"
        route = "" if hf else rlabel(j.get("route"))
        grad = j.get("grad") or {}
        gmark = ""
        if grad.get("required"):
            gmark = "  ⚠PhD" if grad.get("level") == "phd" else "  ⚠MS"
        print(f"{j.get('score', 0):>5}  {status:<8} {route:<6} "
              f"{j.get('company', '?')[:18]:<18} {j.get('title', '')[:46]}{gmark}")
        if hf:
            print(f"       └─ {hf}")
        else:
            matched = ", ".join(j.get("matched", []))
            print(f"       └─ [{matched}]  {j.get('url', '')}")
    print("=" * 100)


def split_results(scored):
    """Return (results_to_send, dropped_noise, hard_failed)."""
    results, noise, failed = [], [], []
    for j in scored:
        if j.get("hard_fail"):
            failed.append(j)
        elif j.get("score", 0) <= 0:
            noise.append(j)
        else:
            results.append(j)
    results.sort(key=lambda j: j.get("score", 0), reverse=True)
    return results, noise, failed


def main(argv=None):
    parser = argparse.ArgumentParser(description="JOBWATCH — keyword job watcher")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch+score+print, no notify, no persist")
    parser.add_argument("--seed", action="store_true",
                        help="mark all fresh matches seen WITHOUT notifying")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    db_path = args.db or (config.get("store", {}) or {}).get("db_path", "seen.db")

    all_jobs = fetch_all(config)

    # Dry-run does not touch the DB; still dedup if a db exists to keep the
    # table focused on genuinely fresh jobs. For a clean tuning view, dry-run
    # scores everything (fresh + seen) so the operator sees the full picture.
    store = None
    if not args.dry_run:
        store = Store(db_path)
        fresh = [j for j in all_jobs if store.is_new(j["id"])]
    else:
        fresh = all_jobs
    print(f"Fresh (new) jobs: {len(fresh)}")

    engine = match_mod.Matcher(config)
    scored = engine.score_jobs(fresh)

    results, noise, failed = split_results(scored)
    # Mark sendable jobs that REQUIRE a graduate degree (Master's/PhD) so the
    # notification / table can flag them. Preferred-only mentions don't count.
    for j in results:
        j["grad"] = grad_degree.assess_graduate_requirement(
            j.get("title", ""), j.get("content", ""))
    grad_n = sum(1 for j in results if j["grad"]["required"])
    print(f"  send: {len(results)}   noise-dropped: {len(noise)}   "
          f"hard-failed: {len(failed)}   grad-degree-required: {grad_n}")
    print(route_summary(results))

    if args.dry_run:
        print_table(scored)
        print("\n[dry-run] no Discord post, no DB write.")
        return 0

    if args.seed:
        for j in fresh:
            store.mark(j["id"])
        store.commit()
        print(f"[seed] marked {len(fresh)} fresh jobs seen "
              f"(no notification). DB now has {store.count()}.")
        store.close()
        return 0

    # Real run: route each result to its channel's webhook, then persist.
    webhooks = channel_webhooks(config)
    groups = defaultdict(list)
    for j in results:
        groups[j.get("route", engine.route_default)].append(j)

    all_ok = True
    posted_ids = set()
    for route, jobs in groups.items():
        wh = webhooks.get(route, "")
        if not wh:
            print(f"  [route:{rlabel(route)}] no webhook configured — "
                  f"skipping {len(jobs)} jobs (will retry next run)")
            all_ok = False
            continue
        if notify_mod.notify(wh, jobs, engine.min_score):
            posted_ids.update(j["id"] for j in jobs)
            print(f"  [route:{rlabel(route)}] posted {len(jobs)}")
        else:
            all_ok = False

    # Mark seen: everything that was never meant to post (noise + hard-fails)
    # plus every result that actually posted. Results whose channel failed or
    # was unconfigured stay unseen so they retry next run (no duplicate posts).
    result_ids = {j["id"] for j in results}
    marked = 0
    for j in fresh:
        if j["id"] in result_ids and j["id"] not in posted_ids:
            continue
        store.mark(j["id"])
        marked += 1
    store.commit()
    print(f"Posted {len(posted_ids)} jobs; marked {marked} seen. "
          f"DB now has {store.count()}.")
    store.close()
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
