[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (Resolve-Path (Join-Path $scriptRoot "..")).Path
}
$rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd("\")
$historyPath = Join-Path $rootPath "history"
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $rootPath "history/docs/HISTORY-MIGRATION-2026-10-17.json"
}
$logPath = [IO.Path]::GetFullPath($LogPath)

function Get-RelativePath([string]$FullName) {
    $fullPath = [IO.Path]::GetFullPath($FullName)
    $prefix = $rootPath.TrimEnd("\") + "\"
    if ($fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $fullPath.Substring($prefix.Length).Replace("\", "/")
    }
    return $fullPath.Replace("\", "/")
}

function Get-FileRecords([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }
    $items = @()
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        $items = @(Get-ChildItem -LiteralPath $Path -File -Recurse -Force -ErrorAction Stop)
    } else {
        $items = @($item)
    }
    foreach ($file in $items) {
        [pscustomobject]@{
            path = Get-RelativePath $file.FullName
            bytes = [int64]$file.Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        }
    }
}

function Add-Move([System.Collections.Generic.List[object]]$Plan, [string]$RelativeSource, [string]$RelativeDestination) {
    $source = Join-Path $rootPath ($RelativeSource -replace "/", "\")
    $destination = Join-Path $rootPath ($RelativeDestination -replace "/", "\")
    if (Test-Path -LiteralPath $source) {
        if (Test-Path -LiteralPath $destination) {
            throw "Destination already exists: $RelativeDestination"
        }
        $Plan.Add([pscustomobject]@{ source = $source; destination = $destination; source_relative = $RelativeSource; destination_relative = $RelativeDestination })
    }
}

$plan = New-Object 'System.Collections.Generic.List[object]'

# Whole directories that are unambiguously historical or outside the current runtime path.
foreach ($relative in @("archive", "official-attachments", "runtime-probe")) {
    Add-Move $plan $relative ("history/" + $relative)
}

# All previous phase documents are frozen evidence; docs/current remains live.
$docsPath = Join-Path $rootPath "docs"
if (Test-Path -LiteralPath $docsPath) {
    foreach ($directory in (Get-ChildItem -LiteralPath $docsPath -Directory -Force | Where-Object Name -like "phase-*")) {
        Add-Move $plan ("docs/" + $directory.Name) ("history/docs/" + $directory.Name)
    }
}

# Phase/overlay deployment files are never a current production entry point.
$deployPath = Join-Path $rootPath "deploy"
if (Test-Path -LiteralPath $deployPath) {
    foreach ($item in (Get-ChildItem -LiteralPath $deployPath -Force | Where-Object Name -match "(?i)(phase|overlay|fast|runtime)")) {
        Add-Move $plan ("deploy/" + $item.Name) ("history/deploy/" + $item.Name)
    }
}

# Runtime experiment, browser, probe, and temporary directories are historical.
$runtimePath = Join-Path $rootPath "runtime"
if (Test-Path -LiteralPath $runtimePath) {
    foreach ($directory in (Get-ChildItem -LiteralPath $runtimePath -Directory -Force | Where-Object Name -match "(?i)^(phase|probe|chrome|cmgb|local-target|deploy|test-temp|tmp-|\.tmp-)")) {
        Add-Move $plan ("runtime/" + $directory.Name) ("history/runtime/" + $directory.Name)
    }

    # Files directly below runtime are scan outputs, logs, or pre-phase database copies.
    # The active database and its SQLite sidecars stay in runtime.
    foreach ($file in (Get-ChildItem -LiteralPath $runtimePath -File -Force | Where-Object Name -notmatch "^job_hub\.sqlite3($|-wal$|-shm$)")) {
        Add-Move $plan ("runtime/" + $file.Name) ("history/runtime/root-files/" + $file.Name)
    }
}

# Generated interpreter/test caches are reproducible and not application inputs.
foreach ($relative in @("__pycache__", ".pytest_cache")) {
    Add-Move $plan $relative ("history/cache/" + $relative)
}

New-Item -ItemType Directory -Force -Path $historyPath | Out-Null
$allRecords = New-Object 'System.Collections.Generic.List[object]'
$startedAt = [DateTime]::UtcNow.ToString("o")
$recovered = $false

if ($plan.Count -eq 0 -and (Test-Path -LiteralPath $historyPath)) {
    $recovered = $true
    # Recovery path for an interrupted final log write: infer the original
    # path from the deterministic archive layout and verify the stored hash.
    foreach ($file in (Get-ChildItem -LiteralPath $historyPath -File -Recurse -Force)) {
        $targetRelative = Get-RelativePath $file.FullName
        $sourceRelative = $null
        if ($targetRelative -match "^history/archive/(.+)$") {
            $sourceRelative = "archive/$($Matches[1])"
        } elseif ($targetRelative -match "^history/docs/(.+)$") {
            $sourceRelative = "docs/$($Matches[1])"
        } elseif ($targetRelative -match "^history/deploy/(.+)$") {
            $sourceRelative = "deploy/$($Matches[1])"
        } elseif ($targetRelative -match "^history/runtime/root-files/(.+)$") {
            $sourceRelative = "runtime/$($Matches[1])"
        } elseif ($targetRelative -match "^history/runtime/(.+)$") {
            $sourceRelative = "runtime/$($Matches[1])"
        } elseif ($targetRelative -match "^history/official-attachments/(.+)$") {
            $sourceRelative = "official-attachments/$($Matches[1])"
        } elseif ($targetRelative -match "^history/runtime-probe/(.+)$") {
            $sourceRelative = "runtime-probe/$($Matches[1])"
        } elseif ($targetRelative -match "^history/cache/__pycache__/(.+)$") {
            $sourceRelative = "__pycache__/$($Matches[1])"
        } elseif ($targetRelative -match "^history/cache/\.pytest_cache/(.+)$") {
            $sourceRelative = ".pytest_cache/$($Matches[1])"
        }
        if ($sourceRelative) {
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
            $allRecords.Add([pscustomobject]@{
                source = $sourceRelative
                destination = $targetRelative
                bytes = [int64]$file.Length
                sha256 = $hash
            })
        }
    }
} else {
foreach ($entry in $plan) {
    $before = @(Get-FileRecords $entry.source)
    $destinationParent = Split-Path -Parent $entry.destination
    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    Move-Item -LiteralPath $entry.source -Destination $entry.destination
    foreach ($record in $before) {
        $suffix = $record.path.Substring($entry.source_relative.Length).TrimStart("/")
        $targetRelative = $entry.destination_relative
        if ($suffix) {
            $targetRelative = "$targetRelative/$suffix"
        }
        $target = Join-Path $rootPath ($targetRelative -replace "/", "\")
        if (-not (Test-Path -LiteralPath $target)) {
            throw "Moved file is missing: $targetRelative"
        }
        $after = Get-FileHash -Algorithm SHA256 -LiteralPath $target
        if ($after.Hash.ToLowerInvariant() -ne $record.sha256) {
            throw "SHA-256 changed during move: $targetRelative"
        }
        $allRecords.Add([pscustomobject]@{
            source = $record.path
            destination = $targetRelative
            bytes = $record.bytes
            sha256 = $record.sha256
        })
    }
}
}

$movedItems = New-Object 'System.Collections.Generic.List[object]'
if ($recovered) {
    foreach ($record in $allRecords.ToArray()) {
        $item = $null
        if ($record.source -match "^(docs/phase-[^/]+)") {
            $item = $Matches[1]
        } elseif ($record.source -match "^(deploy/[^/]+)") {
            $item = $Matches[1]
        } elseif ($record.source -match "^(runtime/[^/]+)") {
            $item = $Matches[1]
        } elseif ($record.source -match "^(archive|official-attachments|runtime-probe)(?:/|$)") {
            $item = $Matches[1]
        } elseif ($record.source -match "^(__pycache__|\.pytest_cache)(?:/|$)") {
            $item = "cache/$($Matches[1])"
        }
        if ($item -and -not @($movedItems.ToArray() | Where-Object source -eq $item)) {
            $destination = if ($item -match "^(archive|official-attachments|runtime-probe|docs/|deploy/|runtime/|cache/)") { "history/$item" } else { $item }
            $movedItems.Add([pscustomobject]@{ source = $item; destination = $destination })
        }
    }
} else {
    foreach ($entry in $plan.ToArray()) {
        $movedItems.Add([pscustomobject]@{ source = $entry.source_relative; destination = $entry.destination_relative })
    }
}

$log = [pscustomobject]@{
    schema_version = "history-migration-v1"
    started_at = $startedAt
    completed_at = [DateTime]::UtcNow.ToString("o")
    root = $rootPath
    history_root = "history"
    recovered_from_existing_history = $recovered
    moved_item_count = $movedItems.Count
    moved_file_count = $allRecords.Count
    moved_bytes = [int64](($allRecords | Measure-Object -Property bytes -Sum).Sum)
    moved_items = @($movedItems.ToArray())
    files = @($allRecords.ToArray())
    intentionally_not_moved = @(
        "runtime/job_hub.sqlite3 and SQLite sidecars: current local runtime database",
        "runtime/captures, runtime/official-attachments, runtime/reports and runtime/verified: retained for current local audit/runtime use",
        ".worktrees and external Git worktrees: moving them would invalidate Git worktree metadata",
        "current code, data, tests, deploy candidates, docs/current and controlled backups"
    )
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $logPath) | Out-Null
$log | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $logPath -Encoding UTF8
Write-Output ("moved {0} items / {1} files to {2}" -f $movedItems.Count, $allRecords.Count, $historyPath)
