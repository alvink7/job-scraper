"""Deterministic weighted keyword scoring engine (no LLM).

Public API:
    engine = Matcher(config)          # compiles regexes once
    scored = engine.score_job(job)    # returns job augmented with:
        score:     int
        matched:   list[str]   # canonical terms hit (title union body), sorted
        hard_fail: str         # reason, or "" if it passed all gates
        partial:   bool
    results = engine.score_jobs(jobs) # convenience; returns all scored jobs

Determinism: no randomness, no network. Same inputs -> same outputs.
"""

import re

# Boundary lookarounds that tolerate non-word chars like c++, c/c++, i2c, ros2.
# We do NOT use \b because it breaks on '+' and '/'.
_BOUND_LEFT = r"(?<![A-Za-z0-9+#/.-])"
_BOUND_RIGHT = r"(?![A-Za-z0-9+#/.-])"

_WS_COLLAPSE = re.compile(r"\s+")

# Years-of-experience patterns. We take the smallest N found.
_YEARS_PATTERNS = [
    re.compile(r"(\d+)\s*-\s*(\d+)\s*\+?\s*years", re.I),
    re.compile(r"(\d+)\s*\+?\s*years", re.I),
    re.compile(r"minimum\s+(?:of\s+)?(\d+)\s+years", re.I),
    re.compile(r"at\s+least\s+(\d+)\s+years", re.I),
]


def _norm(text):
    if not text:
        return ""
    return _WS_COLLAPSE.sub(" ", text).strip().lower()


def _compile_term(term):
    """Compile one surface term into a boundary-aware, case-insensitive regex.

    Internal whitespace in a phrase matches one-or-more whitespace chars.
    """
    escaped = re.escape(term.strip())
    # re.escape turns a space into '\ '; make runs of ws flexible.
    escaped = re.sub(r"(\\?\s)+", r"\\s+", escaped)
    pattern = _BOUND_LEFT + escaped + _BOUND_RIGHT
    return re.compile(pattern, re.I)


