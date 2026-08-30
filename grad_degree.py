"""Flag jobs that REQUIRE a graduate degree (Master's / PhD).

A graduate degree that appears only under *preferred* qualifications does NOT
count as required — that's fine for a candidate without one. Neither does a
requirement that accepts a Bachelor's as an alternative ("BS/MS", "Bachelor's
or Master's degree"). We flag a role only when a graduate degree is the stated
*minimum*.

The detection is deliberately precision-first: when in doubt, we do NOT flag,
because a false "grad required" would hide a role the candidate could actually
get. `assess_graduate_requirement()` is pure and unit-tested; the CLI runs it
over the live pipeline (the same jobs `main.py` would consider) and marks them.

Usage:
    python grad_degree.py                # fetch + gate, then mark the matches
    python grad_degree.py --all          # assess every fetched job, not just matches
    python grad_degree.py --config x.yaml
    python grad_degree.py --text "..."   # assess a single JD string (no network)
"""

import argparse
import re
import sys

# --------------------------------------------------------------------------- #
# Degree phrasing. Bare "MS" is intentionally required to be in degree context
# ("MS in", "MS degree", "MS/PhD", or the punctuated "M.S.") so we never trip on
# "MS Office", "CMS", "systems", etc.
# --------------------------------------------------------------------------- #
_PHD = r"ph\.?\s?d\b|ph\.?d\.?|doctoral|doctorate"
_MASTERS = (
    r"master'?s|master of (?:science|engineering|technology)"
    r"|graduate degree|advanced degree|postgraduate"
    r"|m\.s\.|m\.sc\.|\bm\.?eng\.?\b|\bmsc\b"
    r"|\bms\b(?=\s+(?:in|degree)\b)|\bms\s*/\s*ph\.?d|\bms\s+or\s+ph\.?d"
)
_GRAD_RE = re.compile(rf"(?P<phd>{_PHD})|(?P<masters>{_MASTERS})", re.I)

# A Bachelor's offered anywhere in the same clause means a grad degree is not
# the minimum (BS/MS, "Bachelor's or Master's", "BS or MS in ...").
_BACH_RE = re.compile(
    r"bachelor'?s|bachelor of|undergraduate degree"
    r"|b\.s\.|b\.sc\.|b\.eng\.|b\.a\.|\bbs\b(?=\s*[/,]|\s+(?:or|in|degree)\b)",
    re.I,
)

# In-clause qualifiers that turn any degree mention into a "nice to have".
_PREFERRED_QUALIFIER_RE = re.compile(
    r"preferred|nice[- ]to[- ]have|a plus|is a plus|bonus|desirable|desired"
    r"|ideally|would be (?:a )?(?:plus|great|nice)|good to have|even better"
    r"|advantage|not required|or equivalent experience",
    re.I,
)

# Section headers (position-based, so it survives HTML-stripped one-line blobs).
_PREF_SECTION_RE = re.compile(
    r"preferred qualifications|preferred skills|nice[- ]to[- ]haves?"
    r"|bonus (?:points|qualifications)|desired qualifications"
    r"|pluses|it'?s a plus|even better|additionally,? you",
    re.I,
)
_REQ_SECTION_RE = re.compile(
    r"minimum qualifications|basic qualifications|required qualifications"
    r"|requirements|required skills|what you'?ll need|what we'?re looking for"
    r"|who you are|you (?:must|will) have|qualifications:|must have",
    re.I,
)

# Language that marks a degree as a hard requirement even without a section.
_REQUIREMENT_LANG_RE = re.compile(
    r"required|must (?:have|possess|hold)|minimum of|at least a|requires a"
    r"|or higher|is required|degree required|currently (?:pursuing|enrolled)"
    r"|enrolled in|pursuing a|working toward|candidate for",
    re.I,
)

# Split into clauses; HTML-stripped JDs collapse to one line, so split on
# sentence/bullet punctuation as well as any surviving newlines.
_CLAUSE_SPLIT_RE = re.compile(r"[\n\r]|(?<=[.;:])\s+|\s*[••▪‣]\s*")


def _last_section_before(marks, pos):
    """Given sorted (index, kind) header marks, the kind governing `pos`."""
    kind = None
    for idx, k in marks:
        if idx <= pos:
            kind = k
        else:
            break
    return kind


