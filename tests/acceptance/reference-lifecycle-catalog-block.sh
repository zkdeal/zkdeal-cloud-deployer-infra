#!/bin/sh
set -eu

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT INT TERM

if python scripts/render_reference_docs.py \
  --output-dir "$work/generated" >"$work/stdout" 2>"$work/stderr"
then
  echo "ERROR: lifecycle-incomplete owner catalogs generated release references" >&2
  exit 1
fi

grep -F "roomPool.events=['NodeDrainStarted', 'NodeRetired']" "$work/stderr" >/dev/null
grep -F "roomPool.calls=['beginNodeDrain', 'retireNode']" "$work/stderr" >/dev/null
cat "$work/stderr"
printf '%s\n' '{"referenceLifecycleCatalogPolicy":"passed","releaseBlocked":true,"generatedFilesWritten":false,"missingEvents":["NodeDrainStarted","NodeRetired"],"missingCalls":["beginNodeDrain","retireNode"]}'
