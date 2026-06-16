#!/usr/bin/env bash
# v10 vs v14 benchmark on the 7 held-out projects (IoU 0.3 position-match).
set -u
DATA="/c/Users/TriuneTakeoff/Downloads/data hvac"
OUT="benchmark_output/v14_holdout"
V14="models/hvac_yolov8s_v14.pt"
mkdir -p "$OUT"

run() {
  local name="$1" plan="$2" truth="$3"
  echo ""
  echo "############################################################"
  echo "# $name"
  echo "############################################################"
  if [ -n "$truth" ]; then
    python benchmark_v10_vs_v11.py --pdf "$plan" --truth "$truth" \
      --v11 "$V14" --output-dir "$OUT" 2>&1
  else
    python benchmark_v10_vs_v11.py --pdf "$plan" \
      --v11 "$V14" --output-dir "$OUT" 2>&1
  fi
  echo "# DONE: $name (exit $?)"
}

run "Extra Space Storage Mesa" \
  "$DATA/7-19 Extra Space Storage Mesa/Plans_Specs/Mech.pdf" \
  "$DATA/7-19 Extra Space Storage Mesa/Completed Takeoff/Takeoff_Extra Space Storage Mesa.pdf"

run "Hippo Vet Clinic" \
  "$DATA/11-11 Hippo Vet Clinic University Tempe/Plans_Specs/mechanical.pdf" \
  "$DATA/11-11 Hippo Vet Clinic University Tempe/Completed Takeoff/Takeoff_Hippo Vet Clinic University Tempe.pdf"

run "HLPUSD District Admin" \
  "$DATA/5.11.26 HLPUSD - District Admin Office HVAC Upgrade/Plans_Specs/5.12.26 HLPUSD - District Admin Office HVAC Upgrade.pdf" \
  "$DATA/5.11.26 HLPUSD - District Admin Office HVAC Upgrade/Completed Takeoff/Takeoff_HLPUSD - District Admin Office HVAC Upgrade.pdf"

run "Humble Bistro Adeline" \
  "$DATA/11-01 Humble Bistro Adeline/Plans_Specs/mechanical.pdf" \
  "$DATA/11-01 Humble Bistro Adeline/Completed Takeoff/TAKEOFF_Humble Bistro Adeline.pdf"

run "Pacific Palisades Rec Center" \
  "$DATA/6.1.26 Pacific Palisades Recreation Center & Park/Plans_Specs/6.2.26 Pacific Palisades Recreation Center & Park.pdf" \
  "$DATA/6.1.26 Pacific Palisades Recreation Center & Park/Completed Takeoff/Takeoff_Pacific Palisades Recreation Center & Park.pdf"

run "St Rose Catholic Church" \
  "$DATA/7-15 St Rose Philippine Duchesne Catholic Church/Plans_Specs/mech with rcp.pdf" \
  "$DATA/7-15 St Rose Philippine Duchesne Catholic Church/Completed Takeoff/Takeoff_St Rose Philippine Duchesne Catholic Church with rcp.pdf"

# Surprise: only xlsx truth, no Bluebeam PDF -> count-only comparison
run "Surprise Self Storage (count-only)" \
  "$DATA/7-11 Surprise Self Storage/Plans_Specs/2022-04-07 - Surprise Resubmittal.pdf" \
  ""

echo ""
echo "ALL PROJECTS COMPLETE"
