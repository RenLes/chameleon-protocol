"""
chameleon_karpathy_test.py
==========================
Chameleon Protocol — Karpathy-style Autonomous Self-Improvement Loop
Based on: https://github.com/karpathy/autoresearch (Andrej Karpathy, ~March 2026)

Targets the REAL manifest:
  chameleon_library/kitchen/stovetop_kettle_manifest.json

Experiment backend (in priority order):
  1. Real Isaac Lab   — POST to http://localhost:8211 (--real-sim flag; 60 s timeout; NO fallback)
  2. Mock RPC server  — POST to http://localhost:8211 (mock_server.py; fast physics)
  3. Fallback dummy   — pure-Python physics model (used if RPC unreachable or --no-sim)

Karpathy Protocol core algorithm (adapted for Chameleon):
  1. Load real manifest from chameleon_library/
  2. Create timestamped backup before any change
  3. Probe RPC server health (or skip if --no-sim)
  4. Propose fixed-step mutation (±step per field, e.g. ±0.5 N, ±2.5°)
     on one physicalProperties or action field chosen at random
  5. Safety veto: reject if new value exceeds manifest hard limits
  6. Run experiment via RPC → fallback to dummy on failure
  7. --dry-run (default): show what WOULD happen, no disk write
     --commit           : overwrite real JSON + timestamped backup + round-trip verify
  8. Revert in-memory if metric regresses
  9. Log all iterations to results.tsv + commit_log.json
 10. Plot composite_score and spill_rate over iterations → PNG
 11. Keyboard controls: p=pause  s=stop  r=restart  c=cancel+revert

Mutation strategy:
  Fixed absolute steps per field (not percentage).  Gives finer, more
  predictable convergence near optima — e.g. when tilt is already at 57°,
  a 7% step would move it by only 0.2° (thrashing); a fixed 2.5° step
  explores the landscape properly.  The step size is defined per-field in
  MUTABLE_FIELD_MAP["step"].

Usage:
  python3 chameleon_karpathy_test.py                    # dry-run, RPC auto-detect
  python3 chameleon_karpathy_test.py --commit           # overwrite real manifest
  python3 chameleon_karpathy_test.py --no-sim           # force dummy mode (no server)
  python3 chameleon_karpathy_test.py --sim-url http://host:8211/api/chameleon/experiment
  python3 chameleon_karpathy_test.py --commit --iters 100
  python3 chameleon_karpathy_test.py --commit --step-scale 0.5   # halve all steps
  python3 chameleon_karpathy_test.py --commit --real-sim         # require real Isaac Lab (no fallback)

Author: Chameleon Developer Agent v1.0
"""

import argparse
import json
import math
import random
import shutil
import sys
import threading
import time
import csv
import urllib.request
import urllib.error
import urllib.parse
from copy import deepcopy
from datetime import datetime
from pathlib import Path

# ── Optional dependencies with graceful fallbacks ─────────────────────────────

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

try:
    from termcolor import colored
    TERMCOLOR_AVAILABLE = True
except ImportError:
    TERMCOLOR_AVAILABLE = False
    def colored(text, color=None, attrs=None):
        return text

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR      = Path(__file__).parent
REPO_ROOT     = BASE_DIR.parent.parent        # Desktop/Chameleon/
LIBRARY_DIR   = REPO_ROOT / "chameleon_library"
REAL_MANIFEST = LIBRARY_DIR / "kitchen" / "stovetop_kettle_manifest.json"

RESULTS_DIR   = BASE_DIR / "results"
PLOTS_DIR     = BASE_DIR / "plots"
COMMITS_DIR   = BASE_DIR / "commits"
BACKUPS_DIR   = BASE_DIR / "backups"

for d in [RESULTS_DIR, PLOTS_DIR, COMMITS_DIR, BACKUPS_DIR]:
    d.mkdir(exist_ok=True)

RESULTS_TSV = RESULTS_DIR / "results.tsv"
COMMIT_LOG  = COMMITS_DIR / "commit_log.json"
PLOT_PATH   = PLOTS_DIR   / "improvement_plot.png"

# ── Default RPC endpoint ──────────────────────────────────────────────────────

DEFAULT_SIM_URL    = "http://localhost:8211/api/chameleon/experiment"
RPC_TIMEOUT_S      = 30        # seconds — mock server / fast RPC
REAL_SIM_TIMEOUT_S = 60        # seconds — real Isaac Lab sim (longer for physics warmup)
RPC_HEALTH_URL     = "http://localhost:8211/health"

# ── Experiment config ─────────────────────────────────────────────────────────

DEFAULT_ITERS   = 50
ITERATION_DELAY = 0.3       # RPC server adds 50–250 ms; total loop ~0.4–0.6 s/iter
NOISE_SEED      = 42
DEFAULT_STEP_SCALE = 1.0    # CLI --step-scale multiplier (0.5 = finer, 2.0 = coarser)

# ── Mutable fields: paths into the real manifest + safety limits ──────────────

MUTABLE_FIELD_MAP = {
    # ── grasp_force_n ────────────────────────────────────────────────────────
    # Path: physicalProperties.graspPoints[id=handle_primary].forceRecommendedNewtons
    # Step: ±0.5 N  — fine enough to find the ideal grip without over-tuning
    "grasp_force_n": {
        "path":  "physicalProperties.graspPoints.0.forceRecommendedNewtons",
        "min":   2.0,
        "max":   14.0,
        "step":  0.5,        # ← absolute fixed step
        "unit":  "N",
    },
    # ── tilt_angle_deg ───────────────────────────────────────────────────────
    # Path: physicalProperties.pourTiltAngleDeg
    # Step: ±2.5°  — wide enough to traverse the bowl, small enough for accuracy
    "tilt_angle_deg": {
        "path":  "physicalProperties.pourTiltAngleDeg",
        "min":   25.0,
        "max":   90.0,
        "step":  2.5,        # ← absolute fixed step
        "unit":  "°",
    },
    # ── fill_stop_fraction ───────────────────────────────────────────────────
    # Path: physicalProperties.fillStopFraction
    # Step: ±0.02  — 2% increments; optimum near 0.79 so steps need to be fine
    "fill_stop_fraction": {
        "path":  "physicalProperties.fillStopFraction",
        "min":   0.20,
        "max":   0.93,
        "step":  0.02,       # ← absolute fixed step
        "unit":  "",
    },
    # ── pour_duration_s ──────────────────────────────────────────────────────
    # Path: actions[id=pour_liquid].maxDurationSeconds
    # Step: ±0.5 s  — optimum near 12 s; half-second steps give clean tuning
    "pour_duration_s": {
        "path":  "actions.pour_liquid.maxDurationSeconds",
        "min":   5.0,
        "max":   45.0,
        "step":  0.5,        # ← absolute fixed step
        "unit":  "s",
    },
    # ── lift_height_cm ───────────────────────────────────────────────────────
    # Path: physicalProperties.liftHeightCm
    # Step: ±1.0 cm  — less critical; 1 cm steps give sufficient resolution
    "lift_height_cm": {
        "path":  "physicalProperties.liftHeightCm",
        "min":   10.0,
        "max":   40.0,
        "step":  1.0,        # ← absolute fixed step
        "unit":  "cm",
    },
}

