#!/bin/sh
# relax-duration-floor.sh -- reversible, TEST-ONLY relaxation of the release
# soak duration floor from 43200 seconds (12 hours) to 21600 seconds (6 hours).
#
# --floor lowers the test target further still (default 21600). Every step down
# is a step further from the release gate: a run below 21600 is not even a
# standard test soak, and its evidence must say which floor it used.
#
# THIS IS NOT A RELEASE CHANGE. It exists so a shorter TEST soak can run against
# the real owner soak driver. The 12-hour floor is the release gate. Revert this
# patch (--revert) before any release-gate run, and never publish evidence from
# a patched tree as release evidence.
#
# It edits, in place and reversibly:
#   cloud-deployer-infra/scripts/soak.py            (validate_manifest floor)
#   cloud-deployer-infra/soak-runner/zkdeal_soak.py (SOAK_DURATION_SECONDS floor)
#   cloud-deployer-infra/config/schemas/release-soak-manifest.schema.json
#                                                   (only with --with-schema)
#
# Each edited file is first copied to <file>.release-floor.bak. --revert
# restores every backup byte for byte and deletes the backups.
#
# Usage:
#   ./relax-duration-floor.sh [--repo-root PATH] [--with-schema] [--floor SECONDS]
#   ./relax-duration-floor.sh --check [--repo-root PATH]
#   ./relax-duration-floor.sh --revert [--repo-root PATH]
#
# Exit codes: 0 ok, 1 usage/precondition failure, 2 patch verification failure.

set -eu

MARKER='ZKDEAL-TEST-SOAK-FLOOR'
BACKUP_SUFFIX='.release-floor.bak'

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
MODE=apply
WITH_SCHEMA=0
# The shipped test target. --floor takes it lower, deliberately explicitly.
FLOOR=21600

die() {
    printf 'ERROR: %s\n' "$1" >&2
    exit "${2:-1}"
}

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --repo-root)
            [ $# -ge 2 ] || die "--repo-root needs a path"
            REPO_ROOT=$(CDPATH= cd -- "$2" && pwd) || die "--repo-root is not a directory: $2"
            shift 2
            ;;
        --revert) MODE=revert; shift ;;
        --check) MODE=check; shift ;;
        --with-schema) WITH_SCHEMA=1; shift ;;
        --floor)
            [ $# -ge 2 ] || die "--floor needs a value in seconds"
            case "$2" in *[!0-9]*|'') die "--floor must be a positive integer";; esac
            [ "$2" -ge 60 ] || die "--floor below 60 seconds is not a soak"
            [ "$2" -lt 43200 ] || die "--floor must be below the 43200s release floor"
            FLOOR=$2; shift 2 ;;
        -h|--help) usage 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

INFRA="$REPO_ROOT/cloud-deployer-infra"
SOAK_PY="$INFRA/scripts/soak.py"
RUNNER_PY="$INFRA/soak-runner/zkdeal_soak.py"
SCHEMA_JSON="$INFRA/config/schemas/release-soak-manifest.schema.json"

[ -d "$INFRA" ] || die "cloud-deployer-infra not found under $REPO_ROOT (use --repo-root)"
[ -f "$SOAK_PY" ] || die "missing $SOAK_PY"
[ -f "$RUNNER_PY" ] || die "missing $RUNNER_PY"

# --- exact lines --------------------------------------------------------------

# Python groups digits with underscores; mirror that so the patched source
# reads the way the surrounding code does.
FLOOR_PY=$(printf '%s' "$FLOOR" | sed -E ':a;s/([0-9])([0-9]{3})($|_)/\1_\2\3/;ta')
FLOOR_HOURS=$((FLOOR / 3600))

SOAK_OLD_1='    if int(manifest.get("durationSeconds", 0)) < 43_200:'
SOAK_NEW_1="    if int(manifest.get('durationSeconds', 0)) < ${FLOOR_PY}:"
SOAK_OLD_2='        errors.append("release soak duration must be at least 43200 seconds (12 hours)")'
SOAK_NEW_2="        errors.append('release soak duration must be at least ${FLOOR} seconds (TEST-ONLY floor; release floor is 43200)')"

RUNNER_OLD_1='    if duration != manifest.get("durationSeconds") or duration < 43_200:'
RUNNER_NEW_1="    if duration != manifest.get('durationSeconds') or duration < ${FLOOR_PY}:"
RUNNER_OLD_2='        raise SoakRunnerError("SOAK_DURATION_SECONDS must equal the manifest and be at least 12 hours")'
RUNNER_NEW_2="        raise SoakRunnerError('SOAK_DURATION_SECONDS must equal the manifest and be at least ${FLOOR} seconds (TEST-ONLY floor; release floor is 43200)')"

SCHEMA_OLD_1='    "durationSeconds": {"type": "integer", "minimum": 43200},'
SCHEMA_NEW_1="    \"durationSeconds\": {\"type\": \"integer\", \"minimum\": ${FLOOR}, \"\$comment\": \"ZKDEAL-TEST-SOAK-FLOOR: TEST-ONLY 43200 -> ${FLOOR}; revert before any release gate\"},"

