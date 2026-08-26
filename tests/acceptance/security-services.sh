#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir/../.."
project=zkdeal-security-acceptance
files="-f compose/compose.yaml -f compose/compose.secrets-dev.yaml -f compose/compose.signer.yaml -f compose/compose.security.test.yaml"
export OPENBAO_DEV_ROOT_TOKEN=local-openbao-root-token-only
export DEV_SIGNER_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
first_account=0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266
second_key=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
second_account=0x70997970c51812dc3a010c7d01b50e0d17dc79c8
marker=security-acceptance-secret-never-in-audit

cleanup() {
  docker compose --project-name "$project" --env-file .env.example $files --profile secrets-dev --profile signer down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
cleanup

docker compose --project-name "$project" --env-file .env.example $files --profile secrets-dev up -d --wait openbao
init_json=$(docker compose --project-name "$project" --env-file .env.example $files exec -T openbao \
  bao operator init -address=http://127.0.0.1:8200 -key-shares=1 -key-threshold=1 -format=json)
unseal_key=$(printf '%s' "$init_json" | python -c 'import json,sys; print(json.load(sys.stdin)["unseal_keys_b64"][0])')
root_token=$(printf '%s' "$init_json" | python -c 'import json,sys; print(json.load(sys.stdin)["root_token"])')
docker compose --project-name "$project" --env-file .env.example $files exec -T openbao \
  bao operator unseal -address=http://127.0.0.1:8200 "$unseal_key" >/dev/null
OPENBAO_DEV_ROOT_TOKEN=$root_token
echo "OpenBao started with declarative audit configuration"

bao() {
  docker compose --project-name "$project" --env-file .env.example $files exec -T \
    -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$OPENBAO_DEV_ROOT_TOKEN" openbao bao "$@"
}
bao secrets enable -path=zkdeal kv-v2 >/dev/null
printf 'path "zkdeal/data/coordinator/*" { capabilities = ["read"] }\n' | bao policy write coordinator - >/dev/null
echo "OpenBao scoped policy installed"
bao kv put zkdeal/coordinator/runtime signing_reference="$marker" >/dev/null
bao kv put zkdeal/withdrawal/runtime signing_reference=must-be-denied >/dev/null
echo "OpenBao acceptance secrets written"
token_one=$(bao token create -policy=coordinator -ttl=10m -field=token)
read_one=$(docker compose --project-name "$project" --env-file .env.example $files exec -T \
  -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$token_one" openbao \
  bao kv get -field=signing_reference zkdeal/coordinator/runtime)
[ "$read_one" = "$marker" ] || { echo "OpenBao scoped read failed" >&2; exit 1; }
if docker compose --project-name "$project" --env-file .env.example $files exec -T \
  -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$token_one" openbao \
  bao kv get zkdeal/withdrawal/runtime >/dev/null 2>&1; then
  echo "OpenBao role escaped its policy" >&2; exit 1
fi
token_two=$(bao token create -policy=coordinator -ttl=10m -field=token)
bao token revoke "$token_one" >/dev/null
if docker compose --project-name "$project" --env-file .env.example $files exec -T \
  -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$token_one" openbao \
  bao kv get zkdeal/coordinator/runtime >/dev/null 2>&1; then
  echo "revoked OpenBao token remained usable" >&2; exit 1
fi
read_two=$(docker compose --project-name "$project" --env-file .env.example $files exec -T \
  -e BAO_ADDR=http://127.0.0.1:8200 -e BAO_TOKEN="$token_two" openbao \
  bao kv get -field=signing_reference zkdeal/coordinator/runtime)
[ "$read_two" = "$marker" ] || { echo "rotated OpenBao token failed" >&2; exit 1; }
echo "OpenBao policy denial and token rotation verified"
docker compose --project-name "$project" --env-file .env.example $files exec -T openbao sh -ec \
  'test -s /var/log/openbao/audit.log || { echo "OpenBao audit log is empty" >&2; exit 1; };
   if grep -Fq "security-acceptance-secret-never-in-audit" /var/log/openbao/audit.log; then
     echo "OpenBao audit log exposed a raw secret" >&2; exit 1;
   fi'
echo "OpenBao HMAC audit verified"

docker compose --project-name "$project" --env-file .env.example $files --profile signer up -d --wait web3signer-health
accounts=$(docker compose --project-name "$project" --env-file .env.example $files exec -T web3signer-health \
  curl -fsS -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_accounts","params":[],"id":1}' http://web3signer:9000/)
printf '%s' "$accounts" | grep -qi "$first_account" || { echo "Web3Signer did not load the scoped key" >&2; exit 1; }
signature=$(docker compose --project-name "$project" --env-file .env.example $files exec -T web3signer-health \
  curl -fsS -H 'Content-Type: application/json' --data "{\"jsonrpc\":\"2.0\",\"method\":\"eth_sign\",\"params\":[\"$first_account\",\"0x2eadbe1f\"],\"id\":2}" http://web3signer:9000/)
printf '%s' "$signature" | grep -q '"result":"0x' || { echo "Web3Signer signing failed" >&2; exit 1; }
unsupported=$(docker compose --project-name "$project" --env-file .env.example $files exec -T web3signer-health \
  curl -sS -w '\nHTTP:%{http_code}' -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"personal_unlockAccount","params":[],"id":20}' http://web3signer:9000/)
if printf '%s' "$unsupported" | grep -q '"result"'; then
  echo "Web3Signer accepted an unauthorized method" >&2; exit 1
fi
unsupported_status=$(printf '%s\n' "$unsupported" | sed -n 's/^HTTP://p')
case "$unsupported_status" in
  4*|5*) ;;
  2*) printf '%s' "$unsupported" | grep -q '"error"' || { echo "Web3Signer did not explicitly deny an unauthorized method" >&2; exit 1; } ;;
  *) echo "Web3Signer returned an invalid denial status: $unsupported_status" >&2; exit 1 ;;
esac
admin_status=$(docker compose --project-name "$project" --env-file .env.example $files exec -T web3signer-health \
  curl -sS -o /dev/null -w '%{http_code}' http://web3signer:9000/admin)
case "$admin_status" in 404|405) ;; *) echo "Web3Signer exposed an unauthorized path: HTTP $admin_status" >&2; exit 1 ;; esac
signer_container=$(docker compose --project-name "$project" --env-file .env.example $files ps -q web3signer)
published=$(docker container inspect --format '{{json .HostConfig.PortBindings}}' "$signer_container")
case "$published" in null|'{}') ;; *) echo "Web3Signer API was published on the host: $published" >&2; exit 1 ;; esac
echo "Web3Signer startup and scoped signing verified"

