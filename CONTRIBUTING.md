# Contributing to Chameleon Protocol

First off — thank you. Chameleon only becomes the standard if the community builds it together.

## What We Need Most Right Now

| Priority | Task | Skill needed |
|----------|------|-------------|
| 🔴 **High** | Add new object manifests | JSON |
| 🔴 **High** | Test myCobot adapter on real hardware | Python + robot arm |
| 🟡 **Medium** | Port mycobot_adapter.py to new robot (UR5e, Dobot, xArm) | Python + ROS2 |
| 🟡 **Medium** | Add language translations to certified.html | HTML/JS |
| 🟢 **Low** | Improve Karpathy loop scoring function | Python + ML |
| 🟢 **Low** | Flutter adaptor app screens | Dart/Flutter |

---

## Adding a New Object Manifest (Most Wanted)

This is the single highest-impact contribution. Every new manifest expands what robots can safely interact with.

### Step 1 — Create your manifest JSON

Use this minimal template:

```json
{
  "protocolVersion": "1.0",
  "objectId": "did:chameleon:CATEGORY:OBJECT-NAME-v1",
  "objectClass": "CATEGORY.TYPE",
  "commonName": "Human Readable Name",
  "manufacturer": "Generic",
  "category": "CATEGORY",
  "physicalProperties": {
    "massKg": 0.5,
    "graspPoints": [
      {"id": "handle", "type": "power_grasp", "side": "right"}
    ]
  },
  "actions": {
    "ACTION_NAME": {
      "maxDurationSeconds": 10,
      "parameters": {
        "param1": {"default": 5.0, "min": 1.0, "max": 10.0},
        "param2": {"default": 45,  "min": 0,   "max": 90}
      }
    }
  },
  "safety": {
    "maxForceNewtons": 15,
    "maxTemperatureCelsius": 60,
    "humanoidCrossCheckRequired": false,
    "commandSigningRequired": false
  },
  "security": {
    "auditLogRequired": false,
    "commandSigningRequired": false
  },
  "certificationStatus": {
    "certified": false,
    "status": "pending",
    "notes": "Submitted for community review"
  }
}
```

### Step 2 — Validate your manifest

```bash
python chameleon_certify/cli.py --validate your_manifest.json
```

All checks must pass before submitting.

### Step 3 — Place it in the right folder

```
chameleon_library/
├── kitchen/        ← food prep, appliances, utensils
├── workshop/       ← tools, hardware
├── living_room/    ← furniture, entertainment, decor
├── bathroom/       ← hygiene, grooming
├── bedroom/        ← sleep, clothing, lighting
├── office/         ← stationery, electronics, equipment
├── garden/         ← outdoor tools, plants
├── healthcare/     ← medical devices (requires extra review)
└── security/       ← locks, sensors (requires extra review)
```

### Step 4 — Submit a Pull Request

Title format: `manifest: add [object name] to [category]`

Example: `manifest: add electric_toothbrush to bathroom`

---

## Safety Guidelines

### humanoidCrossCheckRequired
Set to `true` for ANY object that could cause harm if misused:
- Knives, scissors, saws, drills
- Medication dispensers
- Door locks, security devices
- Objects near children or vulnerable people

### commandSigningRequired
Set to `true` for objects where unauthorised commands could cause:
- Injury
- Property damage
- Privacy violation
- Financial loss

### maxForceNewtons
Be conservative. Real robot arms can apply much more force than objects can withstand.

| Object type | Suggested max force |
|-------------|-------------------|
| Fragile (glass, ceramic) | 3-5N |
| Light household | 8-15N |
| Kitchen appliances | 15-25N |
| Workshop tools | 25-50N |
| Heavy equipment | 50N+ |

---

## Pull Request Checklist

Before submitting, confirm:

- [ ] Manifest validates with `python chameleon_certify/cli.py --validate`
- [ ] `objectId` follows `did:chameleon:CATEGORY:OBJECT-v1` format
- [ ] `maxForceNewtons` is conservative (err on the side of safety)
- [ ] `humanoidCrossCheckRequired` is `true` for any sharp/dangerous object
- [ ] `graspPoints` has at least one entry
- [ ] All action parameters have `default`, `min`, `max`
- [ ] `maxDurationSeconds` is set on every action
- [ ] PR title follows format: `manifest: add [object] to [category]`

---

## Development Setup

```bash
git clone https://github.com/RenLes/chameleon-protocol
cd chameleon-protocol

# Install dependencies
pip install -r chameleon_experiments/karpathy_test/requirements.txt
pip install -r chameleon_hub/api/requirements.txt

# Run validation on all manifests
python chameleon_certify/cli.py --validate chameleon_library/kitchen/stovetop_kettle_manifest.json

# Run the Karpathy loop (stub mode — no hardware needed)
python chameleon_experiments/karpathy_test/isaac_lab_kettle_experiment.py --stub --port 8211 &
python chameleon_experiments/karpathy_test/chameleon_karpathy_test.py --iterations 20

# Run the Hub locally
cd chameleon_hub/api && pip install fastapi uvicorn && uvicorn main:app --reload
```

---

## Code Contributions

### Python style
- PEP 8
- Type hints on all function signatures
- Docstrings on all public functions

### Commit message format
```
type: short description

- Detail 1
- Detail 2
```

Types: `manifest`, `feat`, `fix`, `docs`, `test`, `refactor`

---

## Community

- **X / Twitter:** [@DavidQicatabua](https://twitter.com/DavidQicatabua)
- **GitHub Issues:** For bugs, manifest requests, feature ideas
- **GitHub Discussions:** For protocol design questions

---

## Code of Conduct

Be kind. This project is about making robots safer for humans. That starts with how we treat each other.

---

*Chameleon Protocol — Apache 2.0 — Copyright 2026 David Qicatabua / RenLes*
