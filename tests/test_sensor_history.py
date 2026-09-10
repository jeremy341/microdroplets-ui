from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSOR_SOURCE = (ROOT / "ui" / "pages" / "Sensors.py").read_text(encoding="utf-8")


def test_live_history_is_ten_minutes_not_fixed_sample_count():
    assert "LIVE_HISTORY_SECONDS = 10 * 60" in SENSOR_SOURCE
    assert "self.histories[key] = self.histories[key][-300:]" not in SENSOR_SOURCE
    assert "self.history_timestamps[key] = self.history_timestamps[key][-300:]" not in SENSOR_SOURCE


def test_history_trimming_only_happens_while_chart_is_live():
    assert "if self.chart.is_live_view:" in SENSOR_SOURCE
    assert "self._trim_history_to_live_window(key)" in SENSOR_SOURCE


def test_pause_and_fit_disable_live_follow_so_history_is_preserved():
    pause_block = SENSOR_SOURCE.split("def pause_view(self):", 1)[1].split("def resume_live", 1)[0]
    fit_block = SENSOR_SOURCE.split("def fit_data(self):", 1)[1].split("def reset_zoom", 1)[0]
    assert "self._follow_live = False" in pause_block
    assert "self._follow_live = False" in fit_block


def test_live_view_prunes_history_before_resuming():
    block = SENSOR_SOURCE.split("def chart_auto_scale(self):", 1)[1].split("@staticmethod", 1)[0]
    assert "self._trim_all_histories_to_live_window()" in block
    assert "self.refresh_chart()" in block
    assert "self.chart.reset_zoom()" in block
    assert block.index("_trim_all_histories_to_live_window") < block.index("reset_zoom")


def test_sensor_logging_path_is_not_part_of_history_trimming():
    trim_block = SENSOR_SOURCE.split("def _trim_history_to_live_window", 1)[1].split(
        "def _trim_all_histories_to_live_window", 1
    )[0]
    assert "csv_logger" not in trim_block
    assert "session_rows" not in trim_block
    assert "_queue_log_sample" not in trim_block