FIELD_DEFAULTS = {
    "physicalProperties.pourTiltAngleDeg": 45.0,
    "physicalProperties.fillStopFraction":  0.85,
    "physicalProperties.liftHeightCm":     25.0,
}

# ── Global state ──────────────────────────────────────────────────────────────

_state = {
    "paused":    False,
    "stopped":   False,
    "restart":   False,
    "cancelled": False,
}
_state_lock = threading.Lock()
rng = random.Random(NOISE_SEED)

# ── Logging helpers ───────────────────────────────────────────────────────────

def ts():
    return datetime.now().strftime("%H:%M:%S")

def log(msg, color=None, bold=False):
    attrs  = ["bold"] if bold else []
    prefix = colored(f"[{ts()}]", "dark_grey" if TERMCOLOR_AVAILABLE else None)
    body   = colored(msg, color, attrs=attrs) if color else msg
    print(f"{prefix} {body}", flush=True)

def log_warn(msg):
    log(f"⚠  {msg}", "yellow")

def log_err(msg):
    log(f"✗  {msg}", "red", bold=True)

def log_ok(msg):
    log(f"✓  {msg}", "green")

def log_section(title):
    bar = colored("─" * 66, "cyan")
    print(bar, flush=True)
    print(colored(f"  {title}", "cyan", attrs=["bold"]), flush=True)
    print(bar, flush=True)

def sim_mode_tag(use_rpc: bool) -> str:
    if use_rpc:
        return colored("  [Isaac Sim RPC]", "green", attrs=["bold"])
    return colored("  [Dummy physics fallback]", "yellow")

def dry_run_tag(dry_run: bool) -> str:
    if dry_run:
        return colored("  [DRY-RUN — no disk write]", "yellow")
    return colored("  [COMMIT MODE]", "green", attrs=["bold"])

# ── JSON field accessor helpers ───────────────────────────────────────────────

def _resolve_segment(node, segment: str):
    """
    Resolve one path segment:
      - dict  → use segment as key
      - list + digit segment → use as integer index
      - list + non-digit     → id-based lookup (item["id"] == segment)
    """
    if isinstance(node, dict):
        return node[segment]
    if isinstance(node, list):
        if segment.isdigit():
            return node[int(segment)]
        for item in node:
            if isinstance(item, dict) and item.get("id") == segment:
                return item
        raise KeyError(f"No list item with id='{segment}'")
    raise TypeError(f"Cannot index {type(node).__name__} with '{segment}'")


def _set_segment(node, segment: str, value):
    if isinstance(node, dict):
        node[segment] = value
        return
    if isinstance(node, list):
        if segment.isdigit():
            node[int(segment)] = value
            return
        for i, item in enumerate(node):
            if isinstance(item, dict) and item.get("id") == segment:
                node[i] = value
                return
        raise KeyError(f"No list item with id='{segment}'")
    raise TypeError(f"Cannot set on {type(node).__name__} with '{segment}'")


def get_field(manifest: dict, dotpath: str):
    parts = dotpath.split(".")
    node  = manifest
    for p in parts:
        node = _resolve_segment(node, p)
    return node


def set_field(manifest: dict, dotpath: str, value) -> dict:
    m     = deepcopy(manifest)
    parts = dotpath.split(".")
    node  = m
    for p in parts[:-1]:
        node = _resolve_segment(node, p)
    _set_segment(node, parts[-1], value)
    return m


def ensure_fields(manifest: dict) -> dict:
    m = deepcopy(manifest)
    for dotpath, default in FIELD_DEFAULTS.items():
        try:
            get_field(m, dotpath)
        except (KeyError, IndexError, TypeError):
            log(f"  Field '{dotpath}' not found — injecting default: {default}", "yellow")
            m = set_field(m, dotpath, default)
    return m


def read_current_params(manifest: dict) -> dict:
    params = {}
    for name, cfg in MUTABLE_FIELD_MAP.items():
        try:
            params[name] = float(get_field(manifest, cfg["path"]))
        except (KeyError, IndexError, TypeError):
            params[name] = round((cfg["min"] + cfg["max"]) / 2, 3)
            log_warn(f"'{cfg['path']}' not found in manifest — using default {params[name]}")
    return params


def apply_params_to_manifest(manifest: dict, params: dict) -> dict:
    m = deepcopy(manifest)
    for name, value in params.items():
        cfg = MUTABLE_FIELD_MAP[name]
        m   = set_field(m, cfg["path"], round(value, 4))
    return m

# ── Backup ────────────────────────────────────────────────────────────────────

def create_backup(manifest_path: Path) -> Path:
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUPS_DIR / f"{manifest_path.stem}_backup_{stamp}.json"
    shutil.copy2(manifest_path, backup)
    return backup

# ── Disk I/O ──────────────────────────────────────────────────────────────────

