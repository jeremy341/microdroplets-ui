import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.session_manager import (
    SCHEMA_VERSION,
    SessionError,
    SessionManager,
    create_default_session,
    match_board_profile,
    validate_session,
)


class SessionManagerTests(unittest.TestCase):
    def test_new_session_stays_in_memory_until_save(self):
        manager = SessionManager()
        manager.current_session["session"]["name"] = "Unsaved"
        manager.mark_dirty()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            self.assertFalse(path.exists())
            manager.discard()
            self.assertFalse(path.exists())
            self.assertIsNone(manager.session_path)

    def test_first_save_creates_new_file_and_clears_dirty(self):
        manager = SessionManager()
        manager.current_session["session"]["name"] = "Experiment"
        manager.mark_dirty()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            result = manager.save(path)
            self.assertEqual(result.path, path)
            self.assertTrue(path.exists())
            self.assertFalse(manager.is_dirty)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], SCHEMA_VERSION)

    def test_loaded_session_is_not_changed_until_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            original = create_default_session("Original")
            original["board_profiles"] = []
            path.write_text(json.dumps(original), encoding="utf-8")

            manager = SessionManager()
            manager.load(path)
            manager.current_session["session"]["name"] = "Changed in RAM"
            manager.mark_dirty()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["session"]["name"], "Original")

            manager.discard()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["session"]["name"], "Original")

    def test_save_overwrites_loaded_session_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            manager = SessionManager()
            manager.save(path)
            manager.current_session["session"]["name"] = "Updated"
            manager.mark_dirty()
            result = manager.save()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["session"]["name"], "Updated")
            self.assertEqual(result.backup_path, Path(f"{path}.bak"))
            self.assertTrue(result.backup_path.exists())

    def test_save_as_does_not_change_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            original_path = Path(directory) / "one.json"
            second_path = Path(directory) / "two.json"
            manager = SessionManager()
            manager.save(original_path)
            manager.current_session["session"]["name"] = "Second"
            manager.save_as(second_path)
            self.assertEqual(json.loads(original_path.read_text(encoding="utf-8"))["session"]["name"], "Unnamed Session")
            self.assertEqual(json.loads(second_path.read_text(encoding="utf-8"))["session"]["name"], "Second")

    def test_active_hardware_states_are_forced_safe(self):
        session = create_default_session()
        session["board_profiles"] = [{
            "profile_id": "mb1",
            "display_name": "MB1",
            "last_known_port": "COM7",
            "device_type": "mp-Multiboard2",
            "pump_configuration": {"channels": [{
                "channel": 1,
                "driver_index": 0,
                "frequency_hz": 100,
                "amplitude_vpp": 100,
                "waveform": "Sinus",
                "enabled": True,
            }]},
            "valve_configuration": {"valves": [{"valve": 1, "open": True}]},
            "sensor_configuration": {"selected": [], "calibration": "water", "sample_rate_seconds": 1.0, "stream_active": True},
        }]
        manager = SessionManager(session)
        profile = manager.current_session["board_profiles"][0]
        self.assertFalse(profile["pump_configuration"]["channels"][0]["enabled"])
        self.assertFalse(profile["valve_configuration"]["valves"][0]["open"])
        self.assertNotIn("stream_active", profile["sensor_configuration"])

    def test_firmware_change_matches_and_marks_session_dirty(self):
        session = create_default_session()
        session["board_profiles"] = [{
            "profile_id": "mb1",
            "display_name": "MB1",
            "last_known_port": "COM7",
            "device_type": "mp-Multiboard2",
            "vendor_id": "10C4",
            "product_id": "EA60",
            "last_known_firmware": "v1",
            "pump_configuration": {"channels": []},
            "valve_configuration": {"valves": []},
            "sensor_configuration": {"selected": [], "calibration": "water", "sample_rate_seconds": 1.0},
        }]
        manager = SessionManager(session)
        manager.is_dirty = False
        results = manager.match_profiles([{
            "port": "COM7",
            "device_type": "mp-Multiboard2",
            "vendor_id": "10C4",
            "product_id": "EA60",
            "firmware": "v2",
        }])
        self.assertEqual(results[0].status, "firmware_changed")
        self.assertTrue(manager.is_dirty)
        self.assertEqual(manager.current_session["board_profiles"][0]["last_known_firmware"], "v2")

    def test_missing_profile_is_not_reported_as_connected(self):
        session = create_default_session()
        profile = {
            "profile_id": "mb1",
            "display_name": "MB1",
            "last_known_port": "COM7",
            "device_type": "mp-Multiboard2",
            "pump_configuration": {"channels": []},
            "valve_configuration": {"valves": []},
            "sensor_configuration": {"selected": [], "calibration": "water", "sample_rate_seconds": 1.0},
        }
        session["board_profiles"] = [profile]
        manager = SessionManager(session)
        results = manager.match_profiles([])
        self.assertEqual(results[0].status, "missing")
        self.assertEqual(manager.current_session["board_profiles"], [profile])

    def test_corrupt_file_recovers_from_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            backup = Path(f"{path}.bak")
            path.write_text("{broken", encoding="utf-8")
            backup.write_text(json.dumps(create_default_session("Backup")), encoding="utf-8")
            result = SessionManager().load(path)
            self.assertTrue(result.recovered_from_backup)
            self.assertEqual(result.session["session"]["name"], "Backup")

    def test_corrupt_file_is_repaired_immediately_after_backup_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            backup = Path(f"{path}.bak")
            path.write_text("{broken", encoding="utf-8")
            backup.write_text(json.dumps(create_default_session("Backup")), encoding="utf-8")
            result = SessionManager().load(path)
            self.assertTrue(result.recovered_from_backup)
            # The repaired file must be readable immediately, not only after
            # the next save; a crash before that save must not repeat the loss.
            repaired = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(repaired["session"]["name"], "Backup")

    def test_save_does_not_rotate_a_good_backup_over_a_corrupt_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            backup = Path(f"{path}.bak")
            good_backup = create_default_session("Last Good Save")
            path.write_text("{corrupted on disk", encoding="utf-8")
            backup.write_text(json.dumps(good_backup), encoding="utf-8")

            manager = SessionManager()
            manager.save_as(path)

            # The new save succeeded, and the previous good backup survived:
            # a corrupt primary must never overwrite it.
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["session"]["name"], "Unnamed Session")
            rotated = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(rotated["session"]["name"], "Last Good Save")

    def test_save_rotates_a_valid_primary_into_the_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            backup = Path(f"{path}.bak")
            previous = create_default_session("Previous")
            path.write_text(json.dumps(previous), encoding="utf-8")

            manager = SessionManager()
            manager.save_as(path)

            rotated = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(rotated["session"]["name"], "Previous")

    def test_failed_backup_copy_cannot_destroy_the_last_good_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            backup = Path(f"{path}.bak")
            path.write_text(json.dumps(create_default_session("Revision 1")), encoding="utf-8")
            backup.write_text(json.dumps(create_default_session("Revision 0")), encoding="utf-8")

            manager = SessionManager()
            manager.current_session["session"]["name"] = "Revision 2"
            manager.mark_dirty()

            def truncating_copy(source, destination, *args, **kwargs):
                # Reproduce copy2 semantics: the destination is truncated and
                # only partly written before the copy fails.
                Path(destination).write_text('{"truncated', encoding="utf-8")
                raise OSError("simulated copy failure")

            with mock.patch("shutil.copy2", truncating_copy):
                with self.assertRaises(SessionError) as caught:
                    manager.save_as(path)

            self.assertIn("simulated copy failure", str(caught.exception))
            # The last good backup survived the failed rotation, and so did the
            # previous revision of the primary: no revision is lost.
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8"))["session"]["name"], "Revision 0"
            )
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["session"]["name"], "Revision 1"
            )
            # Nothing was left behind by the interrupted rotation.
            self.assertEqual(
                sorted(item.name for item in Path(directory).iterdir()),
                sorted([backup.name, path.name]),
            )
            self.assertTrue(manager.is_dirty)

    def test_failed_target_replace_keeps_the_previous_revision_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            backup = Path(f"{path}.bak")
            path.write_text(json.dumps(create_default_session("Revision 1")), encoding="utf-8")

            manager = SessionManager()
            manager.current_session["session"]["name"] = "Revision 2"
            manager.mark_dirty()

            original_replace = Path.replace

            def failing_replace(self, target, *args, **kwargs):
                if Path(target) == path:
                    raise OSError("simulated replace failure")
                return original_replace(self, target, *args, **kwargs)

            with mock.patch.object(Path, "replace", failing_replace):
                with self.assertRaises(SessionError) as caught:
                    manager.save_as(path)

            self.assertIn("simulated replace failure", str(caught.exception))
            # The primary still loads as its previous revision, and the backup
            # still provides that same recoverable copy.
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["session"]["name"], "Revision 1"
            )
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8"))["session"]["name"], "Revision 1"
            )
            self.assertEqual(
                SessionManager().load(path).session["session"]["name"], "Revision 1"
            )
            # A failed save is never reported as saved, and leaves no litter.
            self.assertTrue(manager.is_dirty)
            self.assertIsNone(manager.session_path)
            self.assertEqual(
                sorted(item.name for item in Path(directory).iterdir()),
                sorted([backup.name, path.name]),
            )

    def test_unloadable_primary_is_not_rotated_over_the_good_backup(self):
        unloadable_primaries = {
            "unknown schema version": {
                "schema_version": SCHEMA_VERSION + 1,
                "session": {"name": "Future Format"},
                "board_profiles": [],
            },
            "absent schema version": {"name": "Legacy", "rows": []},
        }
        for label, payload in unloadable_primaries.items():
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "session.json"
                    backup = Path(f"{path}.bak")
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    backup.write_text(
                        json.dumps(create_default_session("Last Good Save")), encoding="utf-8"
                    )

                    result = SessionManager().save_as(path)

                    # The save itself succeeded ...
                    self.assertIsNone(result.backup_path)
                    self.assertEqual(
                        json.loads(path.read_text(encoding="utf-8"))["session"]["name"],
                        "Unnamed Session",
                    )
                    # ... without letting content the loader would reject
                    # displace the last good backup.
                    self.assertEqual(
                        json.loads(backup.read_text(encoding="utf-8"))["session"]["name"],
                        "Last Good Save",
                    )

    def test_legacy_session_is_migrated_in_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps({"name": "Legacy", "rows": []}), encoding="utf-8")
            result = SessionManager().load(path)
            self.assertTrue(result.migrated)
            self.assertTrue(result.session["schema_version"] == SCHEMA_VERSION)
            self.assertTrue(result.session["session"]["name"] == "Legacy")

    def test_invalid_pump_values_are_rejected(self):
        session = create_default_session()
        session["board_profiles"] = [{
            "profile_id": "mb1",
            "pump_configuration": {"channels": [{
                "channel": 1,
                "frequency_hz": 100,
                "amplitude_vpp": 999,
                "waveform": "Sinus",
            }]},
            "valve_configuration": {"valves": []},
            "sensor_configuration": {"selected": [], "calibration": "water", "sample_rate_seconds": 1.0},
        }]
        self.assertFalse(validate_session(session).valid)

    def test_wrong_nested_types_are_reported_instead_of_crashing(self):
        session = create_default_session()
        session["board_profiles"] = [{
            "profile_id": "mb1",
            "pump_configuration": None,
            "valve_configuration": [],
            "sensor_configuration": "invalid",
        }]
        result = validate_session(session)
        self.assertFalse(result.valid)
        self.assertGreaterEqual(len(result.errors), 3)

    def test_board_match_rejects_different_hardware(self):
        profile = {
            "profile_id": "mb1",
            "last_known_port": "COM7",
            "device_type": "mp-Multiboard2",
            "vendor_id": "10C4",
            "product_id": "EA60",
        }
        result = match_board_profile(profile, {
            "port": "COM7",
            "device_type": "other-device",
            "vendor_id": "10C4",
            "product_id": "EA60",
        })
        self.assertEqual(result.status, "device_mismatch")


if __name__ == "__main__":
    unittest.main()
