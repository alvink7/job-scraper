"""Unit tests for the scoring engine and fetch mapping. Hermetic (no network).

Run:  python -m unittest discover -s tests -v
  or: python -m pytest tests/
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch  # noqa: E402
from match import Matcher  # noqa: E402


# A small, hermetic config so tests don't depend on config.yaml.
CONFIG = {
    "matching": {
        "title_multiplier": 2.0,
        "body_cap_per_category": 3,
        "min_score": 10,
        "weights": {"core": 5, "strong": 3, "supporting": 1, "negative": -2},
    },
    "gates": {
        "career_stage": "intern_or_newgrad",
        "max_years": 3,
        "exclude_title": [
            "senior", "staff", "principal", "lead", "manager", "director",
            "sr.", "sr", "vp", "head of", "fellow", "ii", "iii", "iv",
            "architect",
        ],
        "location_any": [
            "united states", "usa", "remote", "california", ", ca",
            "san francisco", "mountain view", "new york", ", ny",
        ],
        "min_domain_score": 3,
    },
    "routes": {
        "default": "software_and_firmware",
        "priority": ["autonomous_driving", "hardware", "software_and_firmware"],
        "channels": {
            "autonomous_driving": {
                "webhook_env": "DISCORD_WEBHOOK_AUTONOMY",
                "terms": ["lidar", "radar", "point cloud", "sensor fusion",
                          "perception", "slam", "ros"],
            },
            "hardware": {
                "webhook_env": "DISCORD_WEBHOOK_HARDWARE",
                "terms": ["pcb"],
            },
            "software_and_firmware": {
                "webhook_env": "DISCORD_WEBHOOK_SWFW",
                "terms": ["firmware", "can bus", "c++", "python", "linux",
                          "i2c/spi/uart"],
            },
        },
    },
    "keywords": {
        "core": {
            "lidar": ["lidar", "li-dar", "laser scanning"],
            "radar": ["radar", "fmcw"],
            "point cloud": ["point cloud", "pointcloud"],
            "sensor fusion": ["sensor fusion", "multi-sensor fusion"],
            "perception": ["perception"],
            "slam": ["slam"],
        },
        "strong": {
            "cuda": ["cuda", "gpu kernel"],
            "ros": ["ros", "ros2", "ros 2"],
            "firmware": ["firmware"],
            "can bus": ["can bus", "canbus"],
        },
        "supporting": {
            "c++": ["c++", "cpp", "c/c++"],
            "python": ["python", "numpy"],
            "linux": ["linux"],
            "i2c/spi/uart": ["i2c", "spi", "uart"],
            "pcb": ["pcb"],
            "matlab": ["matlab"],
            "git": ["git"],
        },
        "negative": {
            "sales": ["account executive"],
        },
    },
}


def job(title="", content="", location="Remote (US)", company="X", url="u"):
    return {
        "id": "t:1", "company": company, "title": title, "location": location,
        "url": url, "content": content, "updated": "",
    }


class TestBoundaries(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_can_bus_hits_but_scan_does_not(self):
        r = self.m.score_job(job("Firmware Intern", "CAN bus firmware work"))
        self.assertIn("can bus", r["matched"])
        r2 = self.m.score_job(job("Intern", "scan the network for hosts"))
        self.assertNotIn("can bus", r2["matched"])

    def test_ros_not_in_across(self):
        r = self.m.score_job(job("Intern", "across the room we walked"))
        self.assertNotIn("ros", r["matched"])

    def test_laser_scanning_alias_hits(self):
        r = self.m.score_job(job("Intern", "laser scanning of documents"))
        self.assertIn("lidar", r["matched"])

    def test_cpp_and_python_hit_lone_c_does_not(self):
        r = self.m.score_job(job("Intern", "c/c++ and python required"))
        self.assertIn("c++", r["matched"])
        self.assertIn("python", r["matched"])
        r2 = self.m.score_job(job("Intern", "take your vitamin c daily"))
        self.assertNotIn("c++", r2["matched"])

    def test_seniority_token_not_in_word(self):
        # "iv" inside "drive" must not trip the seniority gate.
        r = self.m.score_job(job("Perception Intern", "drive unit calibration"))
        self.assertEqual(r["hard_fail"], "")

    def test_ai_not_in_chair(self):
        # No "ai" keyword configured to falsely hit "chairperson".
        r = self.m.score_job(job("Intern", "chairperson of the committee"))
        # perception/etc should not appear
        self.assertEqual(r["matched"], [])


class TestWeighting(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_title_worth_multiplier_of_body(self):
        title_hit = self.m.score_job(job("LiDAR Intern", "internship"))
        body_hit = self.m.score_job(job("Intern", "lidar internship"))
        # title: 5*2*1 = 10 ; body: 5*1 = 5
        self.assertEqual(title_hit["score"], 10)
        self.assertEqual(body_hit["score"], 5)

    def test_title_only_clears_min_score(self):
        # Simulates a Workday row with empty body.
        r = self.m.score_job(job("LiDAR Perception Intern", ""))
        # lidar + perception in title: (5+5)*2 = 20
        self.assertGreaterEqual(r["score"], CONFIG["matching"]["min_score"])
        self.assertFalse(r["partial"])

    def test_core_title_outscores_supporting_title(self):
        core = self.m.score_job(job("LiDAR Intern", "internship"))
        supp = self.m.score_job(job("Python Intern", "internship"))
        self.assertGreater(core["score"], supp["score"])

    def test_body_cap(self):
        # 7 supporting terms in body; cap is 3 -> at most 3*1 = 3.
        body = "python c++ linux i2c spi pcb matlab git internship"
        r = self.m.score_job(job("Intern", body))
        # supporting contributes min(distinct,3)*1 ; ensure not > 3 from supporting
        # core/strong contribute 0 here, so total should be exactly 3.
        self.assertEqual(r["score"], 3)


class TestSeniorityGate(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_senior_fails(self):
        for t in ["Senior LiDAR Engineer", "Staff Perception Engineer",
                  "Perception Manager", "Engineer III"]:
            r = self.m.score_job(job(t, "lidar internship"))
            self.assertTrue(r["hard_fail"].startswith("excluded title term"),
                            f"{t} should fail, got {r['hard_fail']!r}")

    def test_junior_passes(self):
        for t in ["LiDAR Engineer, New Grad", "Perception Intern"]:
            r = self.m.score_job(job(t, "lidar new grad role"))
            self.assertEqual(r["hard_fail"], "", f"{t} should pass")


class TestCareerGate(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_years_requirement_fails(self):
        r = self.m.score_job(job("LiDAR Engineer", "5+ years required. lidar."))
        self.assertIn("years", r["hard_fail"])

    def test_internship_passes(self):
        r = self.m.score_job(job("LiDAR Engineer", "internship, summer 2027. lidar"))
        self.assertEqual(r["hard_fail"], "")
        self.assertFalse(r["partial"])

    def test_no_signal_no_years_is_partial(self):
        r = self.m.score_job(job("LiDAR Engineer", "work on lidar perception"))
        self.assertEqual(r["hard_fail"], "")
        self.assertTrue(r["partial"])


class TestInternOnlyGate(unittest.TestCase):
    def setUp(self):
        import copy
        cfg = copy.deepcopy(CONFIG)
        cfg["gates"]["career_stage"] = "intern_only"
        # add an ML title exclusion to verify ML roles are dropped
        cfg["gates"]["exclude_title"] = cfg["gates"]["exclude_title"] + [
            "machine learning", "deep learning", "research scientist"]
        self.m = Matcher(cfg)

    def test_internship_passes(self):
        r = self.m.score_job(job("LiDAR Perception Intern", "lidar internship"))
        self.assertEqual(r["hard_fail"], "")
        self.assertFalse(r["partial"])

    def test_new_grad_fails(self):
        # New-grad is NOT an internship -> rejected in intern_only mode.
        r = self.m.score_job(job("LiDAR Engineer, New Grad", "lidar new grad role"))
        self.assertEqual(r["hard_fail"], "not an internship")

    def test_plain_role_no_signal_fails(self):
        r = self.m.score_job(job("Firmware Engineer", "work on firmware and can bus"))
        self.assertEqual(r["hard_fail"], "not an internship")

    def test_years_still_fails(self):
        r = self.m.score_job(job("Firmware Intern", "internship. 5+ years required"))
        self.assertIn("years", r["hard_fail"])

    def test_ml_title_excluded(self):
        r = self.m.score_job(job("Machine Learning Intern", "internship. lidar"))
        self.assertTrue(r["hard_fail"].startswith("excluded title term"))


class TestLocationGate(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_foreign_location_fails(self):
        r = self.m.score_job(job("LiDAR Intern", "lidar internship",
                                 location="London, UK"))
        self.assertEqual(r["hard_fail"], "location not in allowlist")

    def test_remote_us_passes(self):
        r = self.m.score_job(job("LiDAR Intern", "lidar internship",
                                 location="Remote (US)"))
        self.assertEqual(r["hard_fail"], "")

    def test_empty_location_with_body_city_passes(self):
        r = self.m.score_job(job("LiDAR Intern",
                                 "lidar internship in Mountain View",
                                 location=""))
        self.assertEqual(r["hard_fail"], "")


class TestNoiseFloor(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_only_python_dropped(self):
        # python (supporting=1) in body only -> score 1 < min_domain_score 3.
        r = self.m.score_job(job("Intern", "python internship"))
        self.assertEqual(r["hard_fail"], "below domain floor (noise)")


class TestRankingAndDeterminism(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_end_to_end_ranking(self):
        jobs = [
            job("LiDAR Perception Intern", "lidar sensor fusion internship"),   # high
            job("Firmware Intern", "can bus firmware internship"),              # mid
            job("Software Intern", "python and git internship"),                # noise
            job("Senior LiDAR Engineer", "lidar internship"),                   # fail
            job("Radar Intern", "radar perception work"),                       # partial-ish
            job("Sales Intern", "account executive internship"),                # noise/neg
        ]
        scored = self.m.score_jobs(jobs)
        sent = [j for j in scored if not j["hard_fail"] and j["score"] > 0]
        sent.sort(key=lambda j: j["score"], reverse=True)
        # Top must be the lidar+fusion perception title role.
        self.assertIn("LiDAR Perception Intern", sent[0]["title"])
        # Senior role is hard-failed, not in sent.
        self.assertTrue(any(j["hard_fail"] for j in scored
                            if "Senior" in j["title"]))
        # Scores strictly non-increasing.
        scores = [j["score"] for j in sent]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_determinism(self):
        j = job("LiDAR Perception Intern", "lidar sensor fusion internship")
        a = self.m.score_job(j)
        b = self.m.score_job(j)
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(a["matched"], b["matched"])


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.m = Matcher(CONFIG)

    def test_perception_role_routes_to_autonomy(self):
        r = self.m.score_job(job("LiDAR Perception Intern", "lidar internship"))
        self.assertEqual(r["route"], "autonomous_driving")

    def test_firmware_role_routes_to_swfw(self):
        r = self.m.score_job(job("Firmware Intern", "can bus firmware internship"))
        self.assertEqual(r["route"], "software_and_firmware")

    def test_pcb_role_routes_to_hardware(self):
        r = self.m.score_job(job("PCB Design Intern", "pcb layout internship"))
        self.assertEqual(r["route"], "hardware")

    def test_title_decides_over_body(self):
        # Firmware in the title, lidar only in the body -> firmware channel.
        r = self.m.score_job(job("Firmware Intern",
                                 "work near the lidar perception team. internship"))
        self.assertEqual(r["route"], "software_and_firmware")

    def test_route_is_deterministic(self):
        j = job("LiDAR Perception Intern", "lidar sensor fusion internship")
        self.assertEqual(self.m.score_job(j)["route"],
                         self.m.score_job(j)["route"])


class TestFetchMapping(unittest.TestCase):
    def test_strip_html(self):
        self.assertEqual(
            fetch._strip_html("<p>Hello&nbsp;<b>world</b></p>"),
            "Hello world",
        )

    def test_map_lever(self):
        payload = [{
            "id": "abc", "text": "LiDAR Intern",
            "categories": {"location": "SF"},
            "hostedUrl": "https://x/apply",
            "descriptionPlain": "Work on <b>lidar</b>",
            "lists": [{"text": "Reqs", "content": "<li>python</li>"}],
            "createdAt": 123,
        }]
        jobs = fetch.map_lever("Zoox", "zoox", payload)
        self.assertEqual(jobs[0]["id"], "lever:zoox:abc")
        self.assertEqual(jobs[0]["title"], "LiDAR Intern")
        self.assertEqual(jobs[0]["location"], "SF")
        self.assertIn("lidar", jobs[0]["content"].lower())
        self.assertIn("Reqs", jobs[0]["content"])

    def test_map_greenhouse(self):
        payload = {"jobs": [{
            "id": 7, "title": "Perception Intern",
            "location": {"name": "Remote"},
            "absolute_url": "https://x/7",
            "content": "<p>lidar &amp; radar</p>",
            "updated_at": "2026-01-01",
        }]}
        jobs = fetch.map_greenhouse("Skydio", "skydio", payload)
        self.assertEqual(jobs[0]["id"], "greenhouse:skydio:7")
        self.assertEqual(jobs[0]["location"], "Remote")
        self.assertIn("lidar & radar", jobs[0]["content"])

    def test_map_ashby(self):
        payload = {"jobs": [{
            "id": "u1", "title": "Robotics Intern", "location": "NYC",
            "jobUrl": "https://x/u1",
            "descriptionPlain": "ros2 and slam",
            "publishedAt": "2026-02-02",
        }]}
        jobs = fetch.map_ashby("Cobot", "cobot", payload)
        self.assertEqual(jobs[0]["id"], "ashby:cobot:u1")
        self.assertEqual(jobs[0]["content"], "ros2 and slam")

    def test_map_workday(self):
        posting = {
            "title": "New Grad HW Engineer",
            "locationsText": "Santa Clara, CA",
            "externalPath": "/job/Santa-Clara/New-Grad_JR123",
            "postedOn": "Posted Today",
        }
        j = fetch.map_workday_posting(
            "NVIDIA", "nvidia", 5, "NVIDIAExternalCareerSite", posting)
        self.assertEqual(j["id"],
                         "workday:nvidia:/job/Santa-Clara/New-Grad_JR123")
        self.assertTrue(j["url"].startswith(
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"))
        self.assertEqual(j["location"], "Santa Clara, CA")


if __name__ == "__main__":
    unittest.main()
