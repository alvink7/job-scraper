"""ATS adapters -> normalized job dicts.

Stdlib only. Every adapter wraps its network calls, prints a one-line error on
failure, and returns [] so one dead company never crashes the run.

Normalized job schema (every adapter returns a list of these):
    {
        "id":       str,   # "{ats}:{slug}:{native_id}" — stable, used for dedup
        "company":  str,   # display name
        "title":    str,
        "location": str,   # may be ""
        "url":      str,
        "content":  str,   # plain-text JD body (HTML stripped); may be ""
        "updated":  str,   # ISO timestamp or "" — informational
    }

The mapping logic for each adapter is factored into a pure `map_*` function that
takes an already-parsed payload, so it can be unit-tested without a network.
"""

import json
import re
import time
import html as _htmllib
import urllib.request
import urllib.error
import urllib.parse

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Common helpers
# --------------------------------------------------------------------------- #
def _strip_html(s):
    """Unescape HTML entities, remove tags, collapse whitespace."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = _htmllib.unescape(s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def _request(url, data=None, method="GET"):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _get(url):
    return _request(url, method="GET")


def _post(url, body):
    return _request(url, data=body, method="POST")


# --------------------------------------------------------------------------- #
# Lever
# --------------------------------------------------------------------------- #
def map_lever(company, slug, postings):
    jobs = []
    for p in postings:
        content_parts = []
        plain = p.get("descriptionPlain") or p.get("description") or ""
        content_parts.append(_strip_html(plain))
        for lst in p.get("lists", []) or []:
            t = _strip_html(lst.get("text", ""))
            c = _strip_html(lst.get("content", ""))
            if t or c:
                content_parts.append(f"{t}: {c}")
        categories = p.get("categories") or {}
        jobs.append({
            "id": f"lever:{slug}:{p.get('id', '')}",
            "company": company,
            "title": p.get("text", "") or "",
            "location": categories.get("location", "") or "",
            "url": p.get("hostedUrl") or p.get("applyUrl") or "",
            "content": " ".join(x for x in content_parts if x).strip(),
            "updated": str(p.get("createdAt", "") or ""),
        })
    return jobs


def fetch_lever(company, slug):
    try:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        data = _get(url)
        if not isinstance(data, list):
            print(f"  [lever:{slug}] unexpected response shape")
            return []
        return map_lever(company, slug, data)
    except Exception as e:  # noqa: BLE001
        print(f"  [lever:{slug}] ERROR: {e}")
        return []


# --------------------------------------------------------------------------- #
# Greenhouse
# --------------------------------------------------------------------------- #
def map_greenhouse(company, slug, payload):
    jobs = []
    for j in payload.get("jobs", []) or []:
        loc = (j.get("location") or {}).get("name", "") or ""
        jobs.append({
            "id": f"greenhouse:{slug}:{j.get('id', '')}",
            "company": company,
            "title": j.get("title", "") or "",
            "location": loc,
            "url": j.get("absolute_url", "") or "",
            "content": _strip_html(j.get("content", "") or ""),
            "updated": str(j.get("updated_at", "") or ""),
        })
    return jobs


def fetch_greenhouse(company, slug):
    try:
        url = (
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        )
        data = _get(url)
        return map_greenhouse(company, slug, data)
    except Exception as e:  # noqa: BLE001
        print(f"  [greenhouse:{slug}] ERROR: {e}")
        return []


# --------------------------------------------------------------------------- #
# Ashby
# --------------------------------------------------------------------------- #
def map_ashby(company, slug, payload):
    jobs = []
    for j in payload.get("jobs", []) or []:
        content = j.get("descriptionPlain") or ""
        if not content:
            content = _strip_html(j.get("descriptionHtml", "") or "")
        jobs.append({
            "id": f"ashby:{slug}:{j.get('id', '')}",
            "company": company,
            "title": j.get("title", "") or "",
            "location": j.get("location", "") or "",
            "url": j.get("jobUrl") or j.get("applyUrl") or "",
            "content": content,
            "updated": str(j.get("publishedAt", "") or ""),
        })
    return jobs


def fetch_ashby(company, slug):
    try:
        url = (
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            "?includeCompensation=false"
        )
        data = _get(url)
        return map_ashby(company, slug, data)
    except Exception as e:  # noqa: BLE001
        print(f"  [ashby:{slug}] ERROR: {e}")
        return []


# --------------------------------------------------------------------------- #
# Workday (undocumented CXS JSON endpoint)
# --------------------------------------------------------------------------- #
def map_workday_posting(company, tenant, wd_num, site, posting):
    external_path = posting.get("externalPath", "") or ""
    url = (
        f"https://{tenant}.wd{wd_num}.myworkdayjobs.com/{site}{external_path}"
    )
    content = _strip_html(posting.get("jobDescription") or posting.get("title") or "")
    return {
        "id": f"workday:{tenant}:{external_path}",
        "company": company,
        "title": posting.get("title", "") or "",
        "location": posting.get("locationsText", "") or "",
        "url": url,
        "content": content,
        "updated": str(posting.get("postedOn", "") or ""),
    }


def fetch_workday(company, tenant, wd_num, site, max_pages=15):
    jobs = []
    try:
        base = (
            f"https://{tenant}.wd{wd_num}.myworkdayjobs.com"
            f"/wday/cxs/{tenant}/{site}/jobs"
        )
        offset = 0
        limit = 20
        total = None
        for _page in range(max_pages):
            body = {
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            }
            data = _post(base, body)
            postings = data.get("jobPostings", []) or []
            if total is None:
                total = data.get("total", 0)
            if not postings:
                break
            for p in postings:
                jobs.append(
                    map_workday_posting(company, tenant, wd_num, site, p)
                )
            offset += limit
            if total and offset >= total:
                break
            time.sleep(0.4)
        return jobs
    except Exception as e:  # noqa: BLE001
        print(f"  [workday:{tenant}] ERROR: {e}")
        return jobs  # return whatever we managed to collect


# --------------------------------------------------------------------------- #
# Amazon (custom amazon.jobs search.json — no ATS)
# --------------------------------------------------------------------------- #
# Amazon has tens of thousands of roles, so instead of pulling everything we run
# a set of candidate-relevant relevance queries and dedup. Location/intern/domain
# gates then filter as usual. Covers Lab126, Annapurna/AWS silicon, Devices,
# Amazon Robotics, etc.
DEFAULT_AMAZON_QUERIES = [
    "hardware development engineer",   # Amazon's title for HW roles (incl. interns)
    "asic intern",
    "fpga intern",
    "firmware intern",
    "embedded intern",
    "electrical engineer intern",
    "robotics intern",
    "silicon intern",
]


def map_amazon(name, jobs):
    out = []
    for j in jobs:
        loc = j.get("normalized_location") or j.get("location") or ""
        content = " ".join(
            _strip_html(j.get(k, "") or "")
            for k in ("description_short", "basic_qualifications",
                      "preferred_qualifications", "description")
        )
        out.append({
            "id": f"amazon:{j.get('id_icims') or j.get('id')}",
            "company": name,
            "title": j.get("title", "") or "",
            "location": loc,
            "url": "https://www.amazon.jobs" + (j.get("job_path", "") or ""),
            "content": content.strip(),
            "updated": str(j.get("posted_date", "") or ""),
        })
    return out


def fetch_amazon(name, queries=None, result_limit=100, max_pages=3):
    queries = queries or DEFAULT_AMAZON_QUERIES
    seen_ids = set()
    raw = []
    try:
        for q in queries:
            for page in range(max_pages):
                offset = page * result_limit
                url = (
                    "https://www.amazon.jobs/en/search.json?base_query="
                    + urllib.parse.quote(q)
                    + f"&offset={offset}&result_limit={result_limit}&sort=relevant"
                )
                data = _get(url)
                page_jobs = data.get("jobs", []) or []
                if not page_jobs:
                    break
                for j in page_jobs:
                    key = j.get("id_icims") or j.get("id")
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    raw.append(j)
                if len(page_jobs) < result_limit:
                    break
                time.sleep(0.3)
        return map_amazon(name, raw)
    except Exception as e:  # noqa: BLE001
        print(f"  [amazon] ERROR: {e}")
        return map_amazon(name, raw)


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
def fetch_company(company):
    """Route a company dict to the right adapter. Returns list[job] (maybe [])."""
    ats = (company.get("ats") or "").lower()
    name = company.get("name", "?")
    if ats == "lever":
        return fetch_lever(name, company["slug"])
    if ats == "greenhouse":
        return fetch_greenhouse(name, company["slug"])
    if ats == "ashby":
        return fetch_ashby(name, company["slug"])
    if ats == "workday":
        return fetch_workday(
            name,
            company["tenant"],
            company["wd_num"],
            company["site"],
            company.get("max_pages", 15),
        )
    if ats == "amazon":
        return fetch_amazon(name, company.get("queries"))
    print(f"  [{name}] unknown ats '{ats}' — skipping")
    return []
