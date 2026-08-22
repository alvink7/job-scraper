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

# Word-boundary lookarounds. We use these instead of \b because \b breaks on
# terms whose edges are non-word chars (c++, i2c, ros2). Crucially the class is
# word-chars only ([A-Za-z0-9_]) — separators like '/', '.', '-' must NOT be in
# it, or a term glued to one is silently missed (e.g. "intern" in "intern/co-op",
# or each of "i2c"/"spi"/"uart" in "I2C/SPI/UART"). A term may still CONTAIN
# those chars internally (e.g. "c/c++", "co-op"); the boundary only guards the
# term's two ends, so "can" is still blocked inside "scan" and "ros" in "across".
_BOUND_LEFT = r"(?<![A-Za-z0-9_])"
_BOUND_RIGHT = r"(?![A-Za-z0-9_])"

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

        # Routing: map each canonical term -> channel name.
        routes = config.get("routes", {}) or {}
        self.route_default = routes.get("default", "software_and_firmware")
        self.route_priority = list(routes.get("priority", []))
        self.route_of = {}
        for channel, spec in (routes.get("channels") or {}).items():
            for term in (spec.get("terms") or []):
                self.route_of[term] = channel
        # Ensure every channel appears in the priority list (stable order).
        for channel in (routes.get("channels") or {}):
            if channel not in self.route_priority:
                self.route_priority.append(channel)

        # Intern / new-grad positive signals (used by "intern_or_newgrad").
        self._intern_signals = [
            "intern", "interns", "internship", "internships", "co-op", "coop",
            "new grad", "new graduate", "university grad", "recent graduate",
            "early career", "entry level", "entry-level", "college grad",
            "campus", "student", "u.s. new college grad",
            "new college grad", "undergraduate", "apprentice",
            "rotational program", "graduate program", "class of 20",
        ]
        self._intern_res = [_compile_term(s) for s in self._intern_signals]

        # Strict internship-only signals (used by "intern_only"): NO new-grad /
        # entry-level terms — the role must actually be an internship / co-op.
        default_intern_only = [
            "intern", "interns", "internship", "internships", "co-op", "coop",
            "summer intern", "winter intern", "fall intern", "spring intern",
            "internship program", "intern program",
        ]
        intern_only_terms = gates.get("intern_terms", default_intern_only)
        self._intern_only_res = [_compile_term(s) for s in intern_only_terms]

        # Season targeting. season="summer" drops titles naming a different
        # season (keeps "summer …" and season-unspecified intern titles).
        self.season = gates.get("season", "any")
        default_exclude_seasons = ["fall", "autumn", "winter", "spring"]
        self._season_exclude_res = [
            (t, _compile_term(t))
            for t in gates.get("exclude_seasons", default_exclude_seasons)
        ]

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

    def _gate_season(self, title_text):
        """Return hard_fail reason, or ''. Only active when season == 'summer'."""
        if self.season != "summer":
            return ""
        for term, rx in self._season_exclude_res:
            if rx.search(title_text):
                return f"non-summer season: {term}"
        return ""

    def _gate_career_stage(self, title_text, full_text):
        """Return (hard_fail_reason, force_partial)."""
        if self.career_stage == "any":
            return "", False

        if self.career_stage == "intern_only":
            # Must be an actual internship/co-op, and the signal must be in the
            # TITLE — body mentions of internship programs are boilerplate and
            # would let full-time roles leak through.
            years = self._min_years(full_text)
            if years is not None and years >= self.max_years:
                return f"requires {years}+ years experience", False
            if any(rx.search(title_text) for rx in self._intern_only_res):
                return "", False
            return "not an internship", False

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
        out["route"] = self.route_default

        force_partial = False

        # Gate (a): seniority.
        fail = self._gate_seniority(title_text)
        if fail:
            out["hard_fail"] = fail
            return out

        # Gate (b): career stage.
        fail, fp = self._gate_career_stage(title_text, full_text)
        if fail:
            out["hard_fail"] = fail
            return out
        force_partial = force_partial or fp

        # Gate (b2): season targeting.
        fail = self._gate_season(title_text)
        if fail:
            out["hard_fail"] = fail
            return out

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
        # Per-term weighted contribution + whether it hit the title — reused for
        # routing so classification uses the same title-boosted signal.
        term_score = {}
        title_terms = set()

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
            for c in th:
                contrib = w * self.title_multiplier
                term_score[c] = max(term_score.get(c, 0), contrib)
                title_terms.add(c)
            for c in bh:
                term_score[c] = max(term_score.get(c, 0), w)

        score = round(title_score + body_score)
        out["score"] = int(score)
        out["matched"] = sorted(matched)
        out["route"] = self._classify_route(term_score, title_terms)

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

    def _classify_route(self, term_score, title_terms):
        """Pick the channel with the strongest signal.

        Title-owned terms decide first; if no routed term is in the title, the
        body decides; ties break by `route_priority` order; if nothing routes,
        fall back to the default channel.
        """
        def best(scores):
            # scores: {channel: total}. Return highest, tie-break by priority.
            candidates = [(c, s) for c, s in scores.items() if s > 0]
            if not candidates:
                return None

            def rank(item):
                channel, s = item
                try:
                    pri = self.route_priority.index(channel)
                except ValueError:
                    pri = len(self.route_priority)
                return (-s, pri)

            return sorted(candidates, key=rank)[0][0]

        title_scores = {}
        all_scores = {}
        for term, sc in term_score.items():
            channel = self.route_of.get(term)
            if not channel:
                continue
            all_scores[channel] = all_scores.get(channel, 0) + sc
            if term in title_terms:
                title_scores[channel] = title_scores.get(channel, 0) + sc

        return best(title_scores) or best(all_scores) or self.route_default

    def score_jobs(self, jobs):
        return [self.score_job(j) for j in jobs]
