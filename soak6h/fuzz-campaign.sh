#!/bin/bash
set -u
FOUNDRY='ghcr.io/foundry-rs/foundry:v1.7.1@sha256:8347b728d5d393dac1c018691b36f506d23b9dcd78341d40ea0fcb11c3a19cdd'
OUT=/ephemeral/work/fuzz6h
mkdir -p "$OUT/rounds" "$OUT/counterexamples"
DEADLINE=$(( $(date -u +%s) + 21600 ))
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "FUZZ6H-START $START (deadline +6h)"
round=0
inv_pass=0; inv_fail=0; fuzz_pass=0; fuzz_fail=0

run_forge() {  # $1=label $2=seed $3.. = forge args
  label=$1; seed=$2; shift 2
  docker run --rm -v ~/zkdeal-rc/web3-protocol:/workspace -w /workspace/contracts \
    -e FOUNDRY_FUZZ_SEED="$seed" \
    -e FOUNDRY_FUZZ_RUNS="${FUZZ_RUNS:-20000}" \
    -e FOUNDRY_INVARIANT_RUNS="${INV_RUNS:-20000}" \
    -e FOUNDRY_INVARIANT_DEPTH="${INV_DEPTH:-500}" \
    -e FOUNDRY_INVARIANT_FAIL_ON_REVERT=false \
    --entrypoint /bin/sh "$FOUNDRY" -c "forge test $* -vv" \
    >"$OUT/rounds/${label}-${round}.log" 2>&1
  return $?
}

while [ "$(date -u +%s)" -lt "$DEADLINE" ]; do
  round=$((round+1))
  seed=$(( 1000 + round ))
  echo "=== round $round seed $seed $(date -u +%H:%M:%S)"

  if run_forge invariants "$seed" --match-path test/RoomManagerInvariants.t.sol; then
    inv_pass=$((inv_pass+1)); echo "  PASS invariants r$round"
  else
    inv_fail=$((inv_fail+1)); echo "  FAIL invariants r$round"
    cp "$OUT/rounds/invariants-${round}.log" "$OUT/counterexamples/invariants-r${round}-seed${seed}.log"
    grep -E 'invariant_|Counterexample|sequence|FAIL' "$OUT/rounds/invariants-${round}.log" | head -20
  fi
  [ "$(date -u +%s)" -lt "$DEADLINE" ] || break

  if run_forge fuzz "$seed" --match-test testFuzz; then
    fuzz_pass=$((fuzz_pass+1)); echo "  PASS fuzz r$round"
  else
    fuzz_fail=$((fuzz_fail+1)); echo "  FAIL fuzz r$round"
    cp "$OUT/rounds/fuzz-${round}.log" "$OUT/counterexamples/fuzz-r${round}-seed${seed}.log"
    grep -E 'testFuzz|Counterexample|args=|FAIL' "$OUT/rounds/fuzz-${round}.log" | head -20
  fi
done

echo "FUZZ6H-DONE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "rounds=$round invariants: pass=$inv_pass fail=$inv_fail | fuzz: pass=$fuzz_pass fail=$fuzz_fail"
echo "counterexamples: $(ls "$OUT/counterexamples" 2>/dev/null | wc -l)"
{
  echo "{\"kind\":\"zkdeal-6h-fuzz\",\"startedAt\":\"$START\",\"finishedAt\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
  echo " \"rounds\":$round,\"invariantPass\":$inv_pass,\"invariantFail\":$inv_fail,"
  echo " \"fuzzPass\":$fuzz_pass,\"fuzzFail\":$fuzz_fail,"
  echo " \"fuzzRunsPerTest\":${FUZZ_RUNS:-20000},\"invariantRuns\":${INV_RUNS:-20000},\"invariantDepth\":${INV_DEPTH:-500},"
  echo " \"counterexamples\":$(ls "$OUT/counterexamples" 2>/dev/null | wc -l)}"
} > "$OUT/summary.json"
cat "$OUT/summary.json"
