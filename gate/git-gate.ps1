# difflab git-gate — forced-command filter for the container SSH key (Windows/PowerShell).
#
# Allows ONLY these four command shapes:
#   difflab-batch-status<US>path1<US>path2…  (batch status; <US>=0x1F)
#   git -C "path" --no-pager diff
#   git -C "path" --no-pager diff --numstat
#   git -C "path" status --short
#
# Any other command is rejected with "difflab: command not permitted" (on stderr, exit 1).
# Set $env:DIFFLAB_GATE_DEBUG=1 to log rejected commands to stderr.
#
# Install path:  C:\ProgramData\difflab\git-gate.ps1
# authorized_keys prefix (use forward slashes for cross-platform SSH compatibility):
#   command="powershell -NoProfile -ExecutionPolicy Bypass -File C:/ProgramData/difflab/git-gate.ps1",no-pty,...

param()
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$original = $env:SSH_ORIGINAL_COMMAND

function Reject ([string]$reason) {
    if ($env:DIFFLAB_GATE_DEBUG) {
        [Console]::Error.WriteLine("difflab: rejected [{0}]: {1}" -f $reason, $original)
    }
    [Console]::Error.WriteLine("difflab: command not permitted")
    exit 1
}

if ([string]::IsNullOrEmpty($original)) { Reject 'empty' }

# Handle batch-status BEFORE the metachar check (paths contain 0x1F which is harmless)
if ($original.StartsWith('difflab-batch-status' + [char]0x1F)) {
    $suffix = $original.Substring('difflab-batch-status'.Length)
    $paths = $suffix -split [char]0x1F | Where-Object { $_ -ne '' }
    if ($paths.Count -eq 0) { Reject 'empty-batch' }
    if ($paths.Count -gt 64) { Reject 'too-many' }
    foreach ($p in $paths) {
        if ([string]::IsNullOrEmpty($p)) { Reject 'empty-path-in-batch' }
        if ($p.StartsWith('-')) { Reject 'flag-path' }
        foreach ($c in [char[]]'`;&|<>"') {
            if ($p.IndexOf($c) -ge 0) { Reject 'bad-path-char' }
        }
        [Console]::Out.WriteLine(([char]0x1E) + 'REPO ' + $p)
        $out = & git -C $p status --short 2>&1 | Out-String
        if ($out.Trim()) { [Console]::Out.Write($out) }
        [Console]::Out.WriteLine(([char]0x1E) + 'RC ' + $LASTEXITCODE)
    }
    exit 0
}

# Reject shell metacharacters that have no place in a git command
# (backtick, semicolon, ampersand, pipe, angle brackets)
foreach ($c in [char[]]"``;&|<>") {
    if ($original.IndexOf($c) -ge 0) { Reject 'metachar' }
}

# Match the three allowed forms.
# The app always double-quotes paths and normalises backslashes to forward slashes.
# Order: numstat before plain diff so the longer pattern is tried first.
if ($original -match '^git\s+-C\s+"([^"]*)"\s+--no-pager\s+diff\s+--numstat\s*$') {
    $path = $Matches[1]
    $op = 'numstat'
} elseif ($original -match '^git\s+-C\s+"([^"]*)"\s+--no-pager\s+diff\s*$') {
    $path = $Matches[1]
    $op = 'diff'
} elseif ($original -match '^git\s+-C\s+"([^"]*)"\s+status\s+--short\s*$') {
    $path = $Matches[1]
    $op = 'status'
} else {
    Reject 'no-match'
}

if ([string]::IsNullOrEmpty($path)) { Reject 'empty-path' }
if ($path.StartsWith('-'))          { Reject 'flag-path' }

# Extra path-character check (belt-and-suspenders after the regex capture)
foreach ($c in [char[]]'`;&|<>"') {
    if ($path.IndexOf($c) -ge 0) { Reject 'bad-path-char' }
}

switch ($op) {
    'diff'    { & git -C $path --no-pager diff;          exit $LASTEXITCODE }
    'numstat' { & git -C $path --no-pager diff --numstat; exit $LASTEXITCODE }
    'status'  { & git -C $path status --short;           exit $LASTEXITCODE }
}
