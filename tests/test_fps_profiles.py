import unittest

from backend.fps_profiles import (
    format_fps_option,
    rounded_ui_ceiling,
    stable_fps,
    target_is_usable,
    ui_fps_options,
)


class FpsProfileTests(unittest.TestCase):
    def test_stable_fps_ignores_one_high_and_one_low_outlier(self):
        self.assertEqual(stable_fps([30.0, 30.1, 30.2, 43.48, 8.0]), 30.1)

    def test_ten_fps_steps_use_normal_half_up_rounding(self):
        self.assertEqual(rounded_ui_ceiling(43.48), 40)
        self.assertEqual(rounded_ui_ceiling(45.0), 50)
        self.assertEqual(rounded_ui_ceiling(44.99), 40)
        self.assertEqual(rounded_ui_ceiling(29.0), 30)
        self.assertEqual(rounded_ui_ceiling(30.05), 30)
        self.assertEqual(rounded_ui_ceiling(8.0), 10)
        self.assertEqual(ui_fps_options(43.48), [10, 20, 30, 40])
        self.assertEqual(ui_fps_options(30.05), [10, 20, 30])
        self.assertEqual(ui_fps_options(8.0), [10])

    def test_target_usability_is_separate_from_35_percent_warning(self):
        self.assertTrue(target_is_usable(40, 36.0))
        self.assertFalse(target_is_usable(40, 30.0))
        self.assertEqual(format_fps_option(10, 8.0), "10 FPS · actual ≈ 8")


if __name__ == "__main__":
    unittest.main()
