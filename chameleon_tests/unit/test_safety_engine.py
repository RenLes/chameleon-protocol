"""
Chameleon Safety Engine — Unit Tests
Tests the safety veto logic in the Hub API.
"""

import pytest
import sys
sys.path.insert(0, "../../chameleon_hub/api")

# Inline the safety_check function for isolated testing
PROHIBITED_ACTIONS = [
    "weaponize", "harm", "attack", "disable_safety",
    "override_emergency_stop", "exceed_force_limit"
]

def safety_check(action: str, device: dict, parameters: dict) -> tuple:
    action_lower = action.lower()
    for prohibited in PROHIBITED_ACTIONS:
        if prohibited in action_lower:
            return False, f"Action '{action}' flagged by weaponizationPrevention filter."

    safety = device.get("safetyBlock", {})

    if "force_newtons" in parameters:
        max_force = safety.get("maxForceNewtons", 50)
        if parameters["force_newtons"] > max_force:
            return False, f"Force {parameters['force_newtons']}N exceeds limit {max_force}N."

    if "temperature_celsius" in parameters:
        max_temp = safety.get("maxTemperatureCelsius", 100)
        if parameters["temperature_celsius"] > max_temp:
            return False, f"Temperature {parameters['temperature_celsius']}°C exceeds limit {max_temp}°C."

    return True, "OK"

# ─── Test Fixtures ────────────────────────────────────────────────────────────

KETTLE_DEVICE = {
    "safetyBlock": {
        "maxForceNewtons": 15,
        "maxTemperatureCelsius": 100,
        "weaponizationPrevention": True
    }
}

# ─── Tests ────────────────────────────────────────────────────────────────────

def test_normal_action_passes():
    ok, reason = safety_check("pour_liquid", KETTLE_DEVICE, {"force_newtons": 8})
    assert ok is True
    assert reason == "OK"

def test_weaponize_action_vetoed():
    ok, reason = safety_check("weaponize_kettle", KETTLE_DEVICE, {})
    assert ok is False
    assert "weaponizationPrevention" in reason

def test_force_limit_exceeded():
    ok, reason = safety_check("pick_up", KETTLE_DEVICE, {"force_newtons": 100})
    assert ok is False
    assert "exceeds limit" in reason

def test_force_within_limit():
    ok, reason = safety_check("pick_up", KETTLE_DEVICE, {"force_newtons": 10})
    assert ok is True

def test_temperature_limit_exceeded():
    ok, reason = safety_check("heat_water", KETTLE_DEVICE, {"temperature_celsius": 150})
    assert ok is False
    assert "exceeds limit" in reason

def test_temperature_at_limit():
    ok, reason = safety_check("heat_water", KETTLE_DEVICE, {"temperature_celsius": 100})
    assert ok is True

def test_harm_action_vetoed():
    ok, reason = safety_check("harm_user", KETTLE_DEVICE, {})
    assert ok is False

def test_attack_action_vetoed():
    ok, reason = safety_check("attack_target", KETTLE_DEVICE, {})
    assert ok is False

def test_override_emergency_stop_vetoed():
    ok, reason = safety_check("override_emergency_stop", KETTLE_DEVICE, {})
    assert ok is False

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
