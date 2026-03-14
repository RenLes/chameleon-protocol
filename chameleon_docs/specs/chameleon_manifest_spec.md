# Chameleon Manifest Specification v1.0

## Overview
Every physical object in the Chameleon library has a manifest — a JSON file describing its physical properties, safety constraints, blockchain identity, and available actions.

## Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `chameleonManifestVersion` | string | Always `"1.0.0"` |
| `objectClass` | string | Dot-notation class e.g. `kitchen.appliance.kettle` |
| `objectId` | string | Unique ID e.g. `CHA-KIT-001` |
| `physicalProperties` | object | Mass, dimensions, grasp points |
| `safetyBlock` | object | Force/temp limits, prohibited actions |
| `matterAPI` | object | Matter cluster bindings |
| `blockchain` | object | DID, VC schema, ledger reference |
| `actions` | array | Supported action definitions |

## Safety Block Rules
- `weaponizationPrevention` MUST be `true` on every manifest
- `prohibitedActions` MUST include at minimum: `["weaponize", "harm"]`
- Force limits MUST be specified and MUST be realistic for the device

## Action Definition
```json
{
  "id": "action_id",
  "description": "Human-readable description",
  "requiredSensors": ["force_torque", "vision"],
  "requiresVC": false,
  "humanoidCrossCheck": false,
  "humanApprovalRequired": false
}
```
