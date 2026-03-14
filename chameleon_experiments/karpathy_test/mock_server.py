"""
mock_server.py
==============
Chameleon Protocol — Isaac Sim Mock RPC Server
Simulates the physics experiment endpoint that a real Isaac Sim integration
would provide.  Run this in a separate terminal before starting the
Karpathy loop.

Protocol:
  POST /api/chameleon/experiment
    Request:  { "object_id": "CHA-KIT-001",
                "params": { "tilt_angle_deg": 57.5, ... },
                "timestamp": "2026-03-13T09:15:00Z" }
    Response: { "success": true,
                "metrics": { "composite_score": -0.42,
                              "spill_rate": 0.08,
                              "pour_accuracy": 0.91,
                              "fill_efficiency": 0.88 },
                "sim_time_ms": 142,
                "object_id": "CHA-KIT-001" }

  GET  /health
    Response: { "status": "ok", "server": "Chameleon Isaac Sim Mock",
                "version": "1.0.0", "experiments_served": 12 }

  GET  /stats
    Response: full session statistics

Usage:
  # Install FastAPI if available (preferred):
  pip3 install fastapi uvicorn

  # Or run with stdlib-only fallback (http.server):
  python3 mock_server.py            # auto-selects FastAPI or stdlib
  python3 mock_server.py --port 8211
  python3 mock_server.py --stdlib   # force stdlib mode
  python3 mock_server.py --verbose  # log every request body

Author: Chameleon Developer Agent v1.0
"""

import argparse
import json
import math
import random
import time
import sys
from datetime import datetime

# ── Shared physics engine (used by both backends) ─────────────────────────────

_rng = random.Random(99)   # different seed from test script for realism

# Supported object classes — each has its own physics model
OBJECT_PHYSICS = {
    "CHA-KIT-001": "kettle",
    "CHA-KIT-002": "fridge",
    "CHA-KIT-003": "teaspoon",
    "default":     "kettle",
}

def _kettle_physics(params: dict, noise_scale: float = 0.01) -> dict:
    """
    Realistic kettle-pouring physics model.
    Intentionally uses slightly different constants from chameleon_karpathy_test.py
    to simulate real measurement noise from a physics engine.

    Optima (what the Karpathy loop should converge toward):
      tilt_angle_deg   ≈ 58°
      pour_duration_s  ≈ 11–13s
      fill_stop_frac   ≈ 0.78–0.82
      grasp_force_n    ≈ 8–10 N
    """
    tilt  = float(params.get("tilt_angle_deg",    45.0))
    dur   = float(params.get("pour_duration_s",   20.0))
    force = float(params.get("grasp_force_n",      8.0))
    fill  = float(params.get("fill_stop_fraction", 0.85))

    # ── Spill model ───────────────────────────────────────────────────────────
    # Parabola: minimum near tilt=58°, rises steeply for tilt>75° (loss of control)
    tilt_spill   = 0.0015 * (tilt - 58) ** 2
    if tilt > 75:
        tilt_spill += 0.003 * (tilt - 75) ** 2   # extra penalty for extreme tilt
    dur_spill    = 0.014  * max(0, 10 - dur) ** 2
    force_spill  = 0.003  * max(0, force - 12) ** 2
    fill_spill   = 0.05   * max(0, fill - 0.88)   # extra penalty near overflow
    spill_rate   = max(0.0,
        tilt_spill + dur_spill + force_spill + fill_spill
        + _rng.gauss(0, noise_scale)
    )

    # ── Pour accuracy ─────────────────────────────────────────────────────────
    # Gaussian peak at (58°, 12s) — force and fill have minor influence
    tilt_acc   = math.exp(-0.5 * ((tilt - 58) / 10) ** 2)
    dur_acc    = math.exp(-0.5 * ((dur  - 12) / 4.0) ** 2)
    force_acc  = math.exp(-0.5 * ((force - 9) / 5.0) ** 2)
    pour_acc   = min(1.0, max(0.0,
        tilt_acc * dur_acc * (0.85 + 0.15 * force_acc)
        + _rng.gauss(0, noise_scale * 2)
    ))

    # ── Fill efficiency ───────────────────────────────────────────────────────
    fill_eff = math.exp(-0.5 * ((fill - 0.79) / 0.10) ** 2)
    fill_eff = min(1.0, max(0.0, fill_eff + _rng.gauss(0, noise_scale * 1.5)))

    # ── Composite score ───────────────────────────────────────────────────────
    # Lower = better, mirrors Karpathy val_bpb
    score = spill_rate - 0.4 * pour_acc - 0.3 * fill_eff

    return {
        "spill_rate":      round(spill_rate, 4),
        "pour_accuracy":   round(pour_acc,   4),
        "fill_efficiency": round(fill_eff,    4),
        "composite_score": round(score,       4),
    }


