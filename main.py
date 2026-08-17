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


def print_table(scored):
    """Print a ranked table of every fresh job (for --dry-run tuning)."""
    def sort_key(j):
        # sent jobs first (by score desc), then hard-failed
        failed = 1 if j.get("hard_fail") else 0
        return (failed, -j.get("score", 0))

    rows = sorted(scored, key=sort_key)
    print("\n" + "=" * 100)
    print(f"{'SCORE':>5}  {'STATUS':<10} {'COMPANY':<20} TITLE")
    print("=" * 100)
    for j in rows:
        hf = j.get("hard_fail", "")
        if hf:
            status = "FAIL"
        elif j.get("partial"):
            status = "partial"
        else:
            status = "strong"
        print(f"{j.get('score', 0):>5}  {status:<10} "
              f"{j.get('company', '?')[:20]:<20} {j.get('title', '')[:50]}")
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
    print(f"  send: {len(results)}   noise-dropped: {len(noise)}   "
          f"hard-failed: {len(failed)}")

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

    # Real run: notify, then persist.
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    ok = notify_mod.notify(webhook, results, engine.min_score)
    if ok:
        # Mark ALL fresh jobs seen (results + noise + failed): gates are
        # deterministic, so re-evaluating them changes nothing, and this stops
        # noise from being reprocessed forever.
        for j in fresh:
            store.mark(j["id"])
        store.commit()
        print(f"Notified {len(results)} jobs; marked {len(fresh)} seen. "
              f"DB now has {store.count()}.")
    else:
        print("Notification failed — NOT marking jobs seen (will retry next run).")
        store.close()
        return 1

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
