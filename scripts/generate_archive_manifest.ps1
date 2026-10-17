[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (Resolve-Path (Join-Path $scriptRoot "..")).Path
}
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $scriptRoot "..\docs\current\archive-manifest.json"
}
$rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd("\")
$outputPath = [IO.Path]::GetFullPath($Output)
$outputDirectory = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

function Get-RelativePath([string]$FullName) {
    $fullPath = [IO.Path]::GetFullPath($FullName)
    $prefix = $rootPath.TrimEnd("\") + "\"
    if ($fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        return $fullPath.Substring($prefix.Length).Replace("\", "/")
    }
    return $fullPath.Replace("\", "/")
}

function Get-Classification([string]$RelativePath, [string]$FileName) {
    $destination = $RelativePath
    $action = "review_before_action"
    $classification = "unclassified"
    $reason = "Needs operator review"

    if ($RelativePath -match "^docs/phase-[^/]+/") {
        $classification = "historical_phase_document"
        $destination = "docs/archive/$RelativePath"
        $action = "archive_after_approval"
        $reason = "Historical Phase document; retain as evidence"
    } elseif ($RelativePath -match "^docs/current/") {
        if ($FileName -match "\.(sqlite3?|db)$") {
            $classification = "misplaced_database_backup"
            $destination = "backups/$FileName"
            $action = "move_after_approval"
            $reason = "Database backup must not be mixed with current documentation"
        } else {
            $classification = "current_document"
            $destination = $RelativePath
            $action = "keep_current"
            $reason = "Current project policy or audit document"
        }
    } elseif ($RelativePath -match "^archive/") {
        $classification = "historical_archive"
        $destination = $RelativePath
        $action = "keep_archive"
        $reason = "Already under the historical archive boundary"
    } elseif ($RelativePath -match "^runtime/backups/") {
        $classification = "runtime_backup"
        $destination = "backups/$([IO.Path]::GetFileName($RelativePath))"
        $action = "move_after_approval"
        $reason = "Backups should have one controlled top-level location"
    } elseif ($RelativePath -match "^runtime/job_hub\.sqlite3(-.*)?$") {
        $classification = "current_runtime_database"
        $destination = "runtime/$([IO.Path]::GetFileName($RelativePath))"
        $action = "keep_runtime"
        $reason = "Current runtime database; never commit or put in an image"
    } elseif ($RelativePath -match "^runtime/") {
        if ($RelativePath -match "^runtime/(phase-|chrome-|cmgb-|local-target-|probe-|deploy/|.*-baseline|.*-snapshot)") {
            $classification = "historical_runtime_artifact"
            $destination = "archive/runtime/$([IO.Path]::GetFileName($RelativePath))"
            $action = "archive_after_approval"
            $reason = "Runtime artifact appears tied to a historical phase or probe"
        } else {
            $classification = "runtime_artifact"
            $destination = $RelativePath
            $action = "keep_runtime"
            $reason = "Current captures, caches, attachments, logs or health evidence"
        }
    } elseif ($RelativePath -match "^backups/") {
        $classification = "controlled_backup"
        $destination = $RelativePath
        $action = "keep_backup"
        $reason = "Controlled database backup location"
    } elseif ($RelativePath -match "^deploy/") {
        if ($FileName -match "phase|overlay|fast|runtime") {
            $classification = "legacy_deploy_file"
            $destination = "archive/deploy/$FileName"
            $action = "archive_after_approval"
            $reason = "Legacy phase or overlay deployment file"
        } else {
            $classification = "deploy_candidate"
            $destination = $RelativePath
            $action = "keep_deploy_pending_review"
            $reason = "Deployment file; canonical status still requires review"
        }
    } elseif ($RelativePath -match "^job_hub/") {
        $classification = "current_code_candidate"
        $destination = "app/$RelativePath"
        $action = "move_after_approval"
        $reason = "Current application code is still in the legacy job_hub root"
    } elseif ($RelativePath -match "^(data|tests|scripts|examples)/") {
        $classification = "current_project_material"
        $destination = $RelativePath
        $action = "keep_current"
        $reason = "Versioned project configuration, tests, tooling or examples"
    } elseif ($RelativePath -match "^\.worktrees/") {
        $classification = "worktree_artifact"
        $destination = "archive/worktrees/$([IO.Path]::GetFileName($RelativePath))"
        $action = "archive_after_approval"
        $reason = "Tool-managed worktree material, not a production input"
    } elseif ($RelativePath -match "^(__pycache__|.*\/__pycache__)/" -or $RelativePath -match "^\.pytest_cache/") {
        $classification = "local_generated_cache"
        $destination = "runtime/local-cache/$RelativePath"
        $action = "clean_after_approval"
        $reason = "Regenerable local test/interpreter cache"
    } elseif ($RelativePath -match "^(Dockerfile|docker-compose|\.dockerignore|\.env|\.release\.env|ARCHITECTURE\.md|DEPLOYMENT\.md|README\.md|pytest\.ini|requirements)") {
        $classification = "root_config_or_document"
        $destination = "review/$RelativePath"
        $action = "review_before_action"
        $reason = "Root-level configuration or document needs canonical placement review"
    } elseif ($RelativePath -match "^(official-attachments|runtime-probe)/") {
        $classification = "unclassified_runtime_or_reference"
        $destination = "review/$RelativePath"
        $action = "review_before_action"
        $reason = "Root-level runtime/reference material needs explicit owner"
    } else {
        $classification = "root_or_miscellaneous"
        $destination = "review/$RelativePath"
        $action = "review_before_action"
        $reason = "No automatic move is safe"
    }

    return [pscustomobject]@{
        classification = $classification
        proposed_destination = $destination
        proposed_action = $action
        reason = $reason
    }
}

$excluded = @()
foreach ($name in @(".git", ".venv", "history")) {
    $path = Join-Path $rootPath $name
    if (Test-Path -LiteralPath $path) {
        $excluded += [pscustomobject]@{
            path = $name
            reason = if ($name -eq "history") { "Frozen history archive excluded from current-project manifest" } else { "Tool-managed directory excluded from per-file hashing" }
        }
    }
}

$excludedNames = @(".git", ".venv", "history")
$files = New-Object 'System.Collections.Generic.List[System.IO.FileInfo]'
$directories = New-Object 'System.Collections.Generic.Stack[System.IO.DirectoryInfo]'
$directories.Push((Get-Item -LiteralPath $rootPath))
while ($directories.Count -gt 0) {
    $currentDirectory = $directories.Pop()
    foreach ($file in (Get-ChildItem -LiteralPath $currentDirectory.FullName -File -Force -ErrorAction SilentlyContinue)) {
        if ($file.FullName -ne $outputPath) {
            $files.Add($file)
        }
    }
    foreach ($directory in (Get-ChildItem -LiteralPath $currentDirectory.FullName -Directory -Force -ErrorAction SilentlyContinue)) {
        if ($excludedNames -notcontains $directory.Name) {
            $directories.Push($directory)
        }
    }
}

$records = foreach ($file in $files) {
    $relative = Get-RelativePath $file.FullName
    $classification = Get-Classification $relative $file.Name
    try {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        $hashError = $null
    } catch {
        $hash = $null
        $hashError = $_.Exception.Message
    }
    [pscustomobject]@{
        path = $relative
        bytes = [int64]$file.Length
        modified_at = $file.LastWriteTime.ToString("o")
        sha256 = $hash
        hash_error = $hashError
        classification = $classification.classification
        proposed_destination = $classification.proposed_destination
        proposed_action = $classification.proposed_action
        reason = $classification.reason
    }
}

$manifest = [pscustomobject]@{
    schema_version = "archive-manifest-v1"
    generated_at = [DateTime]::UtcNow.ToString("o")
    root = $rootPath
    scope = "All current regular files below project root except .git, .venv and history"
    excluded_directories = $excluded
    file_count = @($records).Count
    files = @($records)
}

$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $outputPath -Encoding UTF8
Write-Output ("wrote {0} files to {1}" -f $manifest.file_count, $outputPath)