def _generic_physics(params: dict) -> dict:
    """Fallback for unknown object types."""
    return _kettle_physics(params, noise_scale=0.015)


def simulate_experiment(object_id: str, params: dict) -> tuple[dict, float]:
    """
    Dispatch to correct physics model and simulate wall-clock latency.
    Returns (metrics, sim_time_ms).
    """
    t0 = time.perf_counter()

    # Simulate realistic physics engine latency (50–250ms)
    sim_latency = _rng.uniform(0.05, 0.25)
    time.sleep(sim_latency)

    obj_type = OBJECT_PHYSICS.get(object_id, OBJECT_PHYSICS["default"])
    if obj_type == "kettle":
        metrics = _kettle_physics(params)
    else:
        metrics = _generic_physics(params)

    sim_time_ms = round((time.perf_counter() - t0) * 1000, 1)
    return metrics, sim_time_ms


# ── Session stats ─────────────────────────────────────────────────────────────

_stats = {
    "experiments_served": 0,
    "success_count":      0,
    "error_count":        0,
    "total_sim_ms":       0.0,
    "start_time":         datetime.now().isoformat(),
    "last_request":       None,
}


# ══════════════════════════════════════════════════════════════════════════════
# BACKEND A — FastAPI (preferred, richer features)
# ══════════════════════════════════════════════════════════════════════════════