export DEV_SIGNER_PRIVATE_KEY=$second_key
docker compose --project-name "$project" --env-file .env.example $files --profile signer run --rm web3signer-key-init >/dev/null
docker compose --project-name "$project" --env-file .env.example $files restart web3signer >/dev/null
attempts=0
until accounts=$(docker compose --project-name "$project" --env-file .env.example $files exec -T web3signer-health \
  curl -fsS -H 'Content-Type: application/json' --data '{"jsonrpc":"2.0","method":"eth_accounts","params":[],"id":3}' http://web3signer:9000/ 2>/dev/null) \
  && printf '%s' "$accounts" | grep -qi "$second_account"; do
  attempts=$((attempts + 1)); [ "$attempts" -lt 60 ] || { echo "Web3Signer rotation did not become ready" >&2; exit 1; }; sleep 1
done
if printf '%s' "$accounts" | grep -qi "$first_account"; then echo "rotated-out signer account remains present" >&2; exit 1; fi
denied=$(docker compose --project-name "$project" --env-file .env.example $files exec -T web3signer-health \
  curl -fsS -H 'Content-Type: application/json' --data "{\"jsonrpc\":\"2.0\",\"method\":\"eth_sign\",\"params\":[\"$first_account\",\"0x2eadbe1f\"],\"id\":4}" http://web3signer:9000/)
printf '%s' "$denied" | grep -q '"error"' || { echo "retired signer account was not denied" >&2; exit 1; }

echo "security services acceptance passed: OpenBao audit/policy/rotation and Web3Signer scope/rotation"
