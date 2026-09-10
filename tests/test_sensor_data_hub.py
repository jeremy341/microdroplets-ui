from datetime import datetime, timezone

from backend.sensor_data_hub import SensorDataHub
from backend.serial_manager import BackendEvent, MultiboardConnection, SensorSample


def _sample(value=12.5):
    return SensorSample(
        timestamp=datetime.now(timezone.utc),
        elapsed_seconds=1.0,
        board_port="COM3",
        sensor_id="liquid_flow",
        value=value,
        unit="µL/min",
        accumulated_volume_ul=1.2,
        raw_line="12.5",
    )


def test_multiboard_event_subscriber_does_not_consume_legacy_queue():
    connection = MultiboardConnection("COM3", connection=None)
    seen = []
    connection.subscribe_events(seen.append)
    event = BackendEvent("measurement", "COM3", sample=_sample())
    connection._publish_event(event)
    assert seen == [event]
    assert connection.drain_events() == [event]


def test_unsubscribe_stops_fanout_but_not_queue():
    connection = MultiboardConnection("COM3", connection=None)
    seen = []
    connection.subscribe_events(seen.append)
    connection.unsubscribe_events(seen.append)  # distinct bound object for list.append; no-op is safe
    callback = lambda event: seen.append(event)
    connection.subscribe_events(callback)
    connection.unsubscribe_events(callback)
    event = BackendEvent("state", "COM3", "ready")
    connection._publish_event(event)
    assert seen == []
    assert connection.drain_events() == [event]


def test_sensor_data_hub_receives_samples_without_drain():
    connection = MultiboardConnection("COM3", connection=None)
    hub = SensorDataHub()
    hub.set_connected_boards([{"port": "COM3", "connection": connection}])
    event = BackendEvent("measurement", "COM3", sample=_sample(42.25))
    connection._publish_event(event)

    values, timestamps = hub.snapshot("COM3", "liquid_flow")
    assert values == [42.25]
    assert len(timestamps) == 1
    assert hub.latest("COM3", "liquid_flow") == 42.25
    assert hub.is_available("COM3", "liquid_flow")
    # The existing Sensors page can still drain exactly the same event.
    assert connection.drain_events() == [event]
    hub.shutdown()
