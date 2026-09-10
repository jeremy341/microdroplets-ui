from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SensorPauseStaticTests(unittest.TestCase):
    def test_pause_button_is_left_of_fit_and_uses_pause_handler(self):
        source = (ROOT / "ui" / "pages" / "Sensors.py").read_text(encoding="utf-8")
        pause_pos = source.index('QPushButton("Pause")')
        fit_pos = source.index('QPushButton("Fit data")')
        live_pos = source.index('QPushButton("Live view")')
        self.assertLess(pause_pos, fit_pos)
        self.assertLess(fit_pos, live_pos)
        self.assertIn('self.pause_button.clicked.connect(self.chart_pause)', source)
        self.assertIn('self.chart.pause_view()', source)

    def test_pause_is_view_only_and_does_not_set_ranges(self):
        source = (ROOT / "ui" / "pages" / "Sensors.py").read_text(encoding="utf-8")
        block = source.split("    def pause_view(self):", 1)[1].split("    def resume_live(self):", 1)[0]
        self.assertIn("self._follow_live = False", block)
        self.assertIn("self._manual_scale = True", block)
        self.assertNotIn("setXRange", block)
        self.assertNotIn("setYRange", block)
        self.assertNotIn("set_recording", block)

    def test_right_axis_autoscale_is_suppressed_during_manual_pause(self):
        source = (ROOT / "ui" / "pages" / "Sensors.py").read_text(encoding="utf-8")
        self.assertIn("if not self._manual_scale:\n                    right_values", source)

    def test_pause_reuses_exact_fit_button_qss_groups(self):
        qss = (ROOT / "style.qss").read_text(encoding="utf-8")
        for suffix in ("", ":hover", ":pressed"):
            self.assertIn(f"QPushButton#sensorPauseButton{suffix},\nQPushButton#sensorFitButton{suffix},", qss)
        self.assertIn(
            "QWidget#sensorsPage QPushButton#sensorPauseButton,\n"
            "QWidget#sensorsPage QPushButton#sensorFitButton,",
            qss,
        )


if __name__ == "__main__":
    unittest.main()
