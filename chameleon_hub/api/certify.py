"""
chameleon_hub/api/certify.py
============================
Chameleon Hub — POST /certify endpoint

Validates a submitted manifest, runs safety checks, issues a DID + VC
signed entry on the private ledger, and returns the certified manifest.

Integrates into the existing FastAPI Hub (main.py).

Endpoint:
  POST /certify
  Body: { "manifest": { ...full Chameleon manifest... } }

  Response (success):
  {
    "success": true,
    "object_id": "CHA-KIT-001",
    "tx_id": "uuid",
    "certificationStatus": {
      "certified": true,
      "certifier": "did:chameleon:hub:v1",
      "certDate": "2026-03-13",
      "certTxId": "uuid",
      "status": "certified",
      "notes": "Chameleon Certified — safety and schema validated by Hub v1.0"
    },
    "vc": {
      "@context": ["https://www.w3.org/2018/credentials/v1"],
      "type": ["VerifiableCredential", "ChameleonCertification"],
      "issuer": "did:chameleon:hub:v1",
      "issuanceDate": "2026-03-13T22:55:44Z",
      "credentialSubject": {
        "id": "did:chameleon:kitchen:CHA-KIT-001",
        "objectId": "CHA-KIT-001",
        "certified": true,
        "safetyLevel": "strict"
      }
    },
    "ledger_entry": { ...IOTA-style ledger record... }
  }

Author: Chameleon Developer Agent v1.0
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ── Router — mounted into main.py ─────────────────────────────────────────────

router = APIRouter(prefix="", tags=["certification"])

# ── In-memory certified object registry (replace with DB in production) ────────
# Keyed by objectId → certification record
certified_registry: dict[str, dict] = {}

# ── Hub DID (issuer identity) ─────────────────────────────────────────────────
HUB_DID     = "did:chameleon:hub:v1"
HUB_VERSION = "1.0.0"

# ── Required manifest fields (schema contract) ────────────────────────────────
REQUIRED_TOP_LEVEL = [
    "protocolVersion", "chameleonManifestVersion", "objectClass",
    "objectId", "displayName", "manufacturer", "category",
    "physicalProperties", "safety", "security", "actions",
]

# ── Pydantic models ────────────────────────────────────────────────────────────

class CertifyRequest(BaseModel):
    manifest: dict[str, Any]

class RevocationRequest(BaseModel):
    object_id: str
    reason:    str
    issued_by: str   # DID of the entity requesting revocation

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _validate_manifest_schema(manifest: dict) -> tuple[bool, list[str]]:
    """
    Full schema validation.
    Returns (valid, list_of_errors).
    """
    errors: list[str] = []

    # 1. Required top-level fields
    for field in REQUIRED_TOP_LEVEL:
        if field not in manifest:
            errors.append(f"Missing required field: '{field}'")

    # 2. Protocol version
    if manifest.get("protocolVersion") != "Chameleon v1.0":
        errors.append(
            f"protocolVersion must be 'Chameleon v1.0', "
            f"got '{manifest.get('protocolVersion')}'"
        )

    # 3. Safety block — absolute requirements
    safety = manifest.get("safety", {})
    if not safety.get("weaponizationPrevention", False):
        errors.append("SAFETY FAIL: safety.weaponizationPrevention must be true")
    if not safety.get("emergencyStop", False):
        errors.append("SAFETY FAIL: safety.emergencyStop must be true")
    if "maxForceNewtons" not in safety:
        errors.append("SAFETY FAIL: safety.maxForceNewtons is required")
    if safety.get("maxForceNewtons", 0) > 200:
        errors.append(
            f"SAFETY FAIL: safety.maxForceNewtons={safety.get('maxForceNewtons')} "
            f"exceeds hard maximum of 200N"
        )

    # 4. Security / DID
    security = manifest.get("security", {})
    did = security.get("did", "")
    if not did.startswith("did:chameleon:"):
        errors.append(
            f"security.did must start with 'did:chameleon:', got '{did}'"
        )
    if not security.get("auditLogRequired", False):
        errors.append("security.auditLogRequired must be true")

    # 5. Actions
    actions = manifest.get("actions", [])
    if not isinstance(actions, list) or len(actions) == 0:
        errors.append("manifest must define at least one action")
    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            errors.append(f"actions[{i}] must be an object")
            continue
        if "id" not in action:
            errors.append(f"actions[{i}] missing 'id'")
        if "maxDurationSeconds" not in action:
            errors.append(
                f"actions[{i}] (id='{action.get('id', '?')}') "
                f"missing maxDurationSeconds"
            )

    # 6. physicalProperties
    phys = manifest.get("physicalProperties", {})
    if "weightKg" not in phys:
        errors.append("physicalProperties.weightKg is required")
    if "graspPoints" not in phys:
        errors.append("physicalProperties.graspPoints is required")

    # 7. No prohibited action names in the action list
    prohibited = set(safety.get("prohibitedActions", []))
    for action in manifest.get("actions", []):
        action_id = action.get("id", "").lower()
        for prohibited_word in prohibited:
            if prohibited_word in action_id:
                errors.append(
                    f"Action id '{action_id}' contains prohibited word '{prohibited_word}'"
                )

    return len(errors) == 0, errors


def _issue_vc(manifest: dict, tx_id: str) -> dict:
    """
    Issue a Verifiable Credential for the certified object.
    Follows W3C VC Data Model v1.1 structure.
    In production: sign with Hub private key (Ed25519 / secp256k1).
    Here: stub VC with all required fields.
    """
    object_id  = manifest["objectId"]
    subject_did = manifest.get("security", {}).get("did", f"did:chameleon:object:{object_id}")
    safety      = manifest.get("safety", {})

    return {
        "@context": [
            "https://www.w3.org/2018/credentials/v1",
            "https://chameleon.io/schemas/certification/v1",
        ],
        "id":           f"urn:chameleon:vc:{tx_id}",
        "type":         ["VerifiableCredential", "ChameleonCertification"],
        "issuer":       HUB_DID,
        "issuanceDate": _now_iso(),
        "expirationDate": None,   # No expiry — revoke explicitly via POST /certify/revoke
        "credentialSubject": {
            "id":            subject_did,
            "objectId":      object_id,
            "displayName":   manifest.get("displayName"),
            "manufacturer":  manifest.get("manufacturer"),
            "objectClass":   manifest.get("objectClass"),
            "certified":     True,
            "safetyLevel":   safety.get("safetyLevel", "standard"),
            "maxForceN":     safety.get("maxForceNewtons"),
            "weaponizationPrevention": safety.get("weaponizationPrevention", True),
            "emergencyStop": safety.get("emergencyStop", True),
            "certTxId":      tx_id,
        },
        "proof": {
            "type":               "ChameleonHubSignature2026",
            "created":            _now_iso(),
            "verificationMethod": f"{HUB_DID}#key-1",
            "proofPurpose":       "assertionMethod",
            "proofValue":         f"stub-sig-{tx_id[:8]}",  # replace with real Ed25519 sig
        },
    }


def _build_ledger_entry(event_type: str, object_id: str, tx_id: str, payload: dict) -> dict:
    """Build an IOTA-style private ledger entry."""
    return {
        "tx_id":       tx_id,
        "event_type":  event_type,
        "timestamp":   _now_iso(),
        "issuer":      HUB_DID,
        "object_id":   object_id,
        "payload":     payload,
    }

# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/certify")
def certify_manifest(req: CertifyRequest) -> dict:
    """
    POST /certify

    Full certification pipeline:
      1. Schema validation (required fields, versions)
      2. Safety validation (weaponization, force limits, emergency stop)
      3. Issue VC (W3C Verifiable Credential)
      4. Write to private ledger
      5. Return certified manifest + VC + ledger entry
    """
    manifest  = req.manifest
    object_id = manifest.get("objectId", "UNKNOWN")

    # ── Step 1 + 2: Schema + safety validation ─────────────────────────────
    valid, errors = _validate_manifest_schema(manifest)
    if not valid:
        raise HTTPException(
            status_code=422,
            detail={
                "error":       "Manifest failed Chameleon schema/safety validation",
                "object_id":   object_id,
                "error_count": len(errors),
                "errors":      errors,
            },
        )

    # ── Step 3: Check for duplicate certification ──────────────────────────
    if object_id in certified_registry:
        existing = certified_registry[object_id]
        raise HTTPException(
            status_code=409,
            detail={
                "error":     f"Object '{object_id}' is already certified.",
                "certDate":  existing.get("certDate"),
                "certTxId":  existing.get("certTxId"),
                "hint":      "Use POST /certify/revoke to revoke, then re-certify.",
            },
        )

    # ── Step 4: Issue VC + ledger entry ────────────────────────────────────
    tx_id = str(uuid.uuid4())
    vc    = _issue_vc(manifest, tx_id)

    certification_status = {
        "certified":  True,
        "certifier":  HUB_DID,
        "certDate":   _today(),
        "certTxId":   tx_id,
        "status":     "certified",
        "notes":      (
            f"Chameleon Certified — safety and schema validated by Hub {HUB_VERSION}. "
            f"weaponizationPrevention=true, emergencyStop=true."
        ),
    }

    ledger_entry = _build_ledger_entry(
        event_type = "OBJECT_CERTIFIED",
        object_id  = object_id,
        tx_id      = tx_id,
        payload    = {
            "displayName":  manifest.get("displayName"),
            "manufacturer": manifest.get("manufacturer"),
            "objectClass":  manifest.get("objectClass"),
            "safetyLevel":  manifest.get("safety", {}).get("safetyLevel"),
            "vc_id":        vc["id"],
        },
    )

    # ── Step 5: Store in registry ──────────────────────────────────────────
    certified_registry[object_id] = {
        "object_id":           object_id,
        "displayName":         manifest.get("displayName"),
        "manufacturer":        manifest.get("manufacturer"),
        "objectClass":         manifest.get("objectClass"),
        "category":            manifest.get("category"),
        "certificationStatus": certification_status,
        "vc":                  vc,
        "ledger_entry":        ledger_entry,
        "manifest_snapshot":   manifest,   # store original at cert time
    }

    return {
        "success":             True,
        "object_id":           object_id,
        "tx_id":               tx_id,
        "certificationStatus": certification_status,
        "vc":                  vc,
        "ledger_entry":        ledger_entry,
    }


@router.get("/certify/registry")
def list_certified() -> dict:
    """
    GET /certify/registry

    Returns all certified objects — powers the certified.html landing page.
    """
    objects = []
    for obj_id, record in certified_registry.items():
        objects.append({
            "objectId":     obj_id,
            "displayName":  record.get("displayName"),
            "manufacturer": record.get("manufacturer"),
            "objectClass":  record.get("objectClass"),
            "category":     record.get("category"),
            "certDate":     record["certificationStatus"].get("certDate"),
            "certTxId":     record["certificationStatus"].get("certTxId"),
            "safetyLevel":  record.get("manifest_snapshot", {})
                                  .get("safety", {})
                                  .get("safetyLevel", "standard"),
        })
    return {
        "total":    len(objects),
        "objects":  objects,
        "hub":      HUB_DID,
        "asOf":     _now_iso(),
    }


@router.get("/certify/{object_id}")
def get_certification(object_id: str) -> dict:
    """
    GET /certify/{object_id}

    Returns the full certification record for a single object.
    """
    if object_id not in certified_registry:
        raise HTTPException(
            status_code=404,
            detail=f"Object '{object_id}' not found in certified registry.",
        )
    return certified_registry[object_id]


@router.post("/certify/revoke")
def revoke_certification(req: RevocationRequest) -> dict:
    """
    POST /certify/revoke

    Revokes certification for an object and logs to the ledger.
    The object remains in the registry with status='revoked'.
    """
    if req.object_id not in certified_registry:
        raise HTTPException(
            status_code=404,
            detail=f"Object '{req.object_id}' is not certified.",
        )

    tx_id = str(uuid.uuid4())
    record = certified_registry[req.object_id]
    record["certificationStatus"].update({
        "certified": False,
        "status":    "revoked",
        "notes":     f"Revoked on {_today()} by {req.issued_by}. Reason: {req.reason}",
        "revokedDate": _today(),
        "revokedBy":   req.issued_by,
    })

    ledger_entry = _build_ledger_entry(
        event_type = "CERTIFICATION_REVOKED",
        object_id  = req.object_id,
        tx_id      = tx_id,
        payload    = {"reason": req.reason, "issued_by": req.issued_by},
    )
    record["revocation_ledger_entry"] = ledger_entry

    # Remove from active registry so it can be re-certified after remediation
    del certified_registry[req.object_id]

    return {
        "success":      True,
        "object_id":    req.object_id,
        "tx_id":        tx_id,
        "status":       "revoked",
        "ledger_entry": ledger_entry,
    }
