param(
    [string]$ArchiveDate = "2026-10-16"
)

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$archive = Join-Path $root (Join-Path "archive\workspace-artifacts" $ArchiveDate)
New-Item -ItemType Directory -Force -Path $archive | Out-Null
$manifest = Join-Path $archive "moved-files.txt"
if (Test-Path -LiteralPath $manifest) { Remove-Item -LiteralPath $manifest -Force }

function Move-Artifact([string]$Path, [string]$RelativeDestination) {
    $destinationDirectory = Join-Path $archive $RelativeDestination
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    $destination = Join-Path $destinationDirectory (Split-Path -Leaf $Path)
    Move-Item -LiteralPath $Path -Destination $destination -Force
    $relative = $Path.Substring($root.Length).TrimStart('\')
    Add-Content -LiteralPath $manifest -Value "$relative`t$($destination.Substring($root.Length).TrimStart('\'))"
}

Get-ChildItem -LiteralPath $root -Force | Where-Object {
    $_.Name -like '.tmp*' -or $_.Name -like 'tmp_*' -or $_.Name -eq 'phase500-src.tar.gz'
} | ForEach-Object { Move-Artifact $_.FullName 'root-temporary' }

$deploy = Join-Path $root 'deploy'
if (Test-Path -LiteralPath $deploy) {
    Get-ChildItem -LiteralPath $deploy -Force -File | Where-Object {
        $_.Name -like 'docker-compose.phase*.yml' -or $_.Name -like '*overlay*'
    } | ForEach-Object { Move-Artifact $_.FullName 'deploy-overlays' }
}

Get-ChildItem -LiteralPath $root -Force -File | Where-Object {
    $_.Name -like 'docker-compose.phase*.yml' -or $_.Name -like 'Dockerfile.phase*'
} | ForEach-Object { Move-Artifact $_.FullName 'root-overlays' }

Write-Output "Archived temporary workspace artifacts under $archive"
Write-Output "Manifest: $manifest"
