#!/bin/bash
# Full pilot: 4 conditions × 9 tasks × 3 reps = 108 runs
# Skips existing results automatically (resume-safe)
# Estimated: ~6-8 hours total

set -e
cd /Volumes/Seagate/Multi-Agent
PYTHON=.venv/bin/python

echo "=========================================="
echo "  MyGO Experiment v2 — Full Pilot"
echo "  4 conditions × 9 tasks × 3 reps"
echo "=========================================="

for COND in single multi fixed_decompose adaptive_bridge; do
    echo ""
    echo "====== CONDITION: $COND ======"
    $PYTHON scripts/experiment_runner_v2.py --condition $COND --runs 3 || true
    echo "====== $COND DONE ======"
done

echo ""
echo "=========================================="
echo "  ALL CONDITIONS COMPLETE"
echo "=========================================="

$PYTHON scripts/analyze_experiment.py --figures --latex