def load_manifest(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def write_manifest(path: Path, manifest: dict):
    """Atomic write via .tmp + rename — crash-safe."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    tmp.replace(path)

def verify_roundtrip(path: Path, expected_params: dict) -> tuple[bool, str]:
    try:
        on_disk        = load_manifest(path)
        on_disk_params = read_current_params(on_disk)
        mismatches     = []
        for name, expected in expected_params.items():
            actual = on_disk_params.get(name)
            if actual is None or abs(actual - expected) > 1e-6:
                mismatches.append(f"{name}: expected {expected}, got {actual}")
        if mismatches:
            return False, "Round-trip mismatch: " + "; ".join(mismatches)
        return True, "OK"
    except Exception as e:
        return False, f"Round-trip read error: {e}"

# ── Safety checker ────────────────────────────────────────────────────────────

def safety_check(proposed_params: dict, manifest: dict) -> tuple[bool, str]:
    """
    Two-layer safety enforcement:
      Layer 1 — MUTABLE_FIELD_MAP hard limits (field-level)
      Layer 2 — manifest safety block (object-level)
    Returns (is_safe, reason).
    """
    safety = manifest.get("safety", {})

    for name, value in proposed_params.items():
        cfg = MUTABLE_FIELD_MAP[name]
        if value > cfg["max"]:
            return False, (
                f"SAFETY VETO [{name}]: {value:.3g}{cfg['unit']} "
                f"exceeds field max {cfg['max']}{cfg['unit']}"
            )
        if value < cfg["min"]:
            return False, (
                f"SAFETY VETO [{name}]: {value:.3g}{cfg['unit']} "
                f"below field min {cfg['min']}{cfg['unit']}"
            )

    grasp = proposed_params.get("grasp_force_n", 0)
    if grasp > safety.get("maxForceNewtons", 15.0):
        return False, (
            f"SAFETY VETO [grasp_force_n]: {grasp:.1f}N exceeds "
            f"manifest maxForceNewtons={safety.get('maxForceNewtons', 15.0)}N"
        )

    tilt = proposed_params.get("tilt_angle_deg", 0)
    if tilt > safety.get("maxTiltAngleDeg", 120.0):
        return False, (
            f"SAFETY VETO [tilt_angle_deg]: {tilt:.1f}° exceeds "
            f"manifest maxTiltAngleDeg={safety.get('maxTiltAngleDeg', 120.0)}°"
        )

    fill = proposed_params.get("fill_stop_fraction", 0)
    if fill > safety.get("maxFillStopFraction", 0.95):
        return False, (
            f"SAFETY VETO [fill_stop_fraction]: {fill:.2f} exceeds "
            f"manifest maxFillStopFraction={safety.get('maxFillStopFraction', 0.95)}"
        )
    if fill < safety.get("minFillStopFraction", 0.10):
        return False, (
            f"SAFETY VETO [fill_stop_fraction]: {fill:.2f} below "
            f"manifest minFillStopFraction={safety.get('minFillStopFraction', 0.10)}"
        )

    return True, "OK"

# ── RPC transport (stdlib only — no httpx dependency) ─────────────────────────

class RPCClient:
    """
    Minimal HTTP client for Isaac Sim / Isaac Lab experiment endpoint.
    Uses only stdlib urllib — no extra dependencies.

    real_sim=False (default): mock server mode — falls back gracefully on failure.
    real_sim=True:            real Isaac Lab mode — 60 s timeout, NO automatic fallback
                              (caller receives None on failure and loop aborts iteration).
    """

    def __init__(
        self,
        experiment_url: str,
        timeout:   float = RPC_TIMEOUT_S,
        real_sim:  bool  = False,
    ):
        self.experiment_url = experiment_url
        self.real_sim       = real_sim
        self.timeout        = REAL_SIM_TIMEOUT_S if real_sim else timeout
        # Derive health URL: same host:port + /health
        parsed              = urllib.parse.urlparse(experiment_url)
        self.health_url     = f"{parsed.scheme}://{parsed.netloc}/health"
        self._consecutive_failures = 0
        # In real-sim mode disable auto-disable (we always want to try);
        # in mock mode auto-disable after 3 consecutive failures.
        self._max_failures_before_disable = 999 if real_sim else 3

    def probe(self) -> tuple[bool, str]:
        """
        GET /health — returns (reachable, message).
        Used at startup to decide RPC vs dummy mode.
        """
        try:
            req = urllib.request.Request(self.health_url, method="GET")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                body = json.loads(resp.read().decode())
                return True, body.get("status", "ok")
        except urllib.error.URLError as e:
            return False, str(e.reason)
        except Exception as e:
            return False, str(e)

    def run_experiment(self, object_id: str, params: dict) -> tuple[dict | None, str]:
        """
        POST to experiment endpoint.
        Returns (metrics_dict, source) where source is "rpc" or "fallback".
        On any failure returns (None, error_message).

        Expected request body:
          {
            "object_id": "CHA-KIT-001",
            "params": { "tilt_angle_deg": 57.5, ... },
            "timestamp": "2026-03-13T09:15:00Z"
          }

        Expected response body:
          {
            "success": true,
            "metrics": {
              "composite_score": -0.42,
              "spill_rate":       0.08,
              "pour_accuracy":    0.91,
              "fill_efficiency":  0.88
            },
            "sim_time_ms": 142,
            "object_id": "CHA-KIT-001"
          }
        """
        if self._consecutive_failures >= self._max_failures_before_disable:
            return None, f"RPC disabled after {self._consecutive_failures} consecutive failures"

        payload = json.dumps({
            "object_id": object_id,
            "params":    params,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                self.experiment_url,
                data    = payload,
                headers = {
                    "Content-Type":   "application/json",
                    "Accept":         "application/json",
                    "X-Chameleon-Ver": "1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())

            if not body.get("success", False):
                self._consecutive_failures += 1
                return None, f"Server returned success=false: {body.get('error', 'unknown')}"

            metrics = body.get("metrics")
            if not metrics or "composite_score" not in metrics:
                self._consecutive_failures += 1
                return None, "Response missing metrics.composite_score"

            self._consecutive_failures = 0   # reset on success
            return metrics, "rpc"

        except urllib.error.URLError as e:
            self._consecutive_failures += 1
            return None, f"URLError: {e.reason}"
        except TimeoutError:
            self._consecutive_failures += 1
            return None, f"Timeout after {self.timeout}s"
        except json.JSONDecodeError as e:
            self._consecutive_failures += 1
            return None, f"JSON decode error: {e}"
        except Exception as e:
            self._consecutive_failures += 1
            return None, f"Unexpected error: {e}"

# ── Dummy physics fallback ────────────────────────────────────────────────────

def _dummy_experiment(params: dict) -> dict:
    """
    Pure-Python physics simulation.
    Used when Isaac Sim RPC is unavailable or --no-sim is set.

    Physics model:
      spill_rate      ↓ better — rises if tilt too high/low, duration too short
      pour_accuracy   ↑ better — peaks near (tilt=60°, duration=12s)
      fill_efficiency ↑ better — peaks near fillStopFraction=0.80
      composite_score = spill_rate - 0.4*pour_accuracy - 0.3*fill_efficiency
    """
    tilt  = params["tilt_angle_deg"]
    dur   = params["pour_duration_s"]
    force = params["grasp_force_n"]
    fill  = params["fill_stop_fraction"]

    tilt_spill  = 0.0018 * (tilt - 58) ** 2
    dur_spill   = 0.012  * max(0, 9 - dur) ** 2
    force_spill = 0.004  * max(0, force - 11) ** 2
    spill_rate  = max(0.0, tilt_spill + dur_spill + force_spill + rng.gauss(0, 0.007))

    tilt_acc  = math.exp(-0.5 * ((tilt - 60) / 11) ** 2)
    dur_acc   = math.exp(-0.5 * ((dur  - 12) / 3.5) ** 2)
    pour_acc  = min(1.0, max(0.0, tilt_acc * dur_acc + rng.gauss(0, 0.025)))

    fill_eff  = math.exp(-0.5 * ((fill - 0.80) / 0.11) ** 2)
    fill_eff  = min(1.0, max(0.0, fill_eff + rng.gauss(0, 0.02)))

    score = spill_rate - 0.4 * pour_acc - 0.3 * fill_eff

    return {
        "spill_rate":      round(spill_rate, 4),
        "pour_accuracy":   round(pour_acc,   4),
        "fill_efficiency": round(fill_eff,    4),
        "composite_score": round(score,       4),
    }

# ── Experiment dispatcher ─────────────────────────────────────────────────────

def run_experiment(
    params:    dict,
    object_id: str,
    rpc:       RPCClient | None,
    use_rpc:   bool,
    real_sim:  bool = False,
) -> tuple[dict | None, str]:
    """
    Dispatch to RPC (Isaac Sim / mock) or dummy physics.
    Returns (metrics_dict, source_label).

    source_label ∈ {"real_sim", "rpc", "dummy", "fallback"}

    In --real-sim mode:
      - Returns (None, error_msg) on failure so the caller can skip/abort.
      - NEVER falls back to dummy — real physics data only.
    In normal RPC mode:
      - Falls back to dummy on any failure.
    """
    if use_rpc and rpc is not None:
        metrics, source = rpc.run_experiment(object_id, params)
        if metrics is not None:
            label = "real_sim" if real_sim else "rpc"
            return metrics, label

        # ── real-sim mode: no fallback ─────────────────────────────────────────
        if real_sim:
            log_err(f"Isaac Sim offline / failed ({source}) — skipping iteration (--real-sim set)")
            return None, f"real_sim_fail: {source}"

        # ── mock/auto mode: fall back to dummy ─────────────────────────────────
        log_warn(f"RPC failed ({source}) — Isaac Sim offline, using dummy fallback")
        return _dummy_experiment(params), "fallback"

    return _dummy_experiment(params), "dummy"

# ── Proposer ──────────────────────────────────────────────────────────────────

# Module-level step_scale — set by run_karpathy_loop() from CLI args
_step_scale: float = 1.0


def propose_change(current_params: dict) -> tuple[dict, str, str]:
    """
    Fixed-step mutation on one randomly chosen mutable field.

    Each field has a pre-calibrated absolute step size (MUTABLE_FIELD_MAP["step"]):
      grasp_force_n       ±0.5  N
      tilt_angle_deg      ±2.5  °
      fill_stop_fraction  ±0.02
      pour_duration_s     ±0.5  s
      lift_height_cm      ±1.0  cm

    The global _step_scale multiplier (from --step-scale CLI flag) scales all
    steps uniformly — use 0.5 for finer convergence, 2.0 for faster exploration.

    Why fixed steps vs percentage?
    ─ Near optima the current value is close to the target.  A 7% step on a
      tilt already at 57° would move it only ±4° — still sensible.  But on
      grasp_force_n already near 8.5 N, a 7% step is ±0.6 N which is larger
      than the fixed 0.5 N step.  Fixed steps give consistent, predictable
      exploration width regardless of the current value.
    """
    field_name = rng.choice(list(MUTABLE_FIELD_MAP.keys()))
    cfg        = MUTABLE_FIELD_MAP[field_name]
    old_val    = current_params[field_name]
    direction  = rng.choice([-1, +1])
    step       = cfg["step"] * _step_scale
    new_val    = round(
        max(cfg["min"], min(cfg["max"], old_val + direction * step)), 4
    )
    new_params             = deepcopy(current_params)
    new_params[field_name] = new_val

    sign      = "+" if new_val >= old_val else "-"
    abs_delta = abs(new_val - old_val)
    pct_delta = abs_delta / old_val * 100 if old_val else 0
    desc = (
        f"Tweak {field_name}: "
        f"{old_val}{cfg['unit']} → {new_val}{cfg['unit']} "
        f"({sign}{abs_delta:.3g}{cfg['unit']}, {sign}{pct_delta:.1f}%)"
    )
    return new_params, field_name, desc

# ── TSV logger ────────────────────────────────────────────────────────────────

def init_tsv():
    if not RESULTS_TSV.exists():
        with open(RESULTS_TSV, "w", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow([
                "iteration", "timestamp", "field_changed",
                "old_val", "new_val",
                "spill_rate", "pour_accuracy", "fill_efficiency",
                "composite_score", "status",
                "experiment_source", "dry_run", "description",
            ])

def append_tsv(row: dict, dry_run: bool, source: str = ""):
    with open(RESULTS_TSV, "a", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow([
            row["iteration"],
            row["timestamp"],
            row.get("field_changed", ""),
            row.get("old_val", ""),
            row.get("new_val", ""),
            row["metrics"]["spill_rate"],
            row["metrics"]["pour_accuracy"],
            row["metrics"]["fill_efficiency"],
            row["metrics"]["composite_score"],
            row["status"],
            source,
            "dry-run" if dry_run else "commit",
            row.get("description", ""),
        ])

# ── Commit log ────────────────────────────────────────────────────────────────

def load_commit_log() -> list:
    if COMMIT_LOG.exists():
        with open(COMMIT_LOG, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_commit_log(data: list):
    with open(COMMIT_LOG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ── Matplotlib plot ───────────────────────────────────────────────────────────

def save_plot(history: list, dry_run: bool, use_rpc: bool):
    if not MATPLOTLIB_AVAILABLE:
        _ascii_plot(history)
        return

    iters   = [h["iteration"] for h in history]
    scores  = [h["metrics"]["composite_score"] for h in history]
    spills  = [h["metrics"]["spill_rate"]       for h in history]
    sources = [h.get("source", "dummy")          for h in history]
    colors  = [
        "green"  if h["status"] == "keep"     else
        "orange" if h["status"] == "veto"     else
        "grey"   if h["status"] == "baseline" else
        "red"
        for h in history
    ]

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 9), sharex=True,
                                         gridspec_kw={"height_ratios": [3, 2, 1]})
    fig.patch.set_facecolor("#1a1a2e")
    for ax in (ax1, ax2, ax3):
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#444")

    # Running best line
    best_so_far, best = [], scores[0]
    for s in scores:
        if s < best:
            best = s
        best_so_far.append(best)

    ax1.plot(iters, scores,      color="#555577", linewidth=0.7, zorder=1)
    ax1.plot(iters, best_so_far, color="#44aaff", linewidth=1.8, zorder=2, label="best so far")
    for it, sc, col in zip(iters, scores, colors):
        ax1.scatter(it, sc, color=col, s=20, zorder=3)

    mode_tag = "(dry-run)" if dry_run else "(commit mode)"
    sim_tag  = "Isaac Sim RPC" if use_rpc else "Dummy physics"
    ax1.set_ylabel("Composite Score (↓ better)", color="white")
    ax1.set_title(
        f"Chameleon Karpathy Loop — Kettle Optimisation {mode_tag} | {sim_tag}",
        color="white", fontsize=11,
    )

    ax2.plot(iters, spills, color="#ff6b6b", linewidth=1.2)
    ax2.fill_between(iters, spills, alpha=0.18, color="#ff6b6b")
    ax2.set_ylabel("Spill Rate (↓ better)", color="white")

    # Source indicator row (rpc=teal, dummy=grey, fallback=orange)
    source_colors = {
        "rpc":      "#00bfa5",
        "dummy":    "#888888",
        "fallback": "#ff9800",
        "":         "#555555",
    }
    bar_cols = [source_colors.get(s, "#555555") for s in sources]
    ax3.bar(iters, [1] * len(iters), color=bar_cols, width=0.8)
    ax3.set_yticks([])
    ax3.set_ylabel("Src", color="white", fontsize=8)
    ax3.set_xlabel("Iteration", color="white")

    from matplotlib.patches import Patch
    legend_els = [
        Patch(facecolor="green",   label="Committed"),
        Patch(facecolor="red",     label="Reverted"),
        Patch(facecolor="orange",  label="Safety Veto"),
        Patch(facecolor="#44aaff", label="Best so far"),
        Patch(facecolor="#00bfa5", label="RPC source"),
        Patch(facecolor="#888888", label="Dummy source"),
        Patch(facecolor="#ff9800", label="Fallback source"),
    ]
    ax1.legend(handles=legend_els, facecolor="#1a1a2e", labelcolor="white",
               fontsize=7, ncol=4, loc="upper right")

    plt.tight_layout()
    plt.savefig(PLOT_PATH, dpi=130, bbox_inches="tight")
    plt.close()
    log(f"Plot saved → {PLOT_PATH}  (open locally to view)", "cyan")

def _ascii_plot(history: list):
    print("\n" + colored("  Composite Score History (ASCII fallback)", "cyan", attrs=["bold"]))
    recent = history[-25:]
    scores = [h["metrics"]["composite_score"] for h in recent]
    if not scores:
        return
    lo, hi = min(scores), max(scores)
    span   = hi - lo if hi != lo else 1.0
    width  = 28
    for h, sc in zip(recent, scores):
        bar_len = int((sc - lo) / span * width)
        sym     = "✓" if h["status"] == "keep" else ("⚠" if h["status"] == "veto" else "✗")
        src     = h.get("source", "?")[0].upper()
        print(f"  {h['iteration']:>3} {sym}{src} |{'█'*bar_len:<{width}}| {sc:+.4f}")

# ── Progress bar ──────────────────────────────────────────────────────────────

def make_progress_bar(iterable, total, desc=""):
    if TQDM_AVAILABLE:
        return tqdm(iterable, total=total, desc=desc,
                    bar_format="{l_bar}{bar:28}{r_bar}",
                    colour="cyan", dynamic_ncols=True)
    return iterable

# ── Keyboard controls ─────────────────────────────────────────────────────────

def _keyboard_listener():
    try:
        import tty, termios
    except ImportError:
        return
    fd = sys.stdin.fileno()
    try:
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1).lower()
            with _state_lock:
                if ch == "p":
                    _state["paused"] = not _state["paused"]
                    label = "PAUSED ⏸" if _state["paused"] else "RESUMED ▶"
                    print(f"\n{colored(f'  [ {label} ]', 'yellow', attrs=['bold'])}", flush=True)
                elif ch == "s":
                    _state["stopped"] = True
                    print(f"\n{colored('  [ STOPPING ]', 'red', attrs=['bold'])}", flush=True)
                    break
                elif ch == "r":
                    _state["restart"] = True
                    print(f"\n{colored('  [ RESTART → last commit ]', 'magenta', attrs=['bold'])}", flush=True)
                elif ch == "c":
                    _state["cancelled"] = True
                    _state["stopped"]   = True
                    print(f"\n{colored('  [ CANCELLED → restoring backup ]', 'red', attrs=['bold'])}", flush=True)
                    break
    except Exception:
        pass
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            pass

def start_keyboard_thread():
    t = threading.Thread(target=_keyboard_listener, daemon=True)
    t.start()

# ── Main Karpathy loop ────────────────────────────────────────────────────────

def run_karpathy_loop(
    manifest_path: Path,
    max_iters:     int   = DEFAULT_ITERS,
    dry_run:       bool  = True,
    sim_url:       str   = DEFAULT_SIM_URL,
    no_sim:        bool  = False,
    step_scale:    float = DEFAULT_STEP_SCALE,
    real_sim:      bool  = False,
):
    """
    Greedy hill-climbing self-improvement loop.

    Backends (in priority order):
      --real-sim  : Real Isaac Lab at sim_url (60 s timeout, NO fallback).
                    Loop skips iteration on failure; stops after 5 consecutive fails.
      default     : Mock server auto-detected; falls back to dummy on failure.
      --no-sim    : Dummy physics only (no network calls).
    """
    global _step_scale
    _step_scale = step_scale

    if not manifest_path.exists():
        log_err(f"Manifest not found: {manifest_path}")
        sys.exit(1)

    # ── Load and prep manifest ────────────────────────────────────────────────
    manifest = load_manifest(manifest_path)
    manifest = ensure_fields(manifest)
    obj_id   = manifest.get("objectId",    "CHA-KIT-001")
    obj_name = manifest.get("displayName", "Unknown Object")

    # ── Backup ────────────────────────────────────────────────────────────────
    backup_path = create_backup(manifest_path)

    # ── RPC probe ─────────────────────────────────────────────────────────────
    rpc     = RPCClient(sim_url, real_sim=real_sim)
    use_rpc = False

    if no_sim:
        log("--no-sim set — using dummy physics mode.", "yellow")
    else:
        timeout_label = f"{REAL_SIM_TIMEOUT_S}s" if real_sim else f"{RPC_TIMEOUT_S}s"
        sim_label     = "Real Isaac Lab" if real_sim else "Isaac Sim mock"
        log(f"Probing {sim_label} at {sim_url} (timeout {timeout_label}) …", "cyan")
        reachable, msg = rpc.probe()
        if reachable:
            use_rpc = True
            log_ok(f"{sim_label} reachable — status: {msg}")
        else:
            if real_sim:
                log_err(
                    f"--real-sim set but Isaac Lab not reachable ({msg}).\n"
                    f"  Start the listener:  python3 isaac_lab_kettle_experiment.py\n"
                    f"  Or remove --real-sim to use mock/dummy fallback."
                )
                sys.exit(1)
            else:
                log_warn(f"Isaac Sim not reachable ({msg}) — using dummy physics fallback")
                log_warn(f"  Start mock server: python3 mock_server.py")

    # ── Banner ─────────────────────────────────────────────────────────────────
    log_section("Chameleon Karpathy Protocol — Autonomous Manifest Improvement")
    log(f"Manifest  : {manifest_path}", "white", bold=True)
    log(f"Object    : {obj_name} ({obj_id})", "white", bold=True)
    log(f"Backup    : {backup_path}", "yellow")
    if real_sim and use_rpc:
        sim_mode_str = colored("  [Real Isaac Lab]", "green", attrs=["bold"]) if TERMCOLOR_AVAILABLE else "  [Real Isaac Lab]"
    else:
        sim_mode_str = sim_mode_tag(use_rpc)
    log(f"Sim mode  :{sim_mode_str}", bold=True)
    log(f"Disk mode :{dry_run_tag(dry_run)}", bold=True)
    log(f"Max iters : {max_iters}   Step scale: ×{step_scale}  (fixed steps: ±0.5 N, ±2.5°, ±0.02, ±0.5 s, ±1 cm)")
    log(f"RPC URL   : {sim_url}  timeout={'60s (real-sim)' if real_sim else '30s'}")
    log(f"Controls  : [p] pause  [s] stop  [r] restart  [c] cancel+revert")
    log(f"Plot      : {PLOT_PATH}")
    print()

    try:
        start_keyboard_thread()
        log("Keyboard controls active.", "cyan")
    except Exception:
        log("Keyboard controls unavailable (non-interactive terminal).", "yellow")

    init_tsv()
    commit_log_data = load_commit_log()
    history         = []
    rpc_calls       = 0
    fallback_calls  = 0
    dummy_calls     = 0

    current_params  = read_current_params(manifest)
    baseline_params = deepcopy(current_params)

    log(f"\nInitial params (from manifest):", "white")
    for name, val in current_params.items():
        cfg = MUTABLE_FIELD_MAP[name]
        log(f"  {name:<24} = {val}{cfg['unit']}  [min={cfg['min']}, max={cfg['max']}]", "cyan")
    print()

    # ── Baseline experiment ───────────────────────────────────────────────────
    baseline_metrics, baseline_src = run_experiment(
        current_params, obj_id, rpc, use_rpc, real_sim=real_sim
    )
    if baseline_metrics is None:
        log_err("Baseline experiment failed (Isaac Sim offline). Cannot start loop.")
        sys.exit(1)
    best_score  = baseline_metrics["composite_score"]
    best_params = deepcopy(current_params)

    log(f"Baseline composite_score : {best_score:+.4f}  [{baseline_src}]", "yellow", bold=True)
    log(f"  spill={baseline_metrics['spill_rate']:.4f}  "
        f"acc={baseline_metrics['pour_accuracy']:.4f}  "
        f"fill_eff={baseline_metrics['fill_efficiency']:.4f}", "yellow")
    print()

    history.append({
        "iteration": 0, "metrics": baseline_metrics,
        "status": "baseline", "description": "Baseline",
        "field_changed": "", "old_val": "", "new_val": "",
        "source": baseline_src,
    })
    append_tsv(
        {"iteration": 0, "timestamp": datetime.now().isoformat(),
         "metrics": baseline_metrics, "status": "baseline", "description": "Baseline"},
        dry_run, baseline_src,
    )

    commits = reverts = vetoes = 0
    real_sim_consecutive_fails = 0
    REAL_SIM_MAX_CONSECUTIVE_FAILS = 5

    iter_range = make_progress_bar(
        range(1, max_iters + 1), total=max_iters, desc="Karpathy loop"
    )

    for iteration in iter_range:

        # ── Control checks ────────────────────────────────────────────────────
        with _state_lock:
            if _state["stopped"] or _state["cancelled"]:
                break
            if _state["restart"]:
                log(f"  ↺ Restart → reverting to best committed params (commit #{commits})", "magenta")
                current_params    = deepcopy(best_params)
                _state["restart"] = False

        while True:
            with _state_lock:
                if not _state["paused"]:
                    break
            time.sleep(0.2)

        # ── Step 1: Propose ───────────────────────────────────────────────────
        proposed_params, field_name, description = propose_change(current_params)
        old_val = current_params[field_name]
        new_val = proposed_params[field_name]

        # ── Step 2: Safety check ──────────────────────────────────────────────
        is_safe, veto_reason = safety_check(proposed_params, manifest)
        if not is_safe:
            log(f"  Iter {iteration:>3} │ {colored('⚠ VETO   ', 'yellow')} │ {description}", "yellow")
            log(f"             {veto_reason}", "red")
            # Run experiment with unchanged params for logging continuity
            veto_metrics, veto_src = run_experiment(current_params, obj_id, rpc, use_rpc, real_sim=real_sim)
            if veto_metrics is None:
                veto_metrics = {"composite_score": 0.0, "spill_rate": 0.0, "pour_accuracy": 0.0, "fill_efficiency": 0.0}
            history.append({
                "iteration": iteration, "metrics": veto_metrics,
                "status": "veto", "description": description,
                "field_changed": field_name, "old_val": old_val, "new_val": new_val,
                "source": veto_src,
            })
            append_tsv(
                {"iteration": iteration, "timestamp": datetime.now().isoformat(),
                 "field_changed": field_name, "old_val": old_val, "new_val": new_val,
                 "metrics": veto_metrics, "status": "veto", "description": veto_reason},
                dry_run, veto_src,
            )
            vetoes += 1
            time.sleep(ITERATION_DELAY)
            continue

        # ── Step 3: Run experiment ────────────────────────────────────────────
        metrics, exp_source = run_experiment(proposed_params, obj_id, rpc, use_rpc, real_sim=real_sim)

        # Handle real-sim failure (no fallback in real-sim mode)
        if metrics is None:
            real_sim_consecutive_fails += 1
            log_warn(f"  Iter {iteration:>3} │ Isaac Sim offline — skipping iteration "
                     f"({real_sim_consecutive_fails}/{REAL_SIM_MAX_CONSECUTIVE_FAILS} consecutive)")
            if real_sim_consecutive_fails >= REAL_SIM_MAX_CONSECUTIVE_FAILS:
                log_err(f"Isaac Sim failed {REAL_SIM_MAX_CONSECUTIVE_FAILS} times in a row. Aborting.")
                break
            time.sleep(ITERATION_DELAY)
            continue
        real_sim_consecutive_fails = 0   # reset on success

        if   exp_source in ("rpc", "real_sim"): rpc_calls      += 1
        elif exp_source == "fallback":           fallback_calls += 1
        else:                                    dummy_calls    += 1

        score    = metrics["composite_score"]
        delta    = score - best_score
        improved = score < best_score

        # ── Step 4: Commit or revert ──────────────────────────────────────────
        if improved:
            status     = "keep"
            status_sym = colored("✓ COMMIT ", "green", attrs=["bold"])
            current_params = deepcopy(proposed_params)
            best_score     = score
            best_params    = deepcopy(proposed_params)

            if not dry_run:
                updated_manifest = apply_params_to_manifest(manifest, current_params)
                write_manifest(manifest_path, updated_manifest)
                manifest = ensure_fields(load_manifest(manifest_path))
                ok, detail = verify_roundtrip(manifest_path, current_params)
                if ok:
                    log_ok(f"             Round-trip verify: OK")
                else:
                    log_err(f"             Round-trip verify FAILED: {detail}")

            commit_log_data.append({
                "commit_id":     f"CHA-{iteration:04d}",
                "iteration":     iteration,
                "timestamp":     datetime.now().isoformat(),
                "dry_run":       dry_run,
                "experiment_src": exp_source,
                "field_changed": field_name,
                "old_val":       old_val,
                "new_val":       new_val,
                "description":   description,
                "score_before":  round(best_score - delta, 4),
                "score_after":   round(best_score, 4),
                "delta":         round(delta, 4),
                "params":        deepcopy(current_params),
            })
            save_commit_log(commit_log_data)
            commits += 1

        else:
            status     = "discard"
            status_sym = colored("✗ REVERT ", "red")
            reverts   += 1

        # ── Step 5: Log ───────────────────────────────────────────────────────
        delta_str  = colored(f"{delta:+.4f}", "green" if improved else "red")
        src_tag    = colored(f"[{exp_source}]", "green" if exp_source == "rpc" else
                             "yellow" if exp_source == "fallback" else "dark_grey"
                             if TERMCOLOR_AVAILABLE else None)
        dry_marker = colored(" ~", "yellow") if dry_run else ""

        log(
            f"  Iter {iteration:>3} │ {status_sym}│ {description}\n"
            f"             score={score:+.4f}  Δ={delta_str}  "
            f"spill={metrics['spill_rate']:.4f}  "
            f"acc={metrics['pour_accuracy']:.4f}  "
            f"fill={metrics['fill_efficiency']:.4f}  {src_tag}{dry_marker}"
        )

        history.append({
            "iteration": iteration, "metrics": metrics,
            "status": status, "description": description,
            "field_changed": field_name, "old_val": old_val, "new_val": new_val,
            "source": exp_source,
        })
        append_tsv(
            {"iteration": iteration, "timestamp": datetime.now().isoformat(),
             "field_changed": field_name, "old_val": old_val, "new_val": new_val,
             "metrics": metrics, "status": status, "description": description},
            dry_run, exp_source,
        )

        if iteration % 10 == 0:
            save_plot(history, dry_run, use_rpc)
            log(
                f"  ── Checkpoint {iteration:>3} ──  "
                f"commits={commits}  reverts={reverts}  vetoes={vetoes}  "
                f"best={best_score:+.4f}  "
                f"rpc={rpc_calls}  fallback={fallback_calls}  dummy={dummy_calls}",
                "cyan",
            )

        time.sleep(ITERATION_DELAY)

    # ── Session end ───────────────────────────────────────────────────────────

    with _state_lock:
        cancelled = _state["cancelled"]

    print()
    log_section("Session Complete")

    if cancelled and not dry_run:
        log("Cancelled — restoring backup…", "red", bold=True)
        shutil.copy2(backup_path, manifest_path)
        log_ok(f"Restored from: {backup_path}")
    elif dry_run:
        log("Dry-run complete — manifest NOT modified on disk.", "yellow", bold=True)
        log("  Re-run with --commit to apply improvements.", "yellow")
    else:
        log("Committed improvements saved to manifest ✓", "green", bold=True)

    if commits > 0:
        log(f"\nParameter changes (baseline → best):", "white")
        for name, baseline_val in baseline_params.items():
            best_val = best_params[name]
            cfg      = MUTABLE_FIELD_MAP[name]
            if abs(best_val - baseline_val) > 1e-9:
                direction = "↑" if best_val > baseline_val else "↓"
                pct_chg   = (best_val - baseline_val) / baseline_val * 100 if baseline_val else 0
                log(f"  {name:<24} {baseline_val}{cfg['unit']} → {best_val}{cfg['unit']} "
                    f"{direction} ({pct_chg:+.1f}%)", "green")

    improvement = baseline_metrics["composite_score"] - best_score
    print()
    log(f"Iterations       : {len(history) - 1}", "white")
    log(f"Commits          : {commits}",          "green")
    log(f"Reverts          : {reverts}",          "yellow")
    log(f"Safety vetoes    : {vetoes}",            "red")
    rpc_label = "Real Isaac Lab calls" if real_sim else "RPC calls        "
    log(f"{rpc_label}: {rpc_calls}",         "green"  if rpc_calls      else "white")
    log(f"Fallback calls   : {fallback_calls}",    "yellow" if fallback_calls  else "white")
    log(f"Dummy calls      : {dummy_calls}",        "white")
    log(f"Baseline score   : {baseline_metrics['composite_score']:+.4f}", "white")
    log(f"Best score       : {best_score:+.4f}",
        "green" if improvement > 0 else "white")
    log(f"Total gain       : {improvement:+.4f}  "
        f"({'improvement ✓' if improvement > 0 else 'no improvement'})",
        "green" if improvement > 0 else "yellow", bold=True)
    print()

    save_plot(history, dry_run, use_rpc)

    log(f"Results TSV : {RESULTS_TSV}", "cyan")
    log(f"Commit log  : {COMMIT_LOG}", "cyan")
    log(f"Backup      : {backup_path}", "cyan")
    log(f"Plot        : {PLOT_PATH}", "cyan")
    if not dry_run:
        log(f"Manifest    : {manifest_path}  ← updated", "cyan", bold=True)
    print()
    log("Chameleon Karpathy Protocol — session complete. 🦎", "cyan", bold=True)

# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Chameleon Karpathy Protocol — autonomous manifest self-improvement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry-run, auto-detect Isaac Sim RPC:
  python3 chameleon_karpathy_test.py

  # Commit mode with RPC:
  python3 chameleon_karpathy_test.py --commit

  # Force dummy physics (no server needed):
  python3 chameleon_karpathy_test.py --no-sim

  # Custom RPC URL, 100 iterations:
  python3 chameleon_karpathy_test.py --commit --iters 100 \\
      --sim-url http://192.168.1.50:8211/api/chameleon/experiment

  # Target a different manifest:
  python3 chameleon_karpathy_test.py --commit \\
      --manifest ../../chameleon_library/workshop/hammer_manifest.json
        """,
    )
    p.add_argument("--commit",   action="store_true", default=False,
                   help="Write improvements to manifest on disk (default: dry-run)")
    p.add_argument("--no-sim",   action="store_true", default=False,
                   help="Skip RPC probe, use dummy physics only")
    p.add_argument("--real-sim", action="store_true", default=False,
                   help="Require real Isaac Lab at sim-url (60s timeout, NO dummy fallback). "
                        "Aborts if Isaac Lab is unreachable.")
    p.add_argument("--iters",    type=int, default=DEFAULT_ITERS, metavar="N",
                   help=f"Number of iterations (default: {DEFAULT_ITERS})")
    p.add_argument("--sim-url",  type=str, default=DEFAULT_SIM_URL, metavar="URL",
                   help=f"Isaac Sim RPC URL (default: {DEFAULT_SIM_URL})")
    p.add_argument("--manifest", type=Path, default=REAL_MANIFEST, metavar="PATH",
                   help=f"Path to manifest JSON (default: {REAL_MANIFEST})")
    p.add_argument("--step-scale", type=float, default=DEFAULT_STEP_SCALE, metavar="F",
                   help=f"Scale factor for all mutation steps (default: {DEFAULT_STEP_SCALE}). "
                        "0.5 = finer (half-steps), 2.0 = coarser (double-steps)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    # --real-sim and --no-sim are mutually exclusive
    if args.real_sim and args.no_sim:
        print("ERROR: --real-sim and --no-sim are mutually exclusive.", file=sys.stderr)
        sys.exit(1)
    run_karpathy_loop(
        manifest_path = args.manifest,
        max_iters     = args.iters,
        dry_run       = not args.commit,
        sim_url       = args.sim_url,
        no_sim        = args.no_sim,
        step_scale    = args.step_scale,
        real_sim      = args.real_sim,
    )
