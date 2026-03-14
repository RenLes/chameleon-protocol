"""
chameleon_certify/cli.py
========================
Chameleon Protocol — Manufacturer Certification CLI

Takes a manufacturer's raw device spec (minimal JSON) and outputs a
fully Chameleon-compliant manifest with:
  - Correct schema fields and versioning
  - Pre-filled safety block from safety templates
  - physicalProperties normalised and validated
  - certificationStatus block (DID + VC reference)
  - Security / DID / ledger block
  - Matter API stub
  - Actions list with safety defaults

Usage:
  # Generate a manifest from a spec file:
  python3 cli.py --spec my_toaster_spec.json

  # Specify output path:
  python3 cli.py --spec my_toaster_spec.json --out ../chameleon_library/kitchen/toaster_manifest.json

  # Submit to Hub for certification (requires Hub running):
  python3 cli.py --spec my_toaster_spec.json --certify

  # List object class templates:
  python3 cli.py --list-templates

  # Validate an existing manifest without regenerating:
  python3 cli.py --validate ../chameleon_library/kitchen/stovetop_kettle_manifest.json

Example spec file (minimal input from manufacturer):
  {
    "displayName": "Smart Toaster Pro",
    "manufacturer": "AcmeCorp",
    "category": "kitchen",
    "objectClass": "kitchen.appliance.toaster",
    "weightKg": 1.2,
    "capacitySlots": 2,
    "maxTemperatureCelsius": 300,
    "maxForceNewtons": 5,
    "actions": ["insert_bread", "eject_toast", "adjust_browning"]
  }

Author: Chameleon Developer Agent v1.0
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import urllib.request
import urllib.error

# ── Paths ─────────────────────────────────────────────────────────────────────

CLI_DIR      = Path(__file__).parent
REPO_ROOT    = CLI_DIR.parent
LIBRARY_DIR  = REPO_ROOT / "chameleon_library"
HUB_CERTIFY  = "http://localhost:8000/certify"

# ── Object class safety templates ─────────────────────────────────────────────
# Each template provides sensible defaults for a device category.
# The manufacturer's spec overrides any field present in the spec.

SAFETY_TEMPLATES: dict[str, dict] = {
    "kitchen.appliance": {
        "weaponizationPrevention": True,
        "safetyLevel": "strict",
        "maxForceNewtons": 15,
        "maxTemperatureCelsius": 100,
        "humanContactMaxTempCelsius": 45,
        "emergencyStop": True,
        "noHumanContactWhenHot": True,
        "prohibitedActions": ["throw", "weaponize", "harm", "injure", "overfill"],
        "requiredPPE": [],
        "safeDistanceMeters": 0.3,
        "spillContingency": "halt_motion_alert_operator",
        "humanoidCrossCheckRequired": False,
    },
    "kitchen.utensil": {
        "weaponizationPrevention": True,
        "safetyLevel": "standard",
        "maxForceNewtons": 5,
        "maxTemperatureCelsius": 80,
        "humanContactMaxTempCelsius": 40,
        "emergencyStop": True,
        "noHumanContactWhenHot": False,
        "prohibitedActions": ["throw", "weaponize", "harm", "injure"],
        "requiredPPE": [],
        "safeDistanceMeters": 0.1,
        "humanoidCrossCheckRequired": False,
    },
    "workshop.tool": {
        "weaponizationPrevention": True,
        "safetyLevel": "strict",
        "maxForceNewtons": 50,
        "maxTemperatureCelsius": 60,
        "humanContactMaxTempCelsius": 35,
        "emergencyStop": True,
        "noHumanContactWhenHot": False,
        "prohibitedActions": ["throw", "weaponize", "harm", "injure", "swing_at_human"],
        "requiredPPE": ["safety_glasses", "gloves"],
        "safeDistanceMeters": 1.5,
        "humanoidCrossCheckRequired": True,
        "commandSigningRequired": True,
    },
    "living_room.object": {
        "weaponizationPrevention": True,
        "safetyLevel": "standard",
        "maxForceNewtons": 8,
        "maxTemperatureCelsius": 50,
        "humanContactMaxTempCelsius": 40,
        "emergencyStop": True,
        "noHumanContactWhenHot": False,
        "prohibitedActions": ["throw", "weaponize", "harm", "injure"],
        "requiredPPE": [],
        "safeDistanceMeters": 0.2,
        "humanoidCrossCheckRequired": False,
    },
    "default": {
        "weaponizationPrevention": True,
        "safetyLevel": "standard",
        "maxForceNewtons": 20,
        "maxTemperatureCelsius": 60,
        "humanContactMaxTempCelsius": 40,
        "emergencyStop": True,
        "noHumanContactWhenHot": False,
        "prohibitedActions": ["throw", "weaponize", "harm", "injure"],
        "requiredPPE": [],
        "safeDistanceMeters": 0.3,
        "humanoidCrossCheckRequired": False,
    },
}

# ── Required manifest fields (schema contract) ────────────────────────────────

REQUIRED_SPEC_FIELDS = ["displayName", "manufacturer", "category", "objectClass"]

REQUIRED_MANIFEST_FIELDS = [
    "protocolVersion", "chameleonManifestVersion", "objectClass",
    "objectId", "displayName", "manufacturer", "category",
    "physicalProperties", "safety", "security", "actions",
    "certificationStatus",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _generate_object_id(category: str, display_name: str) -> str:
    """Generate a deterministic-looking object ID from category + name."""
    cat_code  = category[:3].upper()
    name_code = "".join(w[0] for w in display_name.split()[:3]).upper()
    short_uid = str(uuid.uuid4()).split("-")[0].upper()
    return f"CHA-{cat_code}-{name_code}-{short_uid}"


def _get_safety_template(object_class: str) -> dict:
    """Return the closest matching safety template for the given object class."""
    # Try progressively shorter prefixes
    parts = object_class.split(".")
    for length in range(len(parts), 0, -1):
        key = ".".join(parts[:length])
        if key in SAFETY_TEMPLATES:
            return dict(SAFETY_TEMPLATES[key])
    return dict(SAFETY_TEMPLATES["default"])


def _build_physical_properties(spec: dict) -> dict:
    """Construct physicalProperties block from spec, applying sensible defaults."""
    props: dict = {
        "material":      spec.get("material", "unspecified"),
        "weightKg":      spec.get("weightKg", 0.0),
        "fragile":       spec.get("fragile", False),
        "spillRisk":     spec.get("spillRisk", False),
    }

    # Optional fields — only include if provided in spec
    optional_fields = [
        "capacityLitres", "capacitySlots", "dimensions",
        "surfaceTemperatureRanges", "heatSensitiveSurfaces",
        "centerOfMassOffset", "placementSurfaces",
        "pourTiltAngleDeg", "fillStopFraction", "liftHeightCm",
    ]
    for field in optional_fields:
        if field in spec:
            props[field] = spec[field]

    # Grasp points — provide a default if not specified
    if "graspPoints" in spec:
        props["graspPoints"] = spec["graspPoints"]
    else:
        max_force = spec.get("maxForceNewtons", 15)
        props["graspPoints"] = [
            {
                "id":                      "primary_grasp",
                "type":                    "power_grip",
                "location":                "main_body",
                "forceMaxNewtons":         max_force,
                "forceRecommendedNewtons": round(max_force * 0.6, 1),
                "fingerSpreadMm":          40,
                "requiresHeatGlove":       spec.get("requiresHeatGlove", False),
            }
        ]

    return props


def _build_security_block(object_id: str, category: str, spec: dict) -> dict:
    """Build DID / VC / ledger security block."""
    return {
        "did":                   f"did:chameleon:{category}:{object_id}",
        "vcSchema":              "https://chameleon.io/schemas/appliance/v1",
        "blockchainLedger":      "iota_private_chameleon",
        "accessPolicy":          spec.get("accessPolicy", "any_authorized_humanoid"),
        "auditLogRequired":      True,
        "commandSigningRequired": spec.get("commandSigningRequired", False),
    }


def _build_certification_status(object_id: str, certified: bool = False) -> dict:
    """Build certificationStatus block. certified=False → pending."""
    return {
        "certified":   certified,
        "certifier":   "did:chameleon:hub:v1" if certified else None,
        "certDate":    _now_iso() if certified else None,
        "certTxId":    None,           # filled in by POST /certify response
        "status":      "certified" if certified else "pending",
        "notes":       (
            "Chameleon Certified — safety and schema validated by Chameleon Hub v1.0"
            if certified else
            "Pending certification — submit to POST /certify to complete"
        ),
    }


def _build_actions(spec: dict, safety_template: dict) -> list:
    """Build actions list from spec action names, adding safety defaults."""
    raw_actions = spec.get("actions", ["interact"])
    actions = []
    for action_id in raw_actions:
        actions.append({
            "id":               str(action_id),
            "description":      f"{str(action_id).replace('_', ' ').capitalize()}",
            "requiredSensors":  spec.get("requiredSensors", ["force_torque", "vision"]),
            "requiresVC":       safety_template.get("commandSigningRequired", False),
            "humanoidCrossCheck": safety_template.get("humanoidCrossCheckRequired", False),
            "maxDurationSeconds": spec.get("defaultActionDurationSeconds", 30),
        })
    return actions


def _build_matter_api(spec: dict, object_id: str, category: str) -> dict:
    """Build Matter API block."""
    clusters = spec.get("matterClusters", ["OnOff"])
    return {
        "endpoint":  f"matter://{category}/{object_id}",
        "clusters":  clusters,
        "supported": spec.get("matterSupported", True),
        "matterId":  f"0xCHA{str(uuid.uuid4().int)[:4].upper()}",
    }


def _build_manifest(spec: dict) -> dict:
    """
    Full manifest builder. Combines spec with safety templates,
    default blocks, and certificationStatus.
    Returns a complete Chameleon manifest dict.
    """
    # Validate required spec fields
    missing = [f for f in REQUIRED_SPEC_FIELDS if f not in spec]
    if missing:
        raise ValueError(f"Spec is missing required fields: {missing}")

    object_class = spec["objectClass"]
    category     = spec["category"]
    display_name = spec["displayName"]
    manufacturer = spec["manufacturer"]

    object_id        = spec.get("objectId") or _generate_object_id(category, display_name)
    safety_template  = _get_safety_template(object_class)

    # Override safety template with any values explicitly in the spec
    for key in ["maxForceNewtons", "maxTemperatureCelsius", "humanContactMaxTempCelsius",
                "safetyLevel", "humanoidCrossCheckRequired", "commandSigningRequired",
                "safeDistanceMeters"]:
        if key in spec:
            safety_template[key] = spec[key]

    # Merge prohibitedActions from spec
    if "additionalProhibitedActions" in spec:
        safety_template["prohibitedActions"] = list(set(
            safety_template.get("prohibitedActions", [])
            + spec["additionalProhibitedActions"]
        ))

    manifest = {
        "protocolVersion":          "Chameleon v1.0",
        "chameleonManifestVersion": "1.0.0",
        "objectClass":              object_class,
        "objectId":                 object_id,
        "displayName":              display_name,
        "description":              spec.get(
            "description",
            f"{display_name} by {manufacturer}. Chameleon-enabled."
        ),
        "manufacturer":             manufacturer,
        "category":                 category,
        "tags":                     spec.get("tags", [category]),
        "physicalProperties":       _build_physical_properties(spec),
        "safety":                   safety_template,
        "security":                 _build_security_block(object_id, category, spec),
        "certificationStatus":      _build_certification_status(object_id, certified=False),
        "matterAPI":                _build_matter_api(spec, object_id, category),
        "actions":                  _build_actions(spec, safety_template),
    }

    # Optional: isaac_sim block if URDF provided
    if "urdfPath" in spec:
        manifest["isaac_sim"] = {
            "urdf":            spec["urdfPath"],
            "digitalTwinId":   f"DT-{object_id}",
            "graspSimEnabled": True,
            "physicsEngine":   "PhysX",
        }

    return manifest


# ── Validation ────────────────────────────────────────────────────────────────

def validate_manifest(manifest: dict) -> tuple[bool, list[str]]:
    """
    Validates a manifest against Chameleon schema requirements.
    Returns (valid, list_of_errors).
    """
    errors: list[str] = []

    # Required top-level fields
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            errors.append(f"Missing required field: '{field}'")

    # protocolVersion
    if manifest.get("protocolVersion") != "Chameleon v1.0":
        errors.append(
            f"protocolVersion must be 'Chameleon v1.0', "
            f"got '{manifest.get('protocolVersion')}'"
        )

    # Safety block checks
    safety = manifest.get("safety", {})
    if not safety.get("weaponizationPrevention", False):
        errors.append("safety.weaponizationPrevention must be true")
    if not safety.get("emergencyStop", False):
        errors.append("safety.emergencyStop must be true")
    if "maxForceNewtons" not in safety:
        errors.append("safety.maxForceNewtons is required")
    if safety.get("maxForceNewtons", 0) > 200:
        errors.append(
            f"safety.maxForceNewtons={safety['maxForceNewtons']} exceeds "
            f"hard maximum of 200N"
        )

    # Security / DID check
    security = manifest.get("security", {})
    if not security.get("did", "").startswith("did:chameleon:"):
        errors.append(
            f"security.did must start with 'did:chameleon:', "
            f"got '{security.get('did')}'"
        )
    if not security.get("auditLogRequired", False):
        errors.append("security.auditLogRequired must be true")

    # certificationStatus block
    cert = manifest.get("certificationStatus", {})
    if "certified" not in cert:
        errors.append("certificationStatus.certified field is required")
    if "status" not in cert:
        errors.append("certificationStatus.status field is required")

    # Actions check
    actions = manifest.get("actions", [])
    if not actions:
        errors.append("manifest must define at least one action")
    for i, action in enumerate(actions):
        if "id" not in action:
            errors.append(f"actions[{i}] missing 'id'")
        if "maxDurationSeconds" not in action:
            errors.append(f"actions[{i}] (id='{action.get('id')}') missing maxDurationSeconds")

    return len(errors) == 0, errors


# ── Hub submission ────────────────────────────────────────────────────────────

def submit_to_hub(manifest: dict, hub_url: str = HUB_CERTIFY) -> dict:
    """
    POST the manifest to POST /certify on the Chameleon Hub.
    Returns the certification response dict.
    """
    payload = json.dumps({"manifest": manifest}).encode()
    req = urllib.request.Request(
        hub_url,
        data    = payload,
        headers = {
            "Content-Type":    "application/json",
            "Accept":          "application/json",
            "X-Chameleon-Ver": "1.0",
        },
        method = "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Hub unreachable: {e.reason}"}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Bad JSON from Hub: {e}"}


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Chameleon Certification CLI — generate and certify manifests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate manifest from spec:
  python3 cli.py --spec specs/toaster_spec.json

  # Generate + write to library:
  python3 cli.py --spec specs/toaster_spec.json \\
      --out ../chameleon_library/kitchen/toaster_manifest.json

  # Generate + submit to Hub for DID/VC signing:
  python3 cli.py --spec specs/toaster_spec.json --certify

  # Validate an existing manifest:
  python3 cli.py --validate ../chameleon_library/kitchen/stovetop_kettle_manifest.json

  # List available safety templates:
  python3 cli.py --list-templates
        """,
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--spec",           type=Path, metavar="FILE",
                       help="Path to manufacturer spec JSON file")
    group.add_argument("--validate",       type=Path, metavar="FILE",
                       help="Validate an existing manifest JSON file")
    group.add_argument("--list-templates", action="store_true",
                       help="List available safety templates and exit")

    p.add_argument("--out",      type=Path, metavar="FILE",
                   help="Write generated manifest to this path (default: print to stdout)")
    p.add_argument("--certify",  action="store_true", default=False,
                   help="Submit manifest to Hub for DID/VC certification")
    p.add_argument("--hub-url",  type=str, default=HUB_CERTIFY,
                   help=f"Hub certify endpoint (default: {HUB_CERTIFY})")
    p.add_argument("--pretty",   action="store_true", default=True,
                   help="Pretty-print JSON output (default: true)")
    return p.parse_args()


