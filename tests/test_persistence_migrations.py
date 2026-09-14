# -*- coding: utf-8 -*-
import unittest

from services.persistence import migrations as m


class MigrationFrameworkTests(unittest.TestCase):
    def setUp(self):
        # Snapshot and clear so these framework tests run on an empty registry, then
        # RESTORE in tearDown — other modules (e.g. services.session.settings) register
        # real stores at import time, and clobbering them would break their tests when
        # the suite runs in one process.
        self._saved = dict(m._REGISTRY)
        m.reset_for_tests()

    def tearDown(self):
        m._REGISTRY.clear()
        m._REGISTRY.update(self._saved)

    def test_unregistered_store_is_noop(self):
        data = {"a": 1}
        self.assertEqual(m.migrate("nope", data), {"a": 1})
        self.assertEqual(m.current_version("nope"), 0)

    def test_ordered_apply_from_legacy_zero(self):
        m.register("s", 1, lambda d: {**d, "one": True})
        m.register("s", 2, lambda d: {**d, "two": True})
        out = m.migrate("s", {"orig": 1})  # no version key -> treated as 0
        self.assertTrue(out["one"] and out["two"])
        self.assertEqual(out[m.VERSION_KEY], 2)

    def test_idempotent_when_already_current(self):
        calls = []
        m.register("s", 1, lambda d: calls.append(1) or d)
        first = m.migrate("s", {})
        self.assertEqual(first[m.VERSION_KEY], 1)
        second = m.migrate("s", first)
        self.assertEqual(second[m.VERSION_KEY], 1)
        self.assertEqual(calls, [1])  # step ran exactly once

    def test_partial_upgrade_runs_only_missing_steps(self):
        ran = []
        m.register("s", 1, lambda d: ran.append(1) or d)
        m.register("s", 2, lambda d: ran.append(2) or d)
        m.register("s", 3, lambda d: ran.append(3) or d)
        m.migrate("s", {m.VERSION_KEY: 1})
        self.assertEqual(ran, [2, 3])

    def test_decorator_registration(self):
        @m.migration("s", 1)
        def _up(d):
            d["x"] = 9
            return d

        self.assertEqual(m.migrate("s", {})["x"], 9)

    def test_missing_intermediate_step_raises(self):
        m.register("s", 2, lambda d: d)  # no v1
        with self.assertRaises(KeyError):
            m.migrate("s", {})

    def test_duplicate_registration_raises(self):
        m.register("s", 1, lambda d: d)
        with self.assertRaises(ValueError):
            m.register("s", 1, lambda d: d)

    def test_stamp_sets_current(self):
        m.register("s", 1, lambda d: d)
        m.register("s", 2, lambda d: d)
        self.assertEqual(m.stamp("s", {})[m.VERSION_KEY], 2)

    def test_corrupt_version_treated_as_zero(self):
        ran = []
        m.register("s", 1, lambda d: ran.append(1) or d)
        m.migrate("s", {m.VERSION_KEY: "garbage"})
        self.assertEqual(ran, [1])


if __name__ == "__main__":
    unittest.main()
