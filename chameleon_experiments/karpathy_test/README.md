# Chameleon Karpathy Protocol Test
## Autonomous Self-Improvement Loop for Physical Device Manifests

Based on: https://github.com/karpathy/autoresearch (Andrej Karpathy, ~March 2026)

---

## Quick Start

```bash
# 1. Navigate to this folder
cd Desktop/Chameleon/chameleon_experiments/karpathy_test

# 2. Install optional dependencies
pip install -r requirements.txt

# 3. Run the experiment
python chameleon_karpathy_test.py
```

## Controls (during runtime)
| Key | Action |
|-----|--------|
| `p` | Pause / Resume |
| `s` | Stop gracefully (saves state) |
| `r` | Restart from last committed params |
| `c` | Cancel and revert to baseline |

## Output Files
| File | Description |
|------|-------------|
| `results/results.tsv` | Full experiment history (tab-separated) |
| `commits/commit_log.json` | All committed improvements |
| `plots/improvement_plot.png` | Metric improvement chart |
| `manifests/stovetop_kettle_manifest.json` | Live-updated best manifest |

## Connecting to Isaac Sim (future)
Replace the `mock_experiment()` function body with an HTTP call to your Isaac Sim
RPC server at `http://localhost:8211/api/chameleon/experiment`.
