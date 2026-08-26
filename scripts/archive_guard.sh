#!/bin/sh
set -eu

validate_name() {
  member=$1
  case "$member" in
    ''|/*|\\*|[a-zA-Z]:*|..|../*|*/../*|*/..|*\\*|*//*|*[!a-zA-Z0-9._/-]*)
      echo "unsafe archive member: $member" >&2
      return 1
      ;;
  esac
}

if [ "${1:-}" = --check-name ]; then
  [ "$#" -eq 2 ] || { echo "usage: archive_guard.sh --check-name MEMBER" >&2; exit 2; }
  validate_name "$2"
  exit 0
fi

[ "$#" -eq 1 ] || { echo "usage: archive_guard.sh ARCHIVE" >&2; exit 2; }
archive=$1
[ -f "$archive" ] || { echo "archive does not exist: $archive" >&2; exit 2; }

umask 077
scratch=${TMPDIR:-/tmp}/archive-guard.$$
mkdir "$scratch" || { echo "cannot create archive guard scratch directory" >&2; exit 2; }
trap 'rm -rf "$scratch"' EXIT INT TERM

# Parse the raw ustar headers.  `tar -t` is not a security boundary: several
# implementations silently strip a leading ../ before printing a member name.
# We therefore validate the original header fields and use tar only afterward
# for its checksum/format validation.
archive_bytes=$(wc -c <"$archive" | tr -d ' ')
case "$archive_bytes" in *[!0-9]*|'') echo "cannot determine archive size" >&2; exit 1 ;; esac
[ $((archive_bytes % 512)) -eq 0 ] || { echo "archive is not block aligned" >&2; exit 1; }

block=0
members=0
expanded_bytes=0
while :; do
  offset=$((block * 512))
  [ $((offset + 512)) -le "$archive_bytes" ] || { echo "archive header is truncated" >&2; exit 1; }
  dd if="$archive" of="$scratch/header" bs=512 skip="$block" count=1 2>/dev/null
  [ "$(wc -c <"$scratch/header" | tr -d ' ')" -eq 512 ] || { echo "archive header is truncated" >&2; exit 1; }

  name=$(dd if="$scratch/header" bs=1 count=100 2>/dev/null | tr -d '\000')
  [ -n "$name" ] || break
  prefix=$(dd if="$scratch/header" bs=1 skip=345 count=155 2>/dev/null | tr -d '\000')
  member=$name
  [ -z "$prefix" ] || member="$prefix/$name"
  validate_name "$member"

  magic=$(dd if="$scratch/header" bs=1 skip=257 count=6 2>/dev/null | tr -d '\000 ')
  [ "$magic" = ustar ] || { echo "archive member is not strict ustar: $member" >&2; exit 1; }
  type=$(dd if="$scratch/header" bs=1 skip=156 count=1 2>/dev/null | tr -d '\000')
  case "$type" in ''|0|5) ;; *) echo "archive contains a non-regular member: $member" >&2; exit 1 ;; esac

  size_octal=$(dd if="$scratch/header" bs=1 skip=124 count=12 2>/dev/null | tr -d '\000 ')
  case "$size_octal" in *[!0-7]*) echo "archive member has an invalid size: $member" >&2; exit 1 ;; esac
  if [ -z "$size_octal" ]; then size=0; else size=$(printf '%d' "0$size_octal"); fi
  [ "$type" != 5 ] || [ "$size" -eq 0 ] || { echo "archive directory has a payload: $member" >&2; exit 1; }

  members=$((members + 1))
  expanded_bytes=$((expanded_bytes + size))
  [ "$members" -le 100000 ] || { echo "archive member limit exceeded" >&2; exit 1; }
  [ "$expanded_bytes" -le 10737418240 ] || { echo "archive expanded-byte limit exceeded" >&2; exit 1; }
  block=$((block + 1 + (size + 511) / 512))
done

tar -tf "$archive" >/dev/null
