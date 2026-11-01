# =============================================================================
# backup_repo.ps1 - Scheduled repository backup node (standalone, resumable)
#
# Pipeline : lock -> pack -> encrypt -> upload -> verify -> rotate -> done
# Persistence : backups/state/backup_state.json  (stage checkpoint)
# Logging  : backups/logs/backup_log.jsonl       (one JSON line per event)
# Resume   : if a run is interrupted, artifacts in tmp/ are honored for
#            ARTIFACT_TTL_HOURS and remaining stages continue on next run.
# Safety   : file lock prevents concurrent runs; stale lock auto-expires.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File backup_repo.ps1
#   Optional: -ForceRun    ignore min-interval skip
#             -SkipUpload  pack+encrypt+verify only (no remote push)
# Exit codes: 0 success/skip, 1 failure.
# =============================================================================

param(
    [switch]$ForceRun,
    [switch]$SkipUpload
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------------- paths ----------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptDir = $PSScriptRoot
$BkDir = Join-Path $RepoRoot 'backups'
$StateDir = Join-Path $BkDir 'state'
$LogDir = Join-Path $BkDir 'logs'
$TmpDir = Join-Path $BkDir 'tmp'
$ArchDir = Join-Path $BkDir 'archive'
foreach ($d in @($BkDir, $StateDir, $LogDir, $TmpDir, $ArchDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}
$StateFile = Join-Path $StateDir 'backup_state.json'
$LogFile = Join-Path $LogDir 'backup_log.jsonl'
$LockFile = Join-Path $StateDir 'lock'
$KeyFile = Join-Path $ScriptDir 'backup_key.bin'

# ---------------- config ----------------
$MinIntervalHours = 20          # skip if last success newer than this
$KeepLocalCopies = 7            # encrypted copies kept in backups/archive
$ArtifactTtlHours = 6           # resumable window for tmp artifacts
$LocalTargetDir = 'C:\Users\' + $env:USERNAME + '\.local\share\3D_Scanner_backups'
$GitSshExe = 'C:/Windows/System32/OpenSSH/ssh.exe'   # reads user ssh config (443 route)

# ---------------- helpers ----------------
function Write-Log {
    param([string]$Stage, [string]$Status, [string]$Detail, [int]$DurationMs = 0, [long]$Bytes = 0)
    $rec = [ordered]@{
        ts = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
        run_id = $script:RunId
        stage = $Stage
        status = $Status
        detail = $Detail
        bytes = $Bytes
        duration_ms = $DurationMs
    }
    Add-Content -Path $LogFile -Encoding UTF8 -Value ($rec | ConvertTo-Json -Compress)
}

function Save-State {
    $script:State | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding UTF8
}

function Get-State {
    if (Test-Path $StateFile) {
        try { return (Get-Content $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
    }
    return $null
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
}

# ---------------- run id / init ----------------
$script:RunId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

# ---------------- stage 0: mutual exclusion lock ----------------
if (Test-Path $LockFile) {
    $lockAgeMin = ((Get-Date) - (Get-Item $LockFile).LastWriteTime).TotalMinutes
    if ($lockAgeMin -lt 60) {
        Write-Output "SKIP: another backup run holds the lock (age $([int]$lockAgeMin) min)."
        exit 0
    }
    Write-Output "WARN: stale lock (age $([int]$lockAgeMin) min) - taking over."
}
Set-Content -Path $LockFile -Value "$script:RunId pid=$PID"
Write-Log 'lock' 'acquired' "run=$script:RunId"

try {
    # ---------------- state / resume decision ----------------
    # NOTE: use $prevState (NOT $state) - PS variable names are case-insensitive,
    # $state would collide with $script:State below.
    $prevState = Get-State
    $resume = $false
    if ($null -ne $prevState -and $prevState.stage -ne 'done' -and $prevState.error -ne '' -and $null -ne $prevState.run_id) {
        $ageH = ((Get-Date) - [datetime]$prevState.started).TotalHours
        if ($ageH -lt $ArtifactTtlHours) { $resume = $true }
    }
    if ($resume) {
        Write-Log 'resume' 'detected' "previous run $($prevState.run_id) stopped at stage=$($prevState.stage)"
    }

    # ---------------- min-interval skip ----------------
    if (-not $ForceRun -and $null -ne $prevState -and $prevState.stage -eq 'done' -and $prevState.last_success_utc) {
        # [datetime] cast parses ISO string (Z suffix -> local time); diff vs local now is correct
        $sinceH = ((Get-Date) - [datetime]$prevState.last_success_utc).TotalHours
        if ($sinceH -lt $MinIntervalHours) {
            Write-Log 'skip' 'fresh' "last success $([int]$sinceH)h ago < interval ${MinIntervalHours}h"
            Remove-Item $LockFile -Force
            Write-Output "SKIP: last successful backup was $([int]$sinceH) hours ago."
            exit 0
        }
    }

    $script:State = [ordered]@{
        run_id = $script:RunId
        started = (Get-Date).ToString('o')
        stage = 'init'
        last_success_utc = if ($null -ne $prevState) { $prevState.last_success_utc } else { $null }
        error = ''
        payload = ''
        payload_sha256 = ''
        payload_bytes = 0
        bundle_sha256 = ''
    }
    if ($resume) {
        # carry resumable artifact info forward
        $script:State.payload = $prevState.payload
        $script:State.payload_sha256 = $prevState.payload_sha256
        $script:State.payload_bytes = $prevState.payload_bytes
        $script:State.bundle_sha256 = $prevState.bundle_sha256
    }

    # ---------------- artifact paths ----------------
    $BundleFile = Join-Path $TmpDir 'repo.bundle'
    $DirtyZip = Join-Path $TmpDir 'workdir_dirty.zip'
    $PayloadZip = Join-Path $TmpDir 'payload.zip'
    $EncFile = Join-Path $TmpDir 'payload.enc'
    $payloadOk = $resume -and (Test-Path $EncFile) -and $script:State.payload_sha256 -ne ''

    # ================= stage 1: pack =================
    if (-not $payloadOk) {
        $t0 = Get-Date
        # defensive: stale enc without valid sha must not survive into encrypt stage
        if (Test-Path $EncFile) {
            Remove-Item $EncFile -Force
            Write-Log 'pack' 'stale_enc_removed' 'payload.enc present but not resumable - removed'
        }
        # 1a. full repository bundle (all refs + full history)
        if (Test-Path $BundleFile) { Remove-Item $BundleFile -Force }
        & git -C $RepoRoot bundle create -q $BundleFile --all
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $BundleFile)) {
            throw "git bundle create failed (exit $LASTEXITCODE)"
        }
        $script:State.bundle_sha256 = Get-Sha256 $BundleFile
        Write-Log 'pack' 'bundle_ok' 'git bundle --all created' `
            ( [int]((Get-Date) - $t0).TotalMilliseconds ) (Get-Item $BundleFile).Length

        # 1b. uncommitted changes snapshot (tracked+untracked, excludes ignored)
        $t1 = Get-Date
        $dirty = & git -C $RepoRoot status --porcelain
        if ($dirty -and $dirty.Count -gt 0) {
            if (Test-Path $DirtyZip) { Remove-Item $DirtyZip -Force }
            $rel = @($dirty | ForEach-Object { $_.Substring(3).Trim('"') })
            Push-Location $RepoRoot
            try { Compress-Archive -Path $rel -DestinationPath $DirtyZip -Force }
            finally { Pop-Location }
            Write-Log 'pack' 'dirty_ok' "$($rel.Count) changed files zipped" `
                ( [int]((Get-Date) - $t1).TotalMilliseconds ) (Get-Item $DirtyZip).Length
        }
        else {
            Write-Log 'pack' 'dirty_none' 'worktree clean, no dirty zip'
        }

        # 1c. outer payload zip = bundle + dirty zip
        if (Test-Path $PayloadZip) { Remove-Item $PayloadZip -Force }
        $inFiles = @($BundleFile)
        if (Test-Path $DirtyZip) { $inFiles += $DirtyZip }
        Compress-Archive -Path $inFiles -DestinationPath $PayloadZip -Force
        Write-Log 'pack' 'payload_ok' 'outer payload zip created' `
            ( [int]((Get-Date) - $t0).TotalMilliseconds ) (Get-Item $PayloadZip).Length
        $script:State.stage = 'packed'; Save-State
    }
    else {
        Write-Log 'pack' 'resumed' 'payload.enc already present from interrupted run'
    }

    # ================= stage 2: encrypt =================
    if (-not (Test-Path $EncFile)) {
        $t0 = Get-Date
        # key: 32 random bytes, generated once, never committed (gitignored)
        if (-not (Test-Path $KeyFile)) {
            $kb = New-Object byte[] 32
            $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
            $rng.GetBytes($kb); $rng.Dispose()
            [IO.File]::WriteAllBytes($KeyFile, $kb)
            $fp = Get-Sha256 $KeyFile
            Set-Content -Path (Join-Path $ScriptDir 'backup_key.fingerprint.txt') `
                -Value "key sha256 fingerprint: $fp`nstore this key outside the repo (password manager / usb)."
            Write-Log 'encrypt' 'key_generated' "key fingerprint $fp"
        }
        $key = [IO.File]::ReadAllBytes($KeyFile)
        $iv = New-Object byte[] 16
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($iv); $rng.Dispose()

        $aes = [System.Security.Cryptography.Aes]::Create()
        $aes.KeySize = 256; $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'
        $aes.Key = $key; $aes.IV = $iv
        $plainSha = Get-Sha256 $PayloadZip

        $fin = [IO.File]::OpenRead($PayloadZip)
        $fout = [IO.File]::Create($EncFile)
        try {
            $fout.Write($iv, 0, 16)                      # header: 16-byte IV
            $cs = New-Object System.Security.Cryptography.CryptoStream($fout, $aes.CreateEncryptor(), 'Write')
            $fin.CopyTo($cs); $cs.FlushFinalBlock(); $cs.Dispose()
        }
        finally { $fin.Dispose(); $fout.Dispose(); $aes.Dispose() }

        $script:State.payload = $EncFile
        $script:State.payload_sha256 = $plainSha
        $script:State.payload_bytes = (Get-Item $EncFile).Length
        $script:State.stage = 'encrypted'; Save-State
        Write-Log 'encrypt' 'ok' "aes-256-cbc, plain sha256=$plainSha" `
            ( [int]((Get-Date) - $t0).TotalMilliseconds ) $script:State.payload_bytes
    }
    else {
        Write-Log 'encrypt' 'resumed' 'using existing payload.enc'
    }

    # ================= stage 3: verify (decrypt round-trip) =================
    $t0 = Get-Date
    $key = [IO.File]::ReadAllBytes($KeyFile)
    $raw = [IO.File]::ReadAllBytes($EncFile)
    if ($raw.Length -le 16) { throw 'payload.enc too small / corrupted' }
    $aes = [System.Security.Cryptography.Aes]::Create()
    $aes.KeySize = 256; $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'
    $aes.Key = $key; $aes.IV = $raw[0..15]
    $ms = New-Object System.IO.MemoryStream -ArgumentList @($raw, 16, ($raw.Length - 16))
    $cs = New-Object System.Security.Cryptography.CryptoStream($ms, $aes.CreateDecryptor(), 'Read')
    $decSha = $null
    try {
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $decSha = ([System.BitConverter]::ToString($sha.ComputeHash($cs))).Replace('-', '').ToLower()
        $sha.Dispose()
    }
    finally { $cs.Dispose(); $ms.Dispose(); $aes.Dispose() }
    if ($decSha -ne $script:State.payload_sha256) {
        throw "verify FAILED: decrypted sha256 $decSha != expected $($script:State.payload_sha256)"
    }
    Write-Log 'verify' 'ok' 'decrypt round-trip hash matches' `
        ( [int]((Get-Date) - $t0).TotalMilliseconds ) $raw.Length
    $script:State.stage = 'verified'; Save-State

    # ================= stage 4: upload targets =================
    if ($SkipUpload) {
        Write-Log 'upload' 'skipped' '-SkipUpload flag set'
    }
    else {
        $meta = [ordered]@{
            run_id = $script:RunId
            created_utc = (Get-Date).ToUniversalTime().ToString('o')
            payload_sha256 = $script:State.payload_sha256
            bundle_sha256 = $script:State.bundle_sha256
            bytes = $script:State.payload_bytes
            cipher = 'AES-256-CBC (16-byte IV header)'
        }
        # ---- target A: git branch 'backups' on origin (encrypted, force-pushed) ----
        $t0 = Get-Date
        $upDir = Join-Path $TmpDir 'uploadrepo'
        if (-not $resume -or -not (Test-Path (Join-Path $upDir '.git'))) {
            if (Test-Path $upDir) { Remove-Item $upDir -Recurse -Force }
            & git init -q -b backups $upDir | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "git init uploadrepo failed" }
            Copy-Item $EncFile (Join-Path $upDir 'payload.enc') -Force
            $meta | ConvertTo-Json | Set-Content (Join-Path $upDir 'meta.json') -Encoding UTF8
            & git -C $upDir add -A
            & git -C $upDir -c user.name='backup-agent' -c user.email='backup@local' `
                commit -q -m "encrypted backup $script:RunId"
            $originUrl = & git -C $RepoRoot remote get-url origin
            & git -C $upDir remote add origin $originUrl
        }
        $env:GIT_SSH_COMMAND = $GitSshExe
        $pushed = $false; $lastErr = ''
        for ($i = 1; $i -le 3; $i++) {
            & git -C $upDir push -q -f origin backups
            if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
            $lastErr = "attempt $i exit $LASTEXITCODE"
            Write-Log 'upload' 'retry' "git push attempt $i failed, backoff $($i*10)s"
            Start-Sleep -Seconds ($i * 10)
        }
        if (-not $pushed) { throw "git push to origin/backups failed after 3 attempts ($lastErr)" }
        Write-Log 'upload' 'git_branch_ok' 'pushed encrypted payload to origin backups branch' `
            ( [int]((Get-Date) - $t0).TotalMilliseconds ) $script:State.payload_bytes

        # ---- target B: local secondary directory (off-repo copy) ----
        $t1 = Get-Date
        if (-not (Test-Path $LocalTargetDir)) { New-Item -ItemType Directory -Force -Path $LocalTargetDir | Out-Null }
        $stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
        Copy-Item $EncFile (Join-Path $LocalTargetDir "3D_Scanner_backup_$stamp.enc") -Force
        $meta | ConvertTo-Json | Set-Content (Join-Path $LocalTargetDir "3D_Scanner_backup_$stamp.meta.json") -Encoding UTF8
        # rotate local target: keep newest KeepLocalCopies pairs
        $old = Get-ChildItem $LocalTargetDir -Filter '3D_Scanner_backup_*.enc' | Sort-Object Name -Descending
        if ($old.Count -gt $KeepLocalCopies) {
            $old | Select-Object -Skip $KeepLocalCopies | ForEach-Object {
                $m = $_.FullName -replace '\.enc$', '.meta.json'
                Remove-Item $_.FullName -Force
                if (Test-Path $m) { Remove-Item $m -Force }
            }
        }
        Write-Log 'upload' 'local_dir_ok' "copied to $LocalTargetDir" `
            ( [int]((Get-Date) - $t1).TotalMilliseconds ) $script:State.payload_bytes
    }
    $script:State.stage = 'uploaded'; Save-State

    # ================= stage 5: rotate local archive =================
    $archName = "payload_$($script:RunId).enc"
    Copy-Item $EncFile (Join-Path $ArchDir $archName) -Force
    $oldArch = Get-ChildItem $ArchDir -Filter 'payload_*.enc' | Sort-Object Name -Descending
    if ($oldArch.Count -gt $KeepLocalCopies) {
        $oldArch | Select-Object -Skip $KeepLocalCopies | Remove-Item -Force
    }
    Write-Log 'rotate' 'ok' "archive keeps $($KeepLocalCopies) copies"

    # ================= done =================
    $script:State.stage = 'done'
    $script:State.last_success_utc = (Get-Date).ToUniversalTime().ToString('o')
    $script:State.error = ''
    Save-State
    Write-Log 'run' 'success' "backup pipeline completed, run=$script:RunId"
    Write-Output "SUCCESS: backup $script:RunId completed ($([math]::Round($script:State.payload_bytes/1MB,1)) MB encrypted)."
    exit 0
}
catch {
    $msg = $_.Exception.Message
    Write-Log 'run' 'failed' $msg
    if ($script:State) { $script:State.stage = if ($script:State.stage) { $script:State.stage } else { 'init' }; $script:State.error = $msg; Save-State }
    Write-Output "FAILED: $msg"
    exit 1
}
finally {
    if (Test-Path $LockFile) { Remove-Item $LockFile -Force }
}
