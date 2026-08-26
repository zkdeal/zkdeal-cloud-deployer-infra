#!/bin/bash
set -u
CAND=b3daef1b4c5b0d687ddcb1ceb4a586f5b4e5839e11120717f594a7e326409301
TAG=20260823-5090rc
cd ~/zkdeal-rc/prover-node
OUT=~/multiarch.digests
: > "$OUT"
echo "MULTIARCH-START $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# sm120 is already built, published and GPU-validated on this node.
for ARCH in 86 89 90; do
  echo "=== sm${ARCH} toolchain $(date -u +%H:%M:%S)"
  docker build --target toolchain -f zkvm/docker/risc0-cuda.Dockerfile \
    --build-arg CUDA_ARCH=${ARCH} \
    -t localhost:5000/zkdeal-risc0-toolchain:sm${ARCH} . \
    >/tmp/ma-tc-${ARCH}.log 2>&1 || { echo "FAIL toolchain sm${ARCH}"; tail -8 /tmp/ma-tc-${ARCH}.log; continue; }
  echo "PASS toolchain sm${ARCH}"

  echo "=== sm${ARCH} runtime $(date -u +%H:%M:%S)"
  docker build --target runtime -f zkvm/docker/risc0-cuda.Dockerfile \
    --build-arg CUDA_ARCH=${ARCH} --build-arg SOURCE_MANIFEST_SHA256=${CAND} \
    -t zkdeal/prover-cuda:sm${ARCH}-${TAG} . \
    >/tmp/ma-rt-${ARCH}.log 2>&1 || { echo "FAIL runtime sm${ARCH}"; tail -12 /tmp/ma-rt-${ARCH}.log; continue; }
  echo "PASS runtime sm${ARCH}"

  # licence contract check (same as every other published image)
  lic=$(docker run --rm --entrypoint "" zkdeal/prover-cuda:sm${ARCH}-${TAG} sh -c 'head -c 27 /zkdeal-BUSL-1.1-LICENSE' 2>/dev/null)
  lbl=$(docker inspect --format '{{index .Config.Labels "org.opencontainers.image.licenses"}}' zkdeal/prover-cuda:sm${ARCH}-${TAG} 2>/dev/null)
  [ "$lic" = "Business Source License 1.1" ] && [ "$lbl" = "BUSL-1.1" ] \
    && echo "PASS licence sm${ARCH}" || echo "FAIL licence sm${ARCH} ('$lic' / '$lbl')"

  docker tag zkdeal/prover-cuda:sm${ARCH}-${TAG} zkdeal/prover-cuda:sm${ARCH}
  if docker push zkdeal/prover-cuda:sm${ARCH}-${TAG} >/tmp/ma-p1-${ARCH}.log 2>&1; then
    d=$(grep -oE 'sha256:[0-9a-f]{64}' /tmp/ma-p1-${ARCH}.log | tail -1)
    echo "PUSHED zkdeal/prover-cuda:sm${ARCH}-${TAG}@${d}" | tee -a "$OUT"
  else echo "FAIL push sm${ARCH}-${TAG}"; tail -3 /tmp/ma-p1-${ARCH}.log; fi
  if docker push zkdeal/prover-cuda:sm${ARCH} >/tmp/ma-p2-${ARCH}.log 2>&1; then
    d=$(grep -oE 'sha256:[0-9a-f]{64}' /tmp/ma-p2-${ARCH}.log | tail -1)
    echo "PUSHED zkdeal/prover-cuda:sm${ARCH}@${d}" | tee -a "$OUT"
  else echo "FAIL push sm${ARCH}"; tail -3 /tmp/ma-p2-${ARCH}.log; fi
  docker image prune -f >/dev/null 2>&1
done

# Refresh the plain sm120 tag from the already-published current build, and
# keep the historical convention that :latest tracks sm89 (Ada consumer GPUs).
. ~/sm120-digests.env 2>/dev/null || true
if [ -n "${RUNTIME:-}" ]; then
  docker pull "$RUNTIME" >/dev/null 2>&1
  docker tag "$RUNTIME" zkdeal/prover-cuda:sm120
  docker push zkdeal/prover-cuda:sm120 >/tmp/ma-p120.log 2>&1 \
    && echo "PUSHED zkdeal/prover-cuda:sm120@$(grep -oE 'sha256:[0-9a-f]{64}' /tmp/ma-p120.log | tail -1)" | tee -a "$OUT" \
    || echo "FAIL push sm120"
fi
if docker image inspect zkdeal/prover-cuda:sm89-${TAG} >/dev/null 2>&1; then
  docker tag zkdeal/prover-cuda:sm89-${TAG} zkdeal/prover-cuda:latest
  docker push zkdeal/prover-cuda:latest >/tmp/ma-plat.log 2>&1 \
    && echo "PUSHED zkdeal/prover-cuda:latest(=sm89)@$(grep -oE 'sha256:[0-9a-f]{64}' /tmp/ma-plat.log | tail -1)" | tee -a "$OUT" \
    || echo "FAIL push latest"
fi

echo "MULTIARCH-DONE $(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat "$OUT"
df -h /ephemeral | tail -1
