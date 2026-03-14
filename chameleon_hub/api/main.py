"""
Chameleon Hub — FastAPI Server (formerly UDAP)
Version: 1.0.0
Handles device registration, command routing, safety veto, and blockchain logging.
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uuid
import json
import logging
from datetime import datetime
from pathlib import Path

from certify import router as certify_router

app = FastAPI(
    title="Chameleon Hub API",
    description="Universal Device Adaptor Protocol Hub — formerly UDAP",
    version="1.0.0"
)

# Mount certification routes
app.include_router(certify_router)

# Serve the certified landing page at /certified
_WEB_DIR = Path(__file__).parent.parent / "web"
if _WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(_WEB_DIR)), name="web")

@app.get("/certified", include_in_schema=False)
def certified_page():
    """Serve the Chameleon Certified landing page."""
    html = _WEB_DIR / "certified.html"
    if html.exists():
        return FileResponse(str(html))
    return {"error": "certified.html not found — check chameleon_hub/web/"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chameleon_hub")

# ─── In-memory registry (replace with DB in production) ───────────────────────
device_registry: Dict[str, Any] = {}
command_log: List[Dict] = []

# ─── Models ───────────────────────────────────────────────────────────────────

class DeviceRegistration(BaseModel):
    device_id: str
    manifest_version: str
    object_class: str
    physical_properties: Dict[str, Any]
    safety_block: Dict[str, Any]
    did: str  # Decentralized Identifier

class CommandRequest(BaseModel):
    device_id: str
    action: str
    parameters: Optional[Dict[str, Any]] = {}
    issued_by: str  # humanoid DID or human user ID
    authorization_vc: Optional[str] = None  # Verifiable Credential

class SafetyVeto(BaseModel):
    device_id: str
    reason: str
    vetoed_action: str
    timestamp: str

# ─── Safety Engine ────────────────────────────────────────────────────────────

PROHIBITED_ACTIONS = [
    "weaponize", "harm", "attack", "disable_safety",
    "override_emergency_stop", "exceed_force_limit"
]

def safety_check(action: str, device: Dict, parameters: Dict) -> tuple[bool, str]:
    """Returns (is_safe, reason)"""
    action_lower = action.lower()
    for prohibited in PROHIBITED_ACTIONS:
        if prohibited in action_lower:
            return False, f"Action '{action}' flagged by weaponizationPrevention filter."

    safety = device.get("safety_block", {})

    # Force limit check
    if "force_newtons" in parameters:
        max_force = safety.get("maxForceNewtons", 50)
        if parameters["force_newtons"] > max_force:
            return False, f"Force {parameters['force_newtons']}N exceeds safety limit {max_force}N."

    # Temperature limit check
    if "temperature_celsius" in parameters:
        max_temp = safety.get("maxTemperatureCelsius", 100)
        if parameters["temperature_celsius"] > max_temp:
            return False, f"Temperature {parameters['temperature_celsius']}°C exceeds safety limit {max_temp}°C."

    return True, "OK"

# ─── Blockchain Logger (IOTA-style stub) ──────────────────────────────────────

def log_to_ledger(event_type: str, payload: Dict) -> str:
    tx_id = str(uuid.uuid4())
    entry = {
        "tx_id": tx_id,
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "payload": payload
    }
    command_log.append(entry)
    logger.info(f"[LEDGER] {event_type} → tx:{tx_id}")
    return tx_id

# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Chameleon Hub online", "version": "1.0.0"}

@app.post("/devices/register")
def register_device(reg: DeviceRegistration):
    if reg.device_id in device_registry:
        raise HTTPException(status_code=409, detail="Device already registered.")
    device_registry[reg.device_id] = reg.dict()
    tx_id = log_to_ledger("DEVICE_REGISTERED", reg.dict())
    return {"status": "registered", "device_id": reg.device_id, "tx_id": tx_id}

@app.get("/devices")
def list_devices():
    return {"count": len(device_registry), "devices": list(device_registry.keys())}

@app.get("/devices/{device_id}")
def get_device(device_id: str):
    if device_id not in device_registry:
        raise HTTPException(status_code=404, detail="Device not found.")
    return device_registry[device_id]

@app.post("/commands/send")
def send_command(cmd: CommandRequest):
    if cmd.device_id not in device_registry:
        raise HTTPException(status_code=404, detail="Device not registered.")

    device = device_registry[cmd.device_id]
    is_safe, reason = safety_check(cmd.action, device, cmd.parameters)

    if not is_safe:
        veto_payload = {
            "device_id": cmd.device_id,
            "vetoed_action": cmd.action,
            "reason": reason,
            "issued_by": cmd.issued_by,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        log_to_ledger("SAFETY_VETO", veto_payload)
        raise HTTPException(status_code=403, detail=f"Safety veto: {reason}")

    tx_id = log_to_ledger("COMMAND_SENT", cmd.dict())
    return {
        "status": "command_accepted",
        "device_id": cmd.device_id,
        "action": cmd.action,
        "tx_id": tx_id
    }

@app.get("/ledger")
def view_ledger(limit: int = 50):
    return {"entries": command_log[-limit:], "total": len(command_log)}

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "devices_registered": len(device_registry),
        "ledger_entries": len(command_log),
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
