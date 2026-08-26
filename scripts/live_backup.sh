#!/bin/sh
set -eu

for name in DATABASE_URL SOURCE_OBJECT_STORE_ENDPOINT SOURCE_OBJECT_STORE_ACCESS_KEY SOURCE_OBJECT_STORE_SECRET_KEY BACKUP_OBJECT_STORE_ENDPOINT BACKUP_OBJECT_STORE_ACCESS_KEY BACKUP_OBJECT_STORE_SECRET_KEY SOURCE_BUCKET BACKUP_BUCKET BACKUP_ENCRYPTION_KEY; do
  eval "value=\${$name:-}"
  [ -n "$value" ] || { echo "missing required environment: $name" >&2; exit 2; }
done
case "$BACKUP_ENCRYPTION_KEY" in *[!0-9a-fA-F]*|'') echo "BACKUP_ENCRYPTION_KEY must be hexadecimal" >&2; exit 2 ;; esac
[ "${#BACKUP_ENCRYPTION_KEY}" -eq 64 ] || { echo "BACKUP_ENCRYPTION_KEY must contain 32 bytes" >&2; exit 2; }
case "$SOURCE_BUCKET/$BACKUP_BUCKET/${SOURCE_PREFIX:-}/${BACKUP_PREFIX:-}" in
  *..*|*//*|*[!a-zA-Z0-9._/-]*) echo "bucket or prefix contains unsafe characters" >&2; exit 2 ;;
esac