PY_MARK_1="    # $MARKER: TEST-ONLY relaxation of the 43200s (12h) release floor to ${FLOOR}s (${FLOOR_HOURS}h)."
PY_MARK_2="    # $MARKER: This is not release evidence. Revert before any release gate with"
PY_MARK_3="    # $MARKER: cloud-deployer-infra/soak6h/relax-duration-floor.sh --revert"

CHANGED=''

# --- helpers ------------------------------------------------------------------

CR=$(printf '\r')

count_exact() {
    # count_exact <file> <line>; prints the number of exactly matching lines
    grep -c -x -F -- "$2" "$1" 2>/dev/null || true
}

detect_eol() {
    # detect_eol <file> <line>; echoes "" for LF files and CR for CRLF files,
    # so the patch preserves whatever line endings the checkout already uses.
    if [ "$(count_exact "$1" "$2")" = "1" ]; then
        printf ''
    elif [ "$(count_exact "$1" "$2$CR")" = "1" ]; then
        printf '%s' "$CR"
    else
        printf 'ERROR: %s: expected exactly one line matching the release floor\n' "$1" >&2
        printf '       expected: %s\n' "$2" >&2
        printf '       (upstream code moved, or this file uses unexpected line endings)\n' >&2
        exit 1
    fi
}

require_once() {
    # require_once <file> <line> <what>
    n=$(count_exact "$1" "$2")
    if [ "$n" != "1" ]; then
        printf 'ERROR: %s: expected exactly one %s line, found %s\n' "$1" "$3" "$n" >&2
        printf '       expected: %s\n' "$2" >&2
        printf '       (upstream code moved, or the checkout has unexpected line endings)\n' >&2
        exit 1
    fi
}

apply_file() {
    # apply_file <file> <old1> <new1> <old2> <new2> <insert_markers:0|1>
    file=$1; old1=$2; new1=$3; old2=$4; new2=$5; markers=$6
    backup="$file$BACKUP_SUFFIX"

    if [ -e "$backup" ]; then
        die "$backup already exists; the tree looks patched. Run --revert first."
    fi
    if grep -q -F -- "$MARKER" "$file" 2>/dev/null; then
        die "$file already carries the $MARKER comment but has no backup; fix by hand."
    fi
    eol=$(detect_eol "$file" "$old1")
    old1="$old1$eol"; new1="$new1$eol"
    if [ -n "$old2" ]; then
        old2="$old2$eol"; new2="$new2$eol"
    fi
    mark1="$PY_MARK_1$eol"; mark2="$PY_MARK_2$eol"; mark3="$PY_MARK_3$eol"
    require_once "$file" "$old1" "release-floor"
    if [ -n "$old2" ]; then
        require_once "$file" "$old2" "release-floor message"
    fi

    cp -p -- "$file" "$backup"

    tmp="$file.floor.tmp.$$"
    if ! awk -v old1="$old1" -v new1="$new1" -v old2="$old2" -v new2="$new2" \
             -v m1="$mark1" -v m2="$mark2" -v m3="$mark3" -v markers="$markers" '
        BEGIN { n1 = 0; n2 = 0 }
        {
            if ($0 == old1) {
                if (markers == "1") { print m1; print m2; print m3 }
                print new1
                n1 = n1 + 1
                next
            }
            if (old2 != "" && $0 == old2) { print new2; n2 = n2 + 1; next }
            print
        }
        END {
            if (n1 != 1) { exit 3 }
            if (old2 != "" && n2 != 1) { exit 3 }
        }
    ' "$file" > "$tmp"; then
        rm -f -- "$tmp"
        cp -p -- "$backup" "$file"
        rm -f -- "$backup"
        die "$file: rewrite did not match exactly once; nothing was changed" 2
    fi

    cat -- "$tmp" > "$file"
    rm -f -- "$tmp"

    # Post-conditions: new floor present exactly once, old floor gone.
    n=$(count_exact "$file" "$new1")
    if [ "$n" != "1" ]; then
        cp -p -- "$backup" "$file"; rm -f -- "$backup"
        die "$file: verification failed (new floor line count $n); original restored" 2
    fi
    n=$(count_exact "$file" "$old1")
    if [ "$n" != "0" ]; then
        cp -p -- "$backup" "$file"; rm -f -- "$backup"
        die "$file: verification failed (old floor line still present); original restored" 2
    fi
    if [ "$markers" = "1" ] && ! grep -q -F -- "$MARKER" "$file"; then
        cp -p -- "$backup" "$file"; rm -f -- "$backup"
        die "$file: verification failed (marker comment missing); original restored" 2
    fi
    CHANGED="$CHANGED $file"
    printf 'PATCHED  %s\n' "$file"
    printf '         backup -> %s\n' "$backup"
}

revert_file() {
    file=$1
    backup="$file$BACKUP_SUFFIX"
    if [ ! -e "$backup" ]; then
        printf 'SKIP     %s (no %s)\n' "$file" "$BACKUP_SUFFIX"
        return 0
    fi
    cat -- "$backup" > "$file"
    rm -f -- "$backup"
    if grep -q -F -- "$MARKER" "$file"; then
        die "$file still carries the $MARKER comment after restore; inspect by hand" 2
    fi
    CHANGED="$CHANGED $file"
    printf 'REVERTED %s\n' "$file"
    printf '         backup removed: %s\n' "$backup"
}

