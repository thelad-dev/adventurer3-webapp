from app.mqtt import discovery_payloads, state_payload


def test_discovery_has_ha_device_and_temps():
    topics = dict(discovery_payloads())
    nozzle = topics["homeassistant/sensor/adventurer3/nozzle/config"]
    assert nozzle["device"]["manufacturer"] == "Flashforge"
    assert nozzle["state_topic"] == "adventurer3/state"
    assert nozzle["unit_of_measurement"] == "°C"
    printing = topics["homeassistant/binary_sensor/adventurer3/printing/config"]
    assert printing["payload_on"] is True


def test_state_payload_maps_led():
    body = state_payload(
        {"online": True, "led": "on", "nozzle": 200.0, "printing": True},
        camera_ok=True,
        control_mode="dashboard",
    )
    assert body["led_on"] is True
    assert body["camera_ok"] is True
    assert body["control_mode"] == "dashboard"
    assert body["nozzle"] == 200.0