def run_fastapi_server(port: int, verbose: bool):
    try:
        from fastapi import FastAPI, Request, HTTPException
        from fastapi.responses import JSONResponse
        import uvicorn
    except ImportError:
        print("FastAPI/uvicorn not installed. Falling back to stdlib server.")
        print("  To install: pip3 install fastapi uvicorn")
        run_stdlib_server(port, verbose)
        return

    app = FastAPI(
        title="Chameleon Isaac Sim Mock Server",
        description="Simulates Isaac Sim physics experiment endpoint for the Karpathy protocol.",
        version="1.0.0",
    )

    @app.get("/health")
    async def health():
        return {
            "status":              "ok",
            "server":              "Chameleon Isaac Sim Mock",
            "version":             "1.0.0",
            "experiments_served":  _stats["experiments_served"],
            "uptime_s":            round(
                (datetime.now() - datetime.fromisoformat(_stats["start_time"])).total_seconds(), 1
            ),
        }

    @app.get("/stats")
    async def stats():
        avg_ms = (
            round(_stats["total_sim_ms"] / _stats["experiments_served"], 1)
            if _stats["experiments_served"] > 0 else 0
        )
        return {**_stats, "avg_sim_ms": avg_ms}

    @app.post("/api/chameleon/experiment")
    async def experiment(request: Request):
        """
        Main experiment endpoint.

        Request body:
          {
            "object_id": "CHA-KIT-001",
            "params": {
              "tilt_angle_deg":    57.5,
              "pour_duration_s":   11.2,
              "grasp_force_n":     8.4,
              "fill_stop_fraction": 0.81,
              "lift_height_cm":    22.0
            },
            "timestamp": "2026-03-13T09:15:00Z"
          }

        Response:
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
        try:
            body = await request.json()
        except Exception as e:
            _stats["error_count"] += 1
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

        object_id = body.get("object_id", "CHA-KIT-001")
        params    = body.get("params",    {})
        timestamp = body.get("timestamp", datetime.utcnow().isoformat())

        if verbose:
            print(
                f"\n[{datetime.now().strftime('%H:%M:%S')}] POST /api/chameleon/experiment\n"
                f"  object_id : {object_id}\n"
                f"  params    : {json.dumps(params, separators=(',', ':'))}\n"
                f"  timestamp : {timestamp}",
                flush=True,
            )

        if not params:
            _stats["error_count"] += 1
            raise HTTPException(status_code=422, detail="'params' field is required")

        metrics, sim_time_ms = simulate_experiment(object_id, params)

        _stats["experiments_served"] += 1
        _stats["success_count"]      += 1
        _stats["total_sim_ms"]       += sim_time_ms
        _stats["last_request"]        = timestamp

        response = {
            "success":     True,
            "metrics":     metrics,
            "sim_time_ms": sim_time_ms,
            "object_id":   object_id,
        }

        if verbose:
            print(
                f"  → composite={metrics['composite_score']:+.4f}  "
                f"spill={metrics['spill_rate']:.4f}  "
                f"acc={metrics['pour_accuracy']:.4f}  "
                f"fill={metrics['fill_efficiency']:.4f}  "
                f"({sim_time_ms:.0f}ms)",
                flush=True,
            )

        return JSONResponse(response)

    print(f"\n{'─'*60}", flush=True)
    print(f"  Chameleon Isaac Sim Mock Server (FastAPI)", flush=True)
    print(f"  Port    : {port}", flush=True)
    print(f"  Health  : http://localhost:{port}/health", flush=True)
    print(f"  Endpoint: http://localhost:{port}/api/chameleon/experiment", flush=True)
    print(f"  Docs    : http://localhost:{port}/docs", flush=True)
    print(f"  Verbose : {verbose}", flush=True)
    print(f"{'─'*60}\n", flush=True)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


# ══════════════════════════════════════════════════════════════════════════════
# BACKEND B — stdlib http.server (zero dependencies)
# ══════════════════════════════════════════════════════════════════════════════

def run_stdlib_server(port: int, verbose: bool):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class ChameleonHandler(BaseHTTPRequestHandler):

        def log_message(self, fmt, *args):
            # Suppress default access log (we do our own)
            pass

        def _send_json(self, status: int, body: dict):
            payload = json.dumps(body, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin",  "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            if self.path == "/health":
                self._send_json(200, {
                    "status":             "ok",
                    "server":             "Chameleon Isaac Sim Mock (stdlib)",
                    "version":            "1.0.0",
                    "experiments_served": _stats["experiments_served"],
                })
            elif self.path == "/stats":
                self._send_json(200, _stats)
            else:
                self._send_json(404, {"error": f"Unknown path: {self.path}"})

        def do_POST(self):
            if self.path != "/api/chameleon/experiment":
                self._send_json(404, {"error": f"Unknown path: {self.path}"})
                return

            length = int(self.headers.get("Content-Length", 0))
            raw    = self.rfile.read(length)

            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as e:
                _stats["error_count"] += 1
                self._send_json(400, {"success": False, "error": f"Invalid JSON: {e}"})
                return

            object_id = body.get("object_id", "CHA-KIT-001")
            params    = body.get("params",    {})
            timestamp = body.get("timestamp", datetime.utcnow().isoformat())

            if not params:
                _stats["error_count"] += 1
                self._send_json(422, {"success": False, "error": "'params' is required"})
                return

            if verbose:
                print(
                    f"\n[{datetime.now().strftime('%H:%M:%S')}] POST /api/chameleon/experiment\n"
                    f"  object_id : {object_id}\n"
                    f"  params    : {json.dumps(params, separators=(',', ':'))}",
                    flush=True,
                )

            metrics, sim_time_ms = simulate_experiment(object_id, params)

            _stats["experiments_served"] += 1
            _stats["success_count"]      += 1
            _stats["total_sim_ms"]       += sim_time_ms
            _stats["last_request"]        = timestamp

            response = {
                "success":     True,
                "metrics":     metrics,
                "sim_time_ms": sim_time_ms,
                "object_id":   object_id,
            }

            if verbose:
                print(
                    f"  → composite={metrics['composite_score']:+.4f}  "
                    f"spill={metrics['spill_rate']:.4f}  ({sim_time_ms:.0f}ms)",
                    flush=True,
                )

            self._send_json(200, response)

    server = HTTPServer(("0.0.0.0", port), ChameleonHandler)

    print(f"\n{'─'*60}", flush=True)
    print(f"  Chameleon Isaac Sim Mock Server (stdlib)", flush=True)
    print(f"  Port    : {port}", flush=True)
    print(f"  Health  : http://localhost:{port}/health", flush=True)
    print(f"  Endpoint: http://localhost:{port}/api/chameleon/experiment", flush=True)
    print(f"  Verbose : {verbose}", flush=True)
    print(f"  Note    : install fastapi + uvicorn for richer server", flush=True)
    print(f"{'─'*60}\n", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.", flush=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Chameleon Isaac Sim Mock RPC Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 mock_server.py                    # port 8211, auto backend
  python3 mock_server.py --port 9000        # custom port
  python3 mock_server.py --verbose          # log every request + response
  python3 mock_server.py --stdlib           # force stdlib http.server
        """,
    )
    p.add_argument("--port",    type=int,  default=8211, help="Listen port (default: 8211)")
    p.add_argument("--verbose", action="store_true", default=False,
                   help="Log every request/response body")
    p.add_argument("--stdlib",  action="store_true", default=False,
                   help="Force stdlib http.server (skip FastAPI even if installed)")
    return p.parse_args()


