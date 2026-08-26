#!/bin/bash
set -u
cd ~/zkdeal-rc
export SOAK6H_DIR=/ephemeral/work/soak6h
export SOAK6H_WORK=$SOAK6H_DIR
mkdir -p $SOAK6H_DIR
LOGD=$HOME/soak6h-logs; mkdir -p $LOGD
S=cloud-deployer-infra/soak6h
ph(){ echo "===== PHASE $* $(date -u +%H:%M:%S) ====="; }
die(){ echo "SOAK6H-ABORT: $*"; exit 1; }

ph 1 relax-duration-floor
bash $S/relax-duration-floor.sh --check >$LOGD/p1chk.log 2>&1; if grep -qi PATCHED $LOGD/p1chk.log; then echo "PASS floor-patch (already applied)"; else bash $S/relax-duration-floor.sh >$LOGD/p1.log 2>&1 || die "floor patch failed"; echo "PASS floor-patch"; fi
true

sudo chown -R $(id -u):$(id -g) $SOAK6H_DIR 2>/dev/null || true
ph 2 stack-up
(bash $S/stack-up.sh >$LOGD/p2-stackup.log 2>&1; echo "EXIT=$?" >>$LOGD/p2-stackup.log)
grep -q 'EXIT=0' $LOGD/p2-stackup.log && echo "PASS stack-up" || { echo "FAIL stack-up"; tail -25 $LOGD/p2-stackup.log; die "stack did not come up"; }
[ -f $SOAK6H_DIR/endpoints.env ] || [ -f ~/soak6h/endpoints.env ] || die "no endpoints.env produced"
[ -f $SOAK6H_DIR/endpoints.env ] || cp ~/soak6h/endpoints.env $SOAK6H_DIR/endpoints.env
set -a; . $SOAK6H_DIR/endpoints.env; set +a
echo "endpoints loaded: $(grep -c '=' $SOAK6H_DIR/endpoints.env) vars"

ph 3 image-digests
# An image tagged under both its local build name and its published name carries
# two RepoDigests. Index 0 is whichever Docker recorded first, so taking it can
# write a reference nobody can resolve: zkdeal-coordinator@sha256:... points at
# docker.io/library/zkdeal-coordinator, not at the zkdeal organisation. The
# content digest is right either way, but a manifest whose reference cannot be
# pulled is not provenance. Select the entry for the repository being recorded.
digest(){
  _repo="${1%%:*}"
  docker image inspect "$1" --format '{{range .RepoDigests}}{{println .}}{{end}}' 2>/dev/null \
    | grep -E "^${_repo}@sha256:[0-9a-f]{64}$" | head -1
}
T=20260823-5090rc
cat > $SOAK6H_DIR/images.json <<EOF
{
  "coordinator": "$(digest zkdeal/coordinator:$T)",
  "indexer": "$(digest zkdeal/coordinator:$T)",
  "reconciler": "$(digest zkdeal/coordinator:$T)",
  "headless": "$(digest zkdeal/headless-room-node:$T)",
  "prover": "$(grep '^RUNTIME=' ~/sm120-digests.env | cut -d= -f2-)",
  "ownerAcceptanceRunner": "$(digest zkdeal/acceptance-runner:$T)"
}
EOF
grep -q 'sha256:' $SOAK6H_DIR/images.json && echo "PASS image-digests" || { cat $SOAK6H_DIR/images.json; die "image digests missing"; }