class Matcher:
    def __init__(self, config):
        self.cfg = config
        m = config.get("matching", {})
        self.title_multiplier = float(m.get("title_multiplier", 2.0))
        self.body_cap = int(m.get("body_cap_per_category", 3))
        self.min_score = float(m.get("min_score", 10))
        self.weights = dict(m.get("weights", {
            "core": 5, "strong": 3, "supporting": 1, "negative": -2,
        }))

        gates = config.get("gates", {})
        self.career_stage = gates.get("career_stage", "intern_or_newgrad")
        self.max_years = int(gates.get("max_years", 3))
        self.exclude_title = list(gates.get("exclude_title", []))
        self.location_any = list(gates.get("location_any", []))
        self.min_domain_score = float(gates.get("min_domain_score", 3))

        # Compile keyword regexes once. Structure:
        #   self.terms = [ (category, canonical, compiled_regex), ... ]
        self.terms = []
        for category, mapping in (config.get("keywords") or {}).items():
            if category not in self.weights:
                # Category with no weight -> skip (config error, but be safe).
                continue
            for canonical, aliases in (mapping or {}).items():
                surfaces = aliases if aliases else [canonical]
                for surface in surfaces:
                    self.terms.append(
                        (category, canonical, _compile_term(surface))
                    )

        # Compile gate regexes.
        self._exclude_res = [
            (t, _compile_term(t)) for t in self.exclude_title
        ]

        # Intern / new-grad positive signals.
        self._intern_signals = [
            "intern", "internship", "co-op", "coop", "new grad",
            "new graduate", "university grad", "recent graduate",
            "early career", "entry level", "entry-level", "college grad",
            "campus", "student", "u.s. new college grad",
            "new college grad", "undergraduate", "apprentice",
            "rotational program", "graduate program", "class of 20",
        ]
        self._intern_res = [_compile_term(s) for s in self._intern_signals]

    # ------------------------------------------------------------------ #
    # Hard gates
    # ------------------------------------------------------------------ #
    def _gate_seniority(self, title_text):
        for term, rx in self._exclude_res:
            if rx.search(title_text):
                return f"excluded title term: {term}"
        return ""

    def _min_years(self, text):
        found = []
        for rx in _YEARS_PATTERNS:
            for m in rx.finditer(text):
                # first captured group is the low end
                try:
                    found.append(int(m.group(1)))
                except (ValueError, TypeError):
                    continue
        return min(found) if found else None

    def _has_intern_signal(self, full_text):
        return any(rx.search(full_text) for rx in self._intern_res)

    def _gate_career_stage(self, full_text):
        """Return (hard_fail_reason, force_partial)."""
        if self.career_stage == "any":
            return "", False
        # intern_or_newgrad
        has_signal = self._has_intern_signal(full_text)
        years = self._min_years(full_text)
        if has_signal:
            # Even with a positive signal, a high years requirement disqualifies.
            if years is not None and years >= self.max_years:
                return f"requires {years}+ years experience", False
            return "", False
        # No positive intern signal.
        if years is not None and years >= self.max_years:
            return f"requires {years}+ years experience", False
        # No signal, no disqualifying years req -> soft pass, mark partial.
        return "", True

    def _gate_location(self, location, full_text):
        """Return (hard_fail_reason, force_partial)."""
        if not self.location_any:
            return "", False  # gate disabled
        haystack = location.strip().lower()
        if not haystack:
            # Missing location: allow if body mentions an allowed place,
            # else soft partial pass (don't hard-fail on missing data).
            for loc in self.location_any:
                if loc.lower() in full_text:
                    return "", False
            return "", True
        for loc in self.location_any:
            if loc.lower() in haystack:
                return "", False
        # Location present but not allowlisted: also check body as a fallback.
        for loc in self.location_any:
            if loc.lower() in full_text:
                return "", False
        return "location not in allowlist", False

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #
    def _find_hits(self, title_text, body_text):
        """Return dicts: title_hits[cat] = set(canon), body_hits[cat] = set."""
        title_hits = {}
        body_hits = {}
        for category, canonical, rx in self.terms:
            if rx.search(title_text):
                title_hits.setdefault(category, set()).add(canonical)
            if body_text and rx.search(body_text):
                body_hits.setdefault(category, set()).add(canonical)
        return title_hits, body_hits

    def score_job(self, job):
        out = dict(job)
        title_text = _norm(job.get("title", ""))
        body_text = _norm(job.get("content", ""))
        full_text = (title_text + " " + body_text).strip()
        location = job.get("location", "") or ""

        out["score"] = 0
        out["matched"] = []
        out["hard_fail"] = ""
        out["partial"] = False

        force_partial = False

        # Gate (a): seniority.
        fail = self._gate_seniority(title_text)
        if fail:
            out["hard_fail"] = fail
            return out

        # Gate (b): career stage.
        fail, fp = self._gate_career_stage(full_text)
        if fail:
            out["hard_fail"] = fail
            return out
        force_partial = force_partial or fp

        # Gate (c): location.
        fail, fp = self._gate_location(location, full_text)
        if fail:
            out["hard_fail"] = fail
            return out
        force_partial = force_partial or fp

        # Scoring.
        title_hits, body_hits = self._find_hits(title_text, body_text)
        title_score = 0.0
        body_score = 0.0
        matched = set()

        categories = set(title_hits) | set(body_hits)
        for cat in categories:
            w = self.weights.get(cat, 0)
            th = title_hits.get(cat, set())
            bh = body_hits.get(cat, set())
            # Negative terms are body-only (never boosted by title placement)
            # and never advertised in `matched` — they just push the score down.
            if cat == "negative":
                body_score += w * min(len(bh), self.body_cap)
                continue
            title_score += w * self.title_multiplier * len(th)
            # Body hits capped per category (title hits are NOT capped).
            body_count = min(len(bh), self.body_cap)
            body_score += w * body_count
            matched |= th
            matched |= bh

        score = round(title_score + body_score)
        out["score"] = int(score)
        out["matched"] = sorted(matched)

        # Gate (d): domain floor (anti-noise). Applied after scoring.
        if score < self.min_domain_score:
            out["hard_fail"] = "below domain floor (noise)"
            return out

        # Strong vs partial.
        if score < self.min_score:
            out["partial"] = True
        if force_partial:
            out["partial"] = True

        return out

    def score_jobs(self, jobs):
        return [self.score_job(j) for j in jobs]
