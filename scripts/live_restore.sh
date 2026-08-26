#!/bin/sh
set -eu

for name in TARGET_DATABASE_URL BACKUP_OBJECT_STORE_ENDPOINT BACKUP_OBJECT_STORE_ACCESS_KEY BACKUP_OBJECT_STORE_SECRET_KEY TARGET_OBJECT_STORE_ENDPOINT TARGET_OBJECT_STORE_ACCESS_KEY TARGET_OBJECT_STORE_SECRET_KEY BACKUP_BUCKET TARGET_BUCKET BACKUP_ENCRYPTION_KEY RESTORE_ID; do
  eval "value=\${$name:-}"
  [ -n "$value" ] || { echo "missing required environment: $name" >&2; exit 2; }
done
case "$BACKUP_ENCRYPTION_KEY" in *[!0-9a-fA-F]*|'') echo "BACKUP_ENCRYPTION_KEY must be hexadecimal" >&2; exit 2 ;; esac
[ "${#BACKUP_ENCRYPTION_KEY}" -eq 64 ] || { echo "BACKUP_ENCRYPTION_KEY must contain 32 bytes" >&2; exit 2; }
case "$RESTORE_ID/$BACKUP_BUCKET/$TARGET_BUCKET/${BACKUP_PREFIX:-}/${TARGET_PREFIX:-}" in
  *..*|*//*|*[!a-zA-Z0-9._/-]*) echo "restore identifier, bucket or prefix contains unsafe characters" >&2; exit 2 ;;
esac

work=${BACKUP_WORK_DIR:-/work}
mkdir -p "$work/objects"
umask 077
backup_prefix=${BACKUP_PREFIX:-zkdeal}; backup_prefix=${backup_prefix#/}; backup_prefix=${backup_prefix%/}
target_prefix=${TARGET_PREFIX#/}; target_prefix=${target_prefix%/}
remote="backup/$BACKUP_BUCKET/$backup_prefix/$RESTORE_ID"
export MC_CONFIG_DIR=${MC_CONFIG_DIR:-/tmp/mc}

derive_subkey() {
  purpose=$1
  openssl kdf -keylen 32 \
    -kdfopt digest:SHA256 \
    -kdfopt "hexkey:$BACKUP_ENCRYPTION_KEY" \
    -kdfopt hexsalt:7a6b6465616c2d6261636b75702d656e76656c6f70652d7631 \
    -kdfopt "info:$purpose" HKDF | tr -d ':\r\n'
}

encryption_key=$(derive_subkey zkdeal-backup-encryption-v1)
authentication_key=$(derive_subkey zkdeal-backup-authentication-v1)
[ "${#encryption_key}" -eq 64 ] && [ "${#authentication_key}" -eq 64 ] || {
  echo "backup key derivation failed" >&2; exit 2;
}
[ "$encryption_key" != "$authentication_key" ] || { echo "backup subkeys are not separated" >&2; exit 2; }

key_id=$(printf '%s' zkdeal-backup-key-id-v1 |
  openssl dgst -sha256 -mac HMAC -macopt "hexkey:$BACKUP_ENCRYPTION_KEY" |
  awk '{print $NF}' | cut -c1-32)
[ "${#key_id}" -eq 32 ] || { echo "backup key id derivation failed" >&2; exit 2; }

decrypt_file() {
  encrypted=$1
  plain=${encrypted%.enc}
  openssl dgst -sha256 -mac HMAC -macopt "hexkey:$authentication_key" "$encrypted" |
    awk '{print $NF}' >"$encrypted.hmac.actual"
  cmp "$encrypted.hmac" "$encrypted.hmac.actual" >/dev/null || { echo "backup HMAC mismatch: $encrypted" >&2; exit 1; }
  BACKUP_ENVELOPE_SUBKEY="$encryption_key" openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
    -pass env:BACKUP_ENVELOPE_SUBKEY -in "$encrypted" -out "$plain"
}

mc alias set backup "$BACKUP_OBJECT_STORE_ENDPOINT" "$BACKUP_OBJECT_STORE_ACCESS_KEY" "$BACKUP_OBJECT_STORE_SECRET_KEY" >/dev/null
mc alias set target "$TARGET_OBJECT_STORE_ENDPOINT" "$TARGET_OBJECT_STORE_ACCESS_KEY" "$TARGET_OBJECT_STORE_SECRET_KEY" >/dev/null

# Reject a wrong encryption key before any download or target write, with an
# error distinct from a tampered artifact.  This is a fast pre-check; the
# authenticated HMAC-then-decrypt below remains the authoritative gate.
mc cp --quiet "$remote/keyid" "$work/keyid"
expected_key_id=$(tr -d '\r\n' <"$work/keyid")
[ "$expected_key_id" = "$key_id" ] || { echo "backup encryption key id mismatch" >&2; exit 3; }

for file in database.dump.enc database.dump.enc.hmac objects.tar.enc objects.tar.enc.hmac source-objects.jsonl.enc source-objects.jsonl.enc.hmac manifest.json.enc manifest.json.enc.hmac; do
  mc cp --quiet "$remote/$file" "$work/$file"
done
for file in database.dump.enc objects.tar.enc source-objects.jsonl.enc manifest.json.enc; do decrypt_file "$work/$file"; done

database_sha=$(sha256sum "$work/database.dump" | cut -d' ' -f1)
objects_sha=$(sha256sum "$work/objects.tar" | cut -d' ' -f1)
inventory_sha=$(sha256sum "$work/source-objects.jsonl" | cut -d' ' -f1)
grep -Fq "\"databaseSha256\":\"$database_sha\"" "$work/manifest.json" || { echo "database manifest hash mismatch" >&2; exit 1; }
grep -Fq "\"objectsTarSha256\":\"$objects_sha\"" "$work/manifest.json" || { echo "object manifest hash mismatch" >&2; exit 1; }
grep -Fq "\"sourceInventorySha256\":\"$inventory_sha\"" "$work/manifest.json" || { echo "inventory manifest hash mismatch" >&2; exit 1; }
grep -Fq '"masterKdf":"hkdf-sha256"' "$work/manifest.json" || { echo "unsupported backup master KDF" >&2; exit 1; }
grep -Fq '"keySeparation":true' "$work/manifest.json" || { echo "backup does not attest key separation" >&2; exit 1; }
grep -Fq "\"keyId\":\"$key_id\"" "$work/manifest.json" || { echo "backup manifest key id mismatch" >&2; exit 1; }
pg_restore --list "$work/database.dump" >/dev/null
pg_restore --exit-on-error --no-owner --dbname="$TARGET_DATABASE_URL" "$work/database.dump"

/usr/local/bin/archive_guard.sh "$work/objects.tar"
tar -xof "$work/objects.tar" -C "$work/objects"
mc mb --ignore-existing "target/$TARGET_BUCKET" >/dev/null
target="target/$TARGET_BUCKET"
[ -z "$target_prefix" ] || target="$target/$target_prefix"
if [ "$(find "$work/objects" -type f | wc -l | tr -d ' ')" -gt 0 ]; then
  mc cp --quiet --recursive "$work/objects/" "$target/"
fi
printf '{"restoreId":"%s","targetBucket":"%s","databaseSha256":"%s","objectsTarSha256":"%s","hashesVerified":true}\n' "$RESTORE_ID" "$TARGET_BUCKET" "$database_sha" "$objects_sha"