check_file() {
    file=$1; old1=$2; new1=$3
    backup="$file$BACKUP_SUFFIX"
    if [ "$(count_exact "$file" "$new1$CR")" = "1" ]; then
        new1="$new1$CR"
    elif [ "$(count_exact "$file" "$old1$CR")" = "1" ]; then
        old1="$old1$CR"
    fi
    if [ "$(count_exact "$file" "$new1")" = "1" ]; then
        if [ -e "$backup" ]; then
            printf 'PATCHED  %s (backup present)\n' "$file"
        else
            printf 'PATCHED  %s (WARNING: no backup, --revert cannot restore it)\n' "$file"
        fi
    elif [ "$(count_exact "$file" "$old1")" = "1" ]; then
        printf 'RELEASE  %s (43200s floor intact)\n' "$file"
    else
        printf 'UNKNOWN  %s (neither the release nor the test floor line matched)\n' "$file"
    fi
}

runner_source_sha256() {
    if ! command -v sha256sum >/dev/null 2>&1; then
        return 0
    fi
    value=$(cat \
        "$INFRA/soak-runner/zkdeal_soak.py" \
        "$INFRA/scripts/common.py" \
        "$INFRA/scripts/soak.py" \
        "$INFRA/config/schemas/release-soak-manifest.schema.json" | sha256sum | awk '{print $1}')
    printf '\nSOAK_RUNNER_SOURCE_SHA256=%s\n' "$value"
    printf 'The soak-runner image bakes these four files and binds this hash at build\n'
    printf 'time, so the image MUST be rebuilt with this build-arg after any change here:\n'
    printf '  docker build --file soak-runner/Dockerfile --target candidate \\\n'
    printf '    --build-arg SOAK_RUNNER_SOURCE_SHA256=%s \\\n' "$value"
    printf '    --build-arg OWNER_SOAK_DRIVER_IMAGE=<owner image@sha256:...> \\\n'
    printf '    --build-arg OWNER_SOAK_DRIVER_SOURCE_SHA256=<owner driver source sha256> \\\n'
    printf '    --tag zkdeal-soak-runner:test6h .\n'
}

# --- main ---------------------------------------------------------------------

printf 'repository root : %s\n' "$REPO_ROOT"
printf 'mode            : %s\n\n' "$MODE"

case "$MODE" in
    check)
        check_file "$SOAK_PY" "$SOAK_OLD_1" "$SOAK_NEW_1"
        check_file "$RUNNER_PY" "$RUNNER_OLD_1" "$RUNNER_NEW_1"
        if [ -f "$SCHEMA_JSON" ]; then
            check_file "$SCHEMA_JSON" "$SCHEMA_OLD_1" "$SCHEMA_NEW_1"
        fi
        runner_source_sha256
        ;;
    revert)
        revert_file "$SOAK_PY"
        revert_file "$RUNNER_PY"
        if [ -f "$SCHEMA_JSON" ]; then
            revert_file "$SCHEMA_JSON"
        fi
        if [ -z "$CHANGED" ]; then
            printf '\nNothing to revert: no %s backups were found.\n' "$BACKUP_SUFFIX"
        else
            printf '\nReverted files:%s\n' "$CHANGED"
            printf 'The release 43200s (12h) duration floor is back in force.\n'
        fi
        runner_source_sha256
        ;;
    apply)
        apply_file "$SOAK_PY" "$SOAK_OLD_1" "$SOAK_NEW_1" "$SOAK_OLD_2" "$SOAK_NEW_2" 1
        apply_file "$RUNNER_PY" "$RUNNER_OLD_1" "$RUNNER_NEW_1" "$RUNNER_OLD_2" "$RUNNER_NEW_2" 1
        if [ "$WITH_SCHEMA" = "1" ]; then
            [ -f "$SCHEMA_JSON" ] || die "missing $SCHEMA_JSON"
            apply_file "$SCHEMA_JSON" "$SCHEMA_OLD_1" "$SCHEMA_NEW_1" "" "" 0
        else
            printf 'SKIPPED  %s\n' "$SCHEMA_JSON"
            printf '         (its "minimum": 43200 is not enforced at runtime; pass --with-schema\n'
            printf '          if you want the JSON Schema to agree with the patched code)\n'
        fi
        printf '\nChanged files:%s\n' "$CHANGED"
        printf '\n'
        printf '**********************************************************************\n'
        printf '* TEST-ONLY DURATION FLOOR IS NOW 21600s (6h). THIS IS NOT A RELEASE  *\n'
        printf '* CONFIGURATION. Run:                                                 *\n'
        printf '*   %s --revert\n' "$0"
        printf '* before any release-gate soak, and do not publish results from this   *\n'
        printf '* tree as release evidence.                                           *\n'
        printf '**********************************************************************\n'
        runner_source_sha256
        ;;
esac

exit 0
