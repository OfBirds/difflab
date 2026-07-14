#!/bin/sh
# difflab git-gate — forced-command filter for the container SSH key.
#
# Allows ONLY these command shapes (the trailing HEAD ref is optional so the
# gate accepts app versions from before and after the diff-HEAD change):
#   difflab-batch-status<US>path1<US>path2…  (batch status; <US>=0x1F)
#   git -C <path> --no-pager diff [HEAD]
#   git -C <path> --no-pager diff --numstat [HEAD]
#   git -C <path> status --short
#
# Any other command is rejected with "difflab: command not permitted".
# Set DIFFLAB_GATE_DEBUG=1 to log rejected commands to stderr.
#
# Install path:  /usr/local/lib/difflab/git-gate.sh  (chmod 755)
# authorized_keys prefix:
#   command="/usr/local/lib/difflab/git-gate.sh",no-pty,...

reject() {
    [ -n "$DIFFLAB_GATE_DEBUG" ] && printf 'difflab: rejected [%s]: %s\n' "$1" "$SSH_ORIGINAL_COMMAND" >&2
    printf 'difflab: command not permitted\n' >&2
    exit 1
}

cmd="$SSH_ORIGINAL_COMMAND"
[ -z "$cmd" ] && reject "empty"

case "$cmd" in
    'difflab-batch-status'*)
        batch_rest="${cmd#difflab-batch-status}"
        # Must have at least one unit-separator (0x1F) introducing a path
        case "$batch_rest" in
            "$(printf '\037')"*) ;;
            *) reject "empty-batch" ;;
        esac
        # Strip the leading separator
        batch_rest="${batch_rest#$(printf '\037')}"
        # Split on unit-separator into positional parameters
        old_ifs="$IFS"
        IFS=$(printf '\037')
        set -f
        # shellcheck disable=SC2086
        set -- $batch_rest
        IFS="$old_ifs"
        set +f
        [ "$#" -eq 0 ] && reject "empty-batch"
        [ "$#" -gt 64 ] && reject "too-many"
        for p do
            [ -z "$p" ] && reject "empty-path-in-batch"
            case "$p" in -*) reject "flag-path-in-batch" ;; esac
            case "$p" in
                *[!a-zA-Z0-9_@%+=:,./-]*) reject "unsafe-chars-in-batch" ;;
            esac
            printf '\036REPO %s\n' "$p"
            out=$(git -C "$p" status --short 2>&1); rc=$?
            [ -n "$out" ] && printf '%s\n' "$out"
            printf '\036RC %s\n' "$rc"
        done
        exit 0
        ;;
    'git -C '*) ;;
    *) reject "bad-prefix" ;;
esac

# Strip the 'git -C ' prefix; remainder is: <quoted-path> <subcmd-args>
rest="${cmd#git -C }"

# Identify operation by matching the known tail suffixes.
# Order: longest suffix first (numstat before diff, HEAD variants before the
# bare ones) so the most specific pattern wins. `ref` captures the optional
# trailing HEAD so it can be passed through to git unchanged.
case "$rest" in
    *' --no-pager diff --numstat HEAD')
        op="numstat"; ref="HEAD"
        qpath="${rest% --no-pager diff --numstat HEAD}"
        ;;
    *' --no-pager diff --numstat')
        op="numstat"; ref=""
        qpath="${rest% --no-pager diff --numstat}"
        ;;
    *' --no-pager diff HEAD')
        op="diff"; ref="HEAD"
        qpath="${rest% --no-pager diff HEAD}"
        ;;
    *' --no-pager diff')
        op="diff"; ref=""
        qpath="${rest% --no-pager diff}"
        ;;
    *' status --short')
        op="status"; ref=""
        qpath="${rest% status --short}"
        ;;
    *)
        reject "unknown-op"
        ;;
esac

[ -n "$qpath" ] || reject "empty-path"

# Dequote the path.
# shlex.quote (Python) wraps in single quotes when the path contains non-safe
# characters; safe paths are emitted unquoted.
case "$qpath" in
    "'"*"'")
        # Simple single-quoted path — strip surrounding quotes
        inner="${qpath#\'}"
        inner="${inner%\'}"
        # Reject complex shlex encoding (path itself contains a single quote)
        case "$inner" in
            *"'"*) reject "complex-quote" ;;
        esac
        path="$inner"
        ;;
    "'"*)
        # Starts with quote but does not end with one
        reject "unclosed-quote"
        ;;
    *)
        # Bare (unquoted) path — must contain only shlex-safe characters
        # Safe set matches Python shlex: [a-zA-Z0-9_@%+=:,./-]
        case "$qpath" in
            *[!a-zA-Z0-9_@%+=:,./-]*)
                reject "unsafe-chars"
                ;;
        esac
        path="$qpath"
        ;;
esac

[ -n "$path" ] || reject "empty-dequoted"

# Path must not start with '-' (would be interpreted as a flag)
case "$path" in -*) reject "flag-path" ;; esac

# exec replaces this shell — no further shell interpretation of $path.
# $ref is a controlled literal ("HEAD" or empty); unquoted so an empty ref
# contributes no argument.
case "$op" in
    diff)    exec git -C "$path" --no-pager diff $ref ;;
    numstat) exec git -C "$path" --no-pager diff --numstat $ref ;;
    status)  exec git -C "$path" status --short ;;
esac
