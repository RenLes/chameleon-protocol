# Chameleon Karpathy Protocol — Experiment Goals
## Object: Stovetop Kettle (CHA-KIT-001)
### Session: Kettle Pouring Optimisation

---

## Primary Goals

### 1. Reduce Spill Rate
**Target**: spill_rate < 0.05 (baseline ~0.15)
**Method**: Tune `pourTiltAngleDeg` toward the optimal ~55° and extend `pourDurationSeconds` toward ~10s
**Why**: Spilling hot water near humans violates Chameleon safe-interaction constraints and wastes resources.
**Metric weight**: 50% of composite_score

### 2. Optimise Weight-Based Filling
**Target**: fillStopFraction ≈ 0.80 (baseline 0.85)
**Method**: Reduce `fillStopFraction` slightly — overfilling increases weight, destabilises pour, raises spill risk.
**Why**: Filling to 80% capacity gives the best balance of pour stability and water volume.
**Metric weight**: 30% of composite_score

### 3. Improve Pour Accuracy
**Target**: pour_accuracy > 0.85 (baseline variable)
**Method**: Tune tilt angle and duration jointly — accuracy peaks near (60°, 10s).
**Why**: Accurate pouring ensures water lands in the target vessel, not on the stove or surface.
**Metric weight**: 40% of composite_score

---

## Secondary Goals (Future Isaac Sim sessions)

### 4. Minimise Grasp Force
- Keep `graspForceNewtons` at the lowest effective value
- Reduces wear on robotic end-effector and risk of handle deformation
- Constraint: must remain above minimum required to hold full kettle securely

### 5. Optimise Lift Height
- `liftHeightCm` should clear the stove surface safely while minimising unnecessary height
- Lower lift = faster cycle time + reduced tip-over risk

---

## Composite Score Formula
```
composite_score = spill_rate - 0.4 * pour_accuracy - 0.3 * fill_efficiency
```
**Lower is better** (mirrors Karpathy's val_bpb — lower = better model).

---

## Safety Constraints (Hard — Cannot Be Violated)
| Parameter            | Hard Limit        | Reason                                |
|----------------------|-------------------|---------------------------------------|
| graspForceNewtons    | ≤ 15.0 N          | Handle damage / human contact risk    |
| pourTiltAngleDeg     | ≤ 120.0°          | Loss of liquid control                |
| fillStopFraction     | 0.10 – 0.95       | Avoid empty/overflow conditions       |
| maxTemperatureCelsius| ≤ 100.0°C         | Boiling point hard cap                |

Any proposed change that violates these is **automatically vetoed** and logged to the blockchain ledger.

---

## Expected Convergence
The mock physics model should converge near:
- `pourTiltAngleDeg` ≈ 55–60°
- `pourDurationSeconds` ≈ 9–11s
- `fillStopFraction` ≈ 0.78–0.82
- `graspForceNewtons` ≈ 7–9 N (not the dominant factor)

---

## Future Goals (Post-Isaac Sim Integration)
- [ ] Minimise end-effector trajectory length (energy efficiency)
- [ ] Optimise for different kettle fill levels (25%, 50%, 75%, 100%)
- [ ] Learn pour curves for different target vessel openings (mug vs. teapot vs. bowl)
- [ ] Add thermal model — don't pour above 60°C if human within 0.5m

---

*Chameleon Karpathy Protocol goals.md — CHA-KIT-001 — v1.0*