work=${BACKUP_WORK_DIR:-/work}
mkdir -p "$work/objects"
umask 077
backup_id=${BACKUP_ID:-$(date -u +%Y%m%dT%H%M%SZ)-${HOSTNAME:-job}}
case "$backup_id" in *[!a-zA-Z0-9._-]*) echo "invalid BACKUP_ID" >&2; exit 2 ;; esac
source_prefix=${SOURCE_PREFIX#/}; source_prefix=${source_prefix%/}
backup_prefix=${BACKUP_PREFIX:-zkdeal}; backup_prefix=${backup_prefix#/}; backup_prefix=${backup_prefix%/}
remote="backup/$BACKUP_BUCKET/$backup_prefix/$backup_id"
export MC_CONFIG_DIR=${MC_CONFIG_DIR:-/tmp/mc}

derive_subkey() {
  purpose=$1
  openssl kdf -keylen 32 \
    -kdfopt digest:SHA256 \
    -kdfopt "hexkey:$BACKUP_ENCRYPTION_KEY" \
    -kdfopt hexsalt:7a6b6465616c2d6261636b75702d656e76656c6f70652d7631 \
    -kdfopt "info:$purpose" HKDF | tr -d ':\r\n'
}

# BACKUP_ENCRYPTION_KEY is a random master key.  HKDF gives encryption and
# authentication independent subkeys; the master is never passed to either
# primitive directly.
encryption_key=$(derive_subkey zkdeal-backup-encryption-v1)
authentication_key=$(derive_subkey zkdeal-backup-authentication-v1)
[ "${#encryption_key}" -eq 64 ] && [ "${#authentication_key}" -eq 64 ] || {
  echo "backup key derivation failed" >&2; exit 2;
}
[ "$encryption_key" != "$authentication_key" ] || { echo "backup subkeys are not separated" >&2; exit 2; }

# Non-secret key fingerprint.  Revealing an HMAC of a fixed label under the
# master key does not disclose the key, but lets a restore reject a wrong key
# distinctly and before any target write.
key_id=$(printf '%s' zkdeal-backup-key-id-v1 |
  openssl dgst -sha256 -mac HMAC -macopt "hexkey:$BACKUP_ENCRYPTION_KEY" |
  awk '{print $NF}' | cut -c1-32)
[ "${#key_id}" -eq 32 ] || { echo "backup key id derivation failed" >&2; exit 2; }

encrypt_file() {
  plain=$1
  encrypted="$plain.enc"
  BACKUP_ENVELOPE_SUBKEY="$encryption_key" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
    -pass env:BACKUP_ENVELOPE_SUBKEY -in "$plain" -out "$encrypted"
  openssl dgst -sha256 -mac HMAC -macopt "hexkey:$authentication_key" "$encrypted" |
    awk '{print $NF}' >"$encrypted.hmac"
  rm -f "$plain"
}

mc alias set source "$SOURCE_OBJECT_STORE_ENDPOINT" "$SOURCE_OBJECT_STORE_ACCESS_KEY" "$SOURCE_OBJECT_STORE_SECRET_KEY" >/dev/null
mc alias set backup "$BACKUP_OBJECT_STORE_ENDPOINT" "$BACKUP_OBJECT_STORE_ACCESS_KEY" "$BACKUP_OBJECT_STORE_SECRET_KEY" >/dev/null
mc mb --ignore-existing "backup/$BACKUP_BUCKET" >/dev/null

partial="$work/database.dump.partial"
dump="$work/database.dump"
pg_dump --dbname="$DATABASE_URL" --format=custom --file="$partial"
pg_restore --list "$partial" >/dev/null
mv "$partial" "$dump"
database_sha=$(sha256sum "$dump" | cut -d' ' -f1)

source_path="source/$SOURCE_BUCKET"
[ -z "$source_prefix" ] || source_path="$source_path/$source_prefix"
mc ls --recursive --json "$source_path" >"$work/source-objects.jsonl"
if [ "$(wc -l <"$work/source-objects.jsonl" | tr -d ' ')" -gt 0 ]; then
  mc cp --quiet --recursive "$source_path/" "$work/objects/"
fi
tar -cf "$work/objects.tar" -C "$work/objects" .
objects_sha=$(sha256sum "$work/objects.tar" | cut -d' ' -f1)
inventory_sha=$(sha256sum "$work/source-objects.jsonl" | cut -d' ' -f1)
cat >"$work/manifest.json" <<EOF
{"schemaVersion":2,"backupId":"$backup_id","keyId":"$key_id","databaseSha256":"$database_sha","objectsTarSha256":"$objects_sha","sourceInventorySha256":"$inventory_sha","sourceBucket":"$SOURCE_BUCKET","sourcePrefix":"$source_prefix","envelope":{"cipher":"aes-256-cbc","passwordKdf":"pbkdf2-sha256","iterations":200000,"masterKdf":"hkdf-sha256","keyId":"$key_id","keySeparation":true,"integrity":"hmac-sha256-then-decrypt"}}
EOF

encrypt_file "$work/database.dump"
encrypt_file "$work/objects.tar"
encrypt_file "$work/source-objects.jsonl"
encrypt_file "$work/manifest.json"
# The key fingerprint is written in the clear so a restore can reject a wrong
# key before it downloads or decrypts anything; it is only a fast pre-check and
# never replaces the authenticated HMAC-then-decrypt verification.
printf '%s\n' "$key_id" >"$work/keyid"
for file in database.dump.enc database.dump.enc.hmac objects.tar.enc objects.tar.enc.hmac source-objects.jsonl.enc source-objects.jsonl.enc.hmac manifest.json.enc manifest.json.enc.hmac keyid; do
  mc cp --quiet "$work/$file" "$remote/$file"
done

retention=${BACKUP_RETENTION_DAYS:-365}
case "$retention" in *[!0-9]*|'') echo "BACKUP_RETENTION_DAYS must be an integer" >&2; exit 2 ;; esac
[ "$retention" -ge 1 ] || { echo "BACKUP_RETENTION_DAYS must be positive" >&2; exit 2; }
mc rm --quiet --recursive --force --older-than "${retention}d" "backup/$BACKUP_BUCKET/$backup_prefix/" || true
printf '{"backupId":"%s","keyId":"%s","databaseSha256":"%s","objectsTarSha256":"%s","encrypted":true,"masterKdf":"hkdf-sha256","keySeparation":true,"integrity":"hmac-sha256-then-decrypt"}\n' "$backup_id" "$key_id" "$database_sha" "$objects_sha"