def main():
    args = parse_args()

    # ── List templates ─────────────────────────────────────────────────────────
    if args.list_templates:
        print("\nAvailable safety templates:")
        print("─" * 50)
        for name, tmpl in SAFETY_TEMPLATES.items():
            print(f"  {name:<30}  maxForce={tmpl['maxForceNewtons']}N  "
                  f"level={tmpl['safetyLevel']}  "
                  f"crossCheck={tmpl.get('humanoidCrossCheckRequired', False)}")
        print()
        return

    indent = 2 if args.pretty else None

    # ── Validate mode ──────────────────────────────────────────────────────────
    if args.validate:
        if not args.validate.exists():
            print(f"ERROR: File not found: {args.validate}", file=sys.stderr)
            sys.exit(1)
        try:
            manifest = json.loads(args.validate.read_text())
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in {args.validate}: {e}", file=sys.stderr)
            sys.exit(1)

        valid, errors = validate_manifest(manifest)
        obj_id = manifest.get("objectId", "unknown")
        obj_name = manifest.get("displayName", "unknown")
        cert_status = manifest.get("certificationStatus", {}).get("status", "unknown")

        print(f"\n{'─' * 60}")
        print(f"  Chameleon Manifest Validator")
        print(f"{'─' * 60}")
        print(f"  File   : {args.validate}")
        print(f"  Object : {obj_name} ({obj_id})")
        print(f"  Cert   : {cert_status}")
        print(f"  Result : {'✓ VALID' if valid else '✗ INVALID'}")
        if errors:
            print(f"\n  Errors ({len(errors)}):")
            for err in errors:
                print(f"    • {err}")
        print()
        sys.exit(0 if valid else 1)

    # ── Generate mode ──────────────────────────────────────────────────────────
    if not args.spec.exists():
        print(f"ERROR: Spec file not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    try:
        spec = json.loads(args.spec.read_text())
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in spec file: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Chameleon Certify CLI — generating manifest from spec…", flush=True)

    try:
        manifest = _build_manifest(spec)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate the generated manifest
    valid, errors = validate_manifest(manifest)
    if not valid:
        print("ERROR: Generated manifest failed validation:", file=sys.stderr)
        for err in errors:
            print(f"  • {err}", file=sys.stderr)
        sys.exit(1)

    # Optionally submit to Hub for certification
    if args.certify:
        print(f"  Submitting to Hub at {args.hub_url} …", flush=True)
        hub_response = submit_to_hub(manifest, args.hub_url)
        if hub_response.get("success"):
            cert = hub_response.get("certificationStatus", {})
            manifest["certificationStatus"].update({
                "certified":  True,
                "certifier":  cert.get("certifier", "did:chameleon:hub:v1"),
                "certDate":   cert.get("certDate",  _now_iso()),
                "certTxId":   hub_response.get("tx_id"),
                "status":     "certified",
                "notes":      "Chameleon Certified — DID/VC issued by Hub v1.0",
            })
            print(f"  ✓ Certified! tx_id={hub_response.get('tx_id')}", flush=True)
        else:
            print(f"  ⚠  Hub submission failed: {hub_response.get('error')}", flush=True)
            print(f"     Manifest saved with status=pending.", flush=True)

    # Output
    manifest_json = json.dumps(manifest, indent=indent)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(manifest_json)
        print(f"\n  ✓ Manifest written to: {args.out}")
        print(f"    Object ID    : {manifest['objectId']}")
        print(f"    Cert status  : {manifest['certificationStatus']['status']}")
        print(f"    Safety level : {manifest['safety']['safetyLevel']}")
        print(f"    Actions      : {len(manifest['actions'])}")
    else:
        print(f"\n{'─' * 60}  GENERATED MANIFEST  {'─' * 60}\n")
        print(manifest_json)

    print()


if __name__ == "__main__":
    main()