def assess_graduate_requirement(title, content):
    """Return {'required': bool, 'level': 'phd'|'masters'|None, 'evidence': str}.

    `required` is True only when a graduate degree is the stated minimum: it is
    not offset by a Bachelor's alternative and not confined to a preferred /
    bonus context.
    """
    text = content or ""
    result = {"required": False, "level": None, "evidence": ""}

    # Position-sorted section headers, so a grad mention inherits the section it
    # falls under even when the whole JD is one whitespace-collapsed line.
    pref_marks = [m.start() for m in _PREF_SECTION_RE.finditer(text)]
    # Drop a "required" hit that is really the "...Qualifications:" tail of a
    # "Preferred Qualifications:" header (else it would flip the section).
    req_marks = [m.start() for m in _REQ_SECTION_RE.finditer(text)
                 if not any(0 <= m.start() - p <= 14 for p in pref_marks)]
    marks = sorted([(p, "preferred") for p in pref_marks]
                   + [(p, "required") for p in req_marks])

    best = None  # prefer to report a PhD requirement over a Master's one
    for m in _GRAD_RE.finditer(text):
        level = "phd" if m.group("phd") else "masters"
        # The clause this mention sits in (for local negation signals).
        cl_start = text.rfind("\n", 0, m.start())
        pieces = _CLAUSE_SPLIT_RE.split(text[max(cl_start, 0):m.end() + 160])
        clause = next((p for p in pieces if m.group(0).lower() in p.lower()),
                      text[max(m.start() - 80, 0):m.end() + 120])

        if _BACH_RE.search(clause):
            continue  # a Bachelor's is an accepted alternative
        if _PREFERRED_QUALIFIER_RE.search(clause):
            continue  # "... Master's preferred", "PhD a plus"
        section = _last_section_before(marks, m.start())
        if section == "preferred":
            continue  # under a Preferred / Bonus header

        if section == "required" or _REQUIREMENT_LANG_RE.search(clause):
            evidence = " ".join(clause.split())[:200]
            if level == "phd":
                return {"required": True, "level": "phd", "evidence": evidence}
            if best is None:
                best = {"required": True, "level": "masters", "evidence": evidence}

    return best or result


# --------------------------------------------------------------------------- #
# CLI — mark the live pipeline's jobs
# --------------------------------------------------------------------------- #
def _mark_pipeline(config_path, assess_all):
    import main as main_mod
    import match as match_mod

    config = main_mod.load_config(config_path)
    jobs = main_mod.fetch_all(config)
    if assess_all:
        targets = jobs
    else:
        engine = match_mod.Matcher(config)
        scored = engine.score_jobs(jobs)
        targets = [j for j in scored
                   if not j.get("hard_fail") and j.get("score", 0) > 0]

    for j in targets:
        j["grad"] = assess_graduate_requirement(j.get("title", ""),
                                                 j.get("content", ""))
    flagged = [j for j in targets if j["grad"]["required"]]

    scope = "all fetched jobs" if assess_all else "matched (sendable) jobs"
    print(f"\nGraduate-degree scan over {len(targets)} {scope}: "
          f"{len(flagged)} require a graduate degree.\n")
    print(f"{'DEGREE':<8} {'COMPANY':<20} TITLE")
    print("=" * 90)
    for j in sorted(flagged, key=lambda x: (x["grad"]["level"] != "phd",
                                            x.get("company", ""))):
        tag = "PhD" if j["grad"]["level"] == "phd" else "MS"
        print(f"⚠ {tag:<6} {j.get('company', '?')[:20]:<20} "
              f"{j.get('title', '')[:52]}")
        print(f"         └─ {j['grad']['evidence'][:100]}")
    if not flagged:
        print("(none)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Mark jobs that REQUIRE a graduate degree.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--all", action="store_true",
                    help="assess every fetched job, not just gated matches")
    ap.add_argument("--text",
                    help="assess a single JD string and exit (no network)")
    args = ap.parse_args(argv)

    if args.text is not None:
        a = assess_graduate_requirement("", args.text)
        if a["required"]:
            print(f"REQUIRES a graduate degree ({a['level']}): {a['evidence']}")
        else:
            print("Does not require a graduate degree.")
        return 0
    return _mark_pipeline(args.config, args.all)


if __name__ == "__main__":
    sys.exit(main())
