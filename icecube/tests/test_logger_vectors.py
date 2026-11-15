"""The committed logger coverage vectors match a regeneration (icecube.logger_vectors).

The generator imports analysis/qgis/soundings.py (standard library only) through sys.path.
"""
import json
import unittest

from icecube import logger_vectors


class TestLoggerVectors(unittest.TestCase):
    def test_committed_file_is_current(self):
        committed = logger_vectors.DEFAULT_OUT.read_text()
        self.assertEqual(committed, logger_vectors.render(),
                         "regenerate: python3 -m icecube.logger_vectors --out " + str(logger_vectors.DEFAULT_OUT))

    def test_tilt_rule_drops_the_pole_tilt_term(self):
        doc = json.loads(logger_vectors.render())
        case = next(c for c in doc["sigma"] if c["name"] == "tilt_capture")
        sg = doc["sigma_defaults"]
        self.assertAlmostEqual(case["expected"]["sigma_pole"], sg["tip_sink"], places=12)
        self.assertAlmostEqual(case["expected"]["sigma_rtk"], case["input"]["tilt_sd_u"], places=12)


if __name__ == "__main__":
    unittest.main()
