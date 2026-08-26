#!/bin/sh
set -eu

output=/tmp/trusted-proxies.conf
: >"$output"
if [ "${REQUIRE_TRUSTED_PROXIES:-0}" != 1 ]; then
  exit 0
fi

cidrs=$(printf '%s' "${TRUSTED_PROXY_CIDRS:-}" | tr ',' ' ')
[ -n "$cidrs" ] || { echo "TRUSTED_PROXY_CIDRS is required for TLS/public mode" >&2; exit 1; }
count=0
for cidr in $cidrs; do
  case "$cidr" in
    0.0.0.0/0|::/0|private_ranges) echo "unbounded trusted proxy range is forbidden" >&2; exit 1 ;;
    *[!0-9a-fA-F.:/]*|*//*|/*|*/) echo "invalid trusted proxy CIDR: $cidr" >&2; exit 1 ;;
  esac
  case "$cidr" in */[0-9]* ) : ;; * ) echo "trusted proxy must be an explicit CIDR: $cidr" >&2; exit 1 ;; esac
  printf 'set_real_ip_from %s;\n' "$cidr" >>"$output"
  count=$((count + 1))
  [ "$count" -le 32 ] || { echo "at most 32 trusted proxy CIDRs are accepted" >&2; exit 1; }
done
cat >>"$output" <<'EOF'
real_ip_header X-Forwarded-For;
real_ip_recursive on;
EOF
