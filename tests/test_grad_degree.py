"""Unit tests for the graduate-degree requirement detector. Hermetic."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from grad_degree import assess_graduate_requirement as assess  # noqa: E402


class TestGradRequired(unittest.TestCase):
    """Cases that SHOULD be flagged as requiring a graduate degree."""

    def test_masters_required_plain(self):
        r = assess("HW Eng", "Minimum Qualifications: Master's degree in "
                             "Electrical Engineering. Strong C++ skills.")
        self.assertTrue(r["required"])
        self.assertEqual(r["level"], "masters")

    def test_phd_required_language(self):
        r = assess("Research Eng", "PhD in Computer Science is required. "
                                   "Experience with SLAM.")
        self.assertTrue(r["required"])
        self.assertEqual(r["level"], "phd")

    def test_masters_or_higher(self):
        r = assess("ASIC", "Requirements: Master's degree or higher in EE.")
        self.assertTrue(r["required"])

    def test_phd_intern_enrolled(self):
        r = assess("Research Intern",
                   "Basic Qualifications: Currently enrolled in a PhD program "
                   "in Computer Engineering.")
        self.assertTrue(r["required"])
        self.assertEqual(r["level"], "phd")

    def test_phd_wins_over_masters(self):
        r = assess("Eng", "Required: Master's degree in EE. PhD required for "
                          "the modeling track.")
        self.assertTrue(r["required"])
        self.assertEqual(r["level"], "phd")

    def test_ms_in_field_required_section(self):
        r = assess("DSP", "Required Qualifications: MS in Electrical "
                          "Engineering with DSP focus.")
        self.assertTrue(r["required"])


class TestNotRequired(unittest.TestCase):
    """Cases that should NOT be flagged."""

    def test_masters_preferred_qualifier(self):
        r = assess("HW Eng", "Bachelor's degree in EE. Master's degree "
                             "preferred.")
        self.assertFalse(r["required"])

    def test_bachelors_or_masters_alternative(self):
        r = assess("FW Eng", "Minimum Qualifications: Bachelor's or Master's "
                             "degree in Computer Science.")
        self.assertFalse(r["required"])

    def test_bs_ms_slash(self):
        r = assess("SoC", "Requirements: BS/MS in Electrical Engineering.")
        self.assertFalse(r["required"])

    def test_grad_in_preferred_section(self):
        r = assess("Perception",
                   "Minimum Qualifications: Bachelor's degree in CS. "
                   "Preferred Qualifications: Master's or PhD in robotics.")
        self.assertFalse(r["required"])

    def test_phd_a_plus(self):
        r = assess("SWE", "BS in CS required. PhD is a plus.")
        self.assertFalse(r["required"])

    def test_intern_pursuing_bachelors_or_masters(self):
        r = assess("Intern", "Currently pursuing a Bachelor's, Master's, or "
                            "PhD in Electrical Engineering.")
        self.assertFalse(r["required"])

    def test_no_degree_mention(self):
        r = assess("Tech", "Strong soldering and bench debugging skills. "
                          "Experience with oscilloscopes.")
        self.assertFalse(r["required"])

    def test_no_false_positive_on_ms_office(self):
        r = assess("Coord", "Proficiency in MS Office and CMS platforms "
                          "required. Systems thinking a plus.")
        self.assertFalse(r["required"])

    def test_bachelors_required_masters_preferred_same_clause(self):
        r = assess("EE", "Bachelor's degree required; Master's or PhD "
                        "preferred.")
        self.assertFalse(r["required"])


class TestNotifyGradMarker(unittest.TestCase):
    """The Discord embed marks grad-required jobs and stays clean otherwise."""

    def _embed(self, job):
        import notify
        return notify.build_embed(job, 10)

    def test_grad_required_marks_title_and_field(self):
        e = self._embed({"title": "Research Intern", "company": "X",
                         "score": 12, "grad": {"required": True,
                                               "level": "phd"}})
        self.assertTrue(e["title"].startswith("⚠️"))
        self.assertTrue(any(f["name"] == "Degree" and "PhD" in f["value"]
                            for f in e["fields"]))

    def test_not_required_is_clean(self):
        e = self._embed({"title": "FW Intern", "company": "Y", "score": 12,
                         "grad": {"required": False}})
        self.assertFalse(e["title"].startswith("⚠️"))
        self.assertFalse(any(f["name"] == "Degree" for f in e["fields"]))

    def test_missing_grad_key_is_safe(self):
        e = self._embed({"title": "Z", "company": "Z", "score": 5})
        self.assertEqual(e["title"], "Z")


if __name__ == "__main__":
    unittest.main()