def check_port(port: int) -> tuple[bool, int | None]:
    """
    Check if a port is already in use.
    Returns (is_free, pid_using_it | None).
    Uses stdlib only — no psutil needed.
    """
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True, None          # port is free
        except OSError:
            pass

    # Try to find the PID via lsof (macOS / Linux)
    try:
        import subprocess
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=3
        )
        pids = [int(p) for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
        return False, pids[0] if pids else None
    except Exception:
        return False, None


def kill_port(port: int) -> bool:
    """
    Kill whatever process is holding the given port.
    Returns True if killed successfully.
    """
    import subprocess, signal
    free, pid = check_port(port)
    if free:
        return True
    if pid is None:
        print(f"  ✗ Cannot identify process on port {port}. Kill manually.", flush=True)
        return False
    try:
        import os
        os.kill(pid, signal.SIGKILL)
        import time as _time
        _time.sleep(0.8)
        free2, _ = check_port(port)
        return free2
    except Exception as e:
        print(f"  ✗ Failed to kill PID {pid}: {e}", flush=True)
        return False


if __name__ == "__main__":
    args = parse_args()

    # ── Port conflict detection + auto-fix ────────────────────────────────────
    is_free, occupying_pid = check_port(args.port)

    if not is_free:
        print(f"\n  ⚠  Port {args.port} is already in use", end="", flush=True)
        if occupying_pid:
            print(f" (PID {occupying_pid})", end="", flush=True)
        print("", flush=True)
        print(f"  Attempting to free port {args.port}…", flush=True)

        killed = kill_port(args.port)
        if killed:
            print(f"  ✓ Port {args.port} freed — starting server.\n", flush=True)
        else:
            # Suggest next free port
            import socket
            for alt in range(args.port + 1, args.port + 20):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(("0.0.0.0", alt))
                        print(f"\n  ✗ Could not free port {args.port}.", flush=True)
                        print(f"  → Try:  python3 mock_server.py --port {alt}", flush=True)
                        print(f"  → Or:   kill -9 {occupying_pid or '<PID>'}", flush=True)
                        sys.exit(1)
                    except OSError:
                        continue

    if args.stdlib:
        run_stdlib_server(args.port, args.verbose)
    else:
        run_fastapi_server(args.port, args.verbose)
