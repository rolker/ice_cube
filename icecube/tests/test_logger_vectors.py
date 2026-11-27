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

    def _case(self, part, name):
        return next(c for c in json.loads(logger_vectors.render())[part] if c["name"] == name)

    def _node(self, case, i, j):
        return next(n for n in case["nodes"] if (n["i"], n["j"]) == (i, j))

    def test_rule_cases_do_what_they_are_named_for(self):
        n = self._node(self._case("nodes", "count_promotion"), 0, 0)
        self.assertEqual((n["n_in"], n["n_out"]), (3, 1))
        self.assertGreater(n["z"], 0.29)
        n = self._node(self._case("nodes", "equal_weight_pair"), 0, 0)
        self.assertEqual((n["z"], n["n_in"], n["n_out"]), (0.1, 1, 1))
        n = self._node(self._case("nodes", "equidistant_pair_id_tie"), 0, 0)
        self.assertAlmostEqual(n["z"], -0.0625, places=12)    # id 7 (-12.5 cm) joined the main first
        for name, n_in, z in (("over_cap_takeover", 34, 0.5), ("over_cap_drop", 40, 0.2)):
            n = self._node(self._case("nodes", name), 0, 0)
            self.assertEqual(n["n_in"], n_in, name)
            self.assertAlmostEqual(n["z"], z, delta=0.005)

    def test_sequences_clear_and_flicker(self):
        def states(case):
            cur, out = {}, []
            for step in self._case("sequences", case)["steps"]:
                for t in step:
                    i, j, st = t.split()
                    cur[(i, j)] = st
                out.append(list(cur.values()))
            return out

        def red(step):
            return step.count("disagree")
        steps = states("late_outlier_clears")
        self.assertGreater(red(steps[1]), 0)
        self.assertEqual(red(steps[-1]), 0)
        steps = states("flicker")
        self.assertEqual([red(s) > 0 for s in steps], [False, True, False, True, True])
        last = states("strips_gap_and_fringe")[-1]
        self.assertGreater(last.count("no_data"), 0)
        self.assertGreater(last.count("fringe"), 0)

    def test_plane_cases_do_what_they_are_named_for(self):
        from icecube import nodes
        doc = json.loads(logger_vectors.render())
        self.assertEqual(doc["plane_params"]["plane_min_clear"], nodes.PLANE_DEFAULTS["min_clear"])

        def centre(name):
            return self._node(self._case("plane_vectors", name), 2, 2)
        for name, g in (("bank_0.3_clears", 0.3), ("bank_0.2_clears", 0.2)):
            n = centre(name)
            self.assertEqual(n["code"], nodes.PLANE_ACCEPTED, name)
            self.assertTrue(nodes.is_red(n["n"] - n["n_out1"], n["n_out1"]), name)
            self.assertFalse(nodes.is_red(n["n_in"], n["n_out"]), name)
            self.assertAlmostEqual(n["gradient"], g, delta=0.06)
        self.assertFalse(any(n["planar"] for n in self._case("plane_vectors", "flat_edge_outlier")["nodes"]))
        n = centre("wrong_cluster_keeps_pass1")
        self.assertEqual(n["code"], nodes.PLANE_OFF_PLANE)
        self.assertEqual((n["z"], n["n_out"]), (n["z1"], n["n_out1"]))
        # its fringe node (2, -2) has bunched neighbour centroids: not accepted
        self.assertEqual(self._node(self._case("plane_vectors", "wrong_cluster_keeps_pass1"), 2, -2)["code"],
                         nodes.PLANE_ILL_CONDITIONED)
        n = self._node(self._case("plane_vectors", "fringe_ill_conditioned"), 2, 0)
        self.assertEqual(n["code"], nodes.PLANE_ILL_CONDITIONED)
        self.assertTrue(nodes.is_red(n["n"] - n["n_out1"], n["n_out1"]))
        self.assertEqual(centre("ridge_not_planar")["code"], nodes.PLANE_NOT_PLANAR)
        n = centre("flat_cluster_not_steep")
        self.assertEqual(n["code"], nodes.PLANE_NOT_STEEP)
        self.assertTrue(nodes.is_red(n["n"] - n["n_out1"], n["n_out1"]))
        n = centre("collinear_ill_conditioned")
        self.assertEqual(n["code"], nodes.PLANE_ILL_CONDITIONED)
        self.assertTrue(nodes.is_red(n["n"] - n["n_out1"], n["n_out1"]))


if __name__ == "__main__":
    unittest.main()
