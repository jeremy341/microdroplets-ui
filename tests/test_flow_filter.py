import unittest

from backend.flow_filter import FlowSignalFilter


class FlowSignalFilterTests(unittest.TestCase):
    def test_default_response_is_faster_than_v14(self):
        filt = FlowSignalFilter()
        filt.update(4000.0, None)
        self.assertEqual(filt.update(4000.0, 1.0), 4000.0)
        filt.update(2000.0, 0.35)
        value = filt.update(2000.0, 0.35)
        self.assertLess(value, 3000.0)

    def test_isolated_motion_spike_does_not_start_flow(self):
        filt = FlowSignalFilter()
        self.assertEqual(filt.update(0.0, None), 0.0)
        self.assertEqual(filt.update(12000.0, 1.0), 0.0)
        self.assertEqual(filt.update(0.0, 1.0), 0.0)
        self.assertFalse(filt.active)

    def test_sustained_flow_is_confirmed_and_smoothed(self):
        filt = FlowSignalFilter()
        self.assertEqual(filt.update(0.0, None), 0.0)
        self.assertEqual(filt.update(4000.0, 1.0), 0.0)
        self.assertEqual(filt.update(4000.0, 1.0), 4000.0)
        filt.update(2000.0, 1.0)
        value = filt.update(2000.0, 1.0)
        self.assertGreater(value, 2000.0)
        self.assertLess(value, 4000.0)

    def test_small_zero_noise_is_deadbanded(self):
        filt = FlowSignalFilter()
        for value in (20.0, 50.0, 74.0, 0.0):
            self.assertEqual(filt.update(value, 1.0), 0.0)

    def test_sustained_negative_flow_is_preserved_and_smoothed(self):
        filt = FlowSignalFilter()
        self.assertEqual(filt.update(-1000.0, None), 0.0)
        self.assertEqual(filt.update(-1000.0, 1.0), -1000.0)
        filt.update(-2000.0, 1.0)
        value = filt.update(-2000.0, 1.0)
        self.assertLess(value, -1000.0)
        self.assertGreater(value, -2000.0)

    def test_small_signed_zero_noise_is_deadbanded(self):
        filt = FlowSignalFilter()
        for value in (-74.0, -20.0, 0.0, 20.0, 74.0):
            self.assertEqual(filt.update(value, 1.0), 0.0)


if __name__ == "__main__":
    unittest.main()
