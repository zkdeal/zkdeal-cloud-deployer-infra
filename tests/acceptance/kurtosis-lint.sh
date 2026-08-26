#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."

version=$(kurtosis version 2>&1 | tr '\n' ' ')
for package in local failover soak acceptance-matrix; do
  kurtosis lint "kurtosis/$package"
done

printf '{"kurtosisLint":"passed","packages":4,"cli":"%s","scenarioExecution":"blocked-until-digest-pinned-owner-runner"}\n' "$version"
