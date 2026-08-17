"""One-off helper: probe candidate ATS slugs and report which return jobs.

Not part of the runtime — a tool to build/repair the verified company list.
Usage: python verify_slugs.py
"""
import json
import sys
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0 Safari/537.36"


def probe(ats, slug):
    try:
        if ats == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        elif ats == "lever":
            url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        elif ats == "ashby":
            url = (f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
                   "?includeCompensation=false")
        else:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        if ats == "lever":
            n = len(data) if isinstance(data, list) else 0
        else:
            n = len(data.get("jobs", []))
        return n
    except Exception as e:  # noqa: BLE001
        return f"ERR {getattr(e, 'code', e)}"


# (display, [(ats, candidate_slug), ...])
CANDIDATES = [
    ("Skydio", [("greenhouse", "skydio"), ("lever", "skydio"), ("ashby", "skydio")]),
    ("Zipline", [("lever", "flyzipline"), ("greenhouse", "zipline"), ("greenhouse", "ziplinelogistics")]),
    ("Applied Intuition", [("greenhouse", "appliedintuition"), ("ashby", "appliedintuition"), ("lever", "applied-intuition")]),
    ("Aurora", [("greenhouse", "aurora"), ("greenhouse", "aurorainnovationinc")]),
    ("Luminar", [("greenhouse", "luminar"), ("greenhouse", "luminartechnologies")]),
    ("Ouster", [("greenhouse", "ouster"), ("lever", "ouster")]),
    ("Cruise", [("greenhouse", "cruise"), ("greenhouse", "getcruise")]),
    ("Waabi", [("greenhouse", "waabi"), ("ashby", "waabi"), ("lever", "waabi")]),
    ("Gatik", [("greenhouse", "gatikai"), ("lever", "gatik"), ("ashby", "gatik")]),
    ("Kodiak", [("greenhouse", "kodiak"), ("lever", "kodiak"), ("ashby", "kodiak")]),
    ("Bear Robotics", [("lever", "bearrobotics"), ("greenhouse", "bearrobotics")]),
    ("Physical Intelligence", [("ashby", "physicalintelligence"), ("greenhouse", "physicalintelligence")]),
    ("Skild AI", [("ashby", "skild"), ("greenhouse", "skildai")]),
    ("Dexterity", [("lever", "dexterity"), ("greenhouse", "dexterity"), ("ashby", "dexterity")]),
    ("Chef Robotics", [("ashby", "chefrobotics"), ("lever", "chefrobotics"), ("greenhouse", "chefrobotics")]),
    ("Bright Machines", [("lever", "brightmachines"), ("greenhouse", "brightmachines")]),
    ("Shield AI", [("greenhouse", "shieldai"), ("lever", "shieldai"), ("ashby", "shieldai")]),
    ("Vannevar Labs", [("greenhouse", "vannevarlabs"), ("ashby", "vannevarlabs"), ("lever", "vannevarlabs")]),
    ("Chaos Industries", [("ashby", "chaosindustries"), ("greenhouse", "chaosindustries")]),
    ("Castelion", [("ashby", "castelion"), ("greenhouse", "castelion"), ("lever", "castelion")]),
    ("True Anomaly", [("greenhouse", "trueanomaly"), ("ashby", "trueanomaly"), ("lever", "trueanomaly")]),
    ("Hadrian", [("greenhouse", "hadrian"), ("ashby", "hadrianautomation"), ("lever", "hadrian")]),
    ("Relativity Space", [("greenhouse", "relativity"), ("greenhouse", "relativityspace")]),
    ("Stoke Space", [("greenhouse", "stokespace"), ("ashby", "stokespace"), ("lever", "stokespace")]),
    ("K2 Space", [("ashby", "k2space"), ("greenhouse", "k2space")]),
    ("Cerebras", [("greenhouse", "cerebras"), ("lever", "cerebras")]),
    ("Groq", [("greenhouse", "groq"), ("lever", "groq"), ("ashby", "groq")]),
    ("SambaNova", [("greenhouse", "sambanova"), ("greenhouse", "sambanovasystems")]),
    ("Tenstorrent", [("greenhouse", "tenstorrent"), ("lever", "tenstorrent")]),
    ("Etched", [("ashby", "etched"), ("greenhouse", "etched")]),
    ("d-Matrix", [("greenhouse", "dmatrix"), ("ashby", "dmatrix")]),
    ("Rain AI", [("ashby", "rain"), ("greenhouse", "rainai")]),
    ("Ayar Labs", [("greenhouse", "ayarlabs"), ("lever", "ayarlabs")]),
    ("Lightmatter", [("greenhouse", "lightmatter"), ("ashby", "lightmatter")]),
    ("Lucid Motors", [("greenhouse", "lucidmotors"), ("greenhouse", "lucid")]),
    ("Form Energy", [("greenhouse", "formenergy"), ("lever", "formenergy")]),
    ("Commonwealth Fusion", [("greenhouse", "commonwealthfusionsystems"), ("greenhouse", "cfsenergy")]),
    ("OpenAI", [("ashby", "openai"), ("greenhouse", "openai")]),
    ("Anthropic", [("greenhouse", "anthropic"), ("ashby", "anthropic")]),
    ("Scale AI", [("ashby", "scaleai"), ("greenhouse", "scaleai"), ("lever", "scaleai")]),
    ("Databricks", [("greenhouse", "databricks")]),
    ("Snowflake", [("greenhouse", "snowflakecomputing"), ("greenhouse", "snowflake")]),
    ("Roblox", [("greenhouse", "roblox")]),
    ("Reddit", [("greenhouse", "reddit")]),
    ("Pinterest", [("greenhouse", "pinterest"), ("greenhouse", "pinterestlabs")]),
    ("Coinbase", [("greenhouse", "coinbase")]),
    ("Stripe", [("greenhouse", "stripe")]),
    ("Ramp", [("ashby", "ramp"), ("greenhouse", "ramp")]),
    ("Neuralink", [("greenhouse", "neuralink"), ("ashby", "neuralink")]),
    ("Waymo", [("greenhouse", "waymo")]),
    ("Boston Dynamics", [("greenhouse", "bostondynamics")]),
    ("Matic Robots", [("ashby", "matic"), ("greenhouse", "maticrobots"), ("lever", "matic")]),
    ("Overland AI", [("ashby", "overlandai"), ("greenhouse", "overlandai")]),
    ("Anduril", [("greenhouse", "andurilindustries")]),
    ("Applied Intuition (ashby)", [("ashby", "applied-intuition")]),
]


def main():
    for name, cands in CANDIDATES:
        hits = []
        for ats, slug in cands:
            n = probe(ats, slug)
            if isinstance(n, int) and n > 0:
                hits.append(f"{ats}:{slug}={n}")
        status = "  ".join(hits) if hits else "NONE"
        print(f"{name:<28} {status}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