GENESIS_HASH=$(docker run --rm --network zksoak6h_soaknet curlimages/curl@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922 -s -X POST -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByNumber","params":["0x0",false]}' http://rpc-a:8545 | grep -oE '"hash":"0x[0-9a-f]{64}"' | head -1 | grep -oE '0x[0-9a-f]{64}')
[ -n "$GENESIS_HASH" ] || die "could not resolve the genesis hash"
echo "genesis: $GENESIS_HASH"
ph 4 seed-manifest
python3 $S/make-manifest.py --out $SOAK6H_DIR/manifest.seed.json \
  --images-file $SOAK6H_DIR/images.json --chain-id ${SOAK_CHAIN_ID:-31337} \
  --genesis-hash "$GENESIS_HASH" --rpc-endpoint "$L1_RPC_A" --rpc-endpoint "$L1_RPC_B" \
  --expected-usage-units 0 --expected-charges-wei 0 \
  --max-deadline-misses 12 --test-binding-fallback --force >$LOGD/p4.log 2>&1 \
  && echo "PASS seed-manifest" || { tail -12 $LOGD/p4.log; die "seed manifest failed"; }

ph 5 calibration
# Endpoints are docker-network aliases; calibration must run inside the
# stack network rather than on the host.
(docker run --rm --network zksoak6h_soaknet --env-file $SOAK6H_DIR/endpoints.env \
  -v $SOAK6H_DIR:$SOAK6H_DIR -v $HOME/zkdeal-rc:/repo:ro -w /repo \
  python:3.13-alpine python3 cloud-deployer-infra/owner-soak-driver/calibrate_expected.py \
  --manifest $SOAK6H_DIR/manifest.seed.json --work-dir $SOAK6H_DIR/calibrate \
  > $SOAK6H_DIR/calibration.json 2>$LOGD/p5.err; echo "EXIT=$?" >$LOGD/p5.rc)
grep -q 'EXIT=0' $LOGD/p5.rc && echo "PASS calibration" || { echo "FAIL calibration"; tail -15 $LOGD/p5.err; die "calibration failed"; }
head -c 200 $SOAK6H_DIR/calibration.json; echo

sudo chown -R $(id -u):$(id -g) $SOAK6H_DIR 2>/dev/null || true
ph 6 fresh-stack-redeploy
bash $S/stack-down.sh --keep-volumes >$LOGD/p6-down.log 2>&1
(bash $S/stack-up.sh >$LOGD/p6-up.log 2>&1; echo "EXIT=$?" >>$LOGD/p6-up.log)
grep -q 'EXIT=0' $LOGD/p6-up.log && echo "PASS fresh-stack" || { echo "FAIL fresh-stack"; tail -20 $LOGD/p6-up.log; die "redeploy failed"; }
[ -f $SOAK6H_DIR/endpoints.env ] || cp ~/soak6h/endpoints.env $SOAK6H_DIR/endpoints.env
set -a; . $SOAK6H_DIR/endpoints.env; set +a

ph 7 real-manifest
python3 $S/make-manifest.py --out $SOAK6H_DIR/soak-manifest.json \
  --provenance-out $SOAK6H_DIR/soak-manifest.provenance.json \
  --env-out $SOAK6H_DIR/soak-manifest.env \
  --images-file $SOAK6H_DIR/images.json --chain-id ${SOAK_CHAIN_ID:-31337} \
  --genesis-hash "$GENESIS_HASH" --rpc-endpoint "$L1_RPC_A" --rpc-endpoint "$L1_RPC_B" \
  --expected-from-calibration $SOAK6H_DIR/calibration.json \
  --max-deadline-misses 12 --test-binding-fallback --force >$LOGD/p7.log 2>&1 \
  && echo "PASS real-manifest" || { tail -12 $LOGD/p7.log; die "manifest failed"; }

ph 8 launch-soak
export SOAK_MANIFEST_HOST=$SOAK6H_DIR/soak-manifest.json
(bash $S/run-soak.sh >$LOGD/p8-launch.log 2>&1; echo "EXIT=$?" >>$LOGD/p8-launch.log)
grep -q 'EXIT=0' $LOGD/p8-launch.log && echo "PASS soak-launched" || { echo "FAIL soak-launch"; tail -20 $LOGD/p8-launch.log; die "soak launch failed"; }
grep -iE 'start|expected completion|timestamp' $LOGD/p8-launch.log | head -5
echo "SOAK6H-STARTED $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "===== ORCHESTRATE DONE $(date -u +%H:%M:%S) ====="
