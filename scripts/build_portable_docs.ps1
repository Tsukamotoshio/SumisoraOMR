param(
    [Parameter(Mandatory=$true)]
    [string]$Staging,
    [Parameter(Mandatory=$true)]
    [string]$Out
)

Copy-Item -Path .\README_EN.txt -Destination (Join-Path $Staging 'README.txt') -Force
Copy-Item -Path .\读我.txt -Destination $Staging -Force
Copy-Item -Path .\LICENSE -Destination $Staging -Force
Copy-Item -Path .\THIRD_PARTY_NOTICES.md -Destination $Staging -Force

# Compress-Archive buffers the whole archive in memory and is unreliable for
# large (~600 MB+) trees (it fails here with IOException on bundled python310.zip).
# ZipFile.CreateFromDirectory streams entries to disk and handles big trees.
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path $Out) { Remove-Item $Out -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    (Resolve-Path 'installer-dist\_staging').Path,
    $Out,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false)   # includeBaseDirectory=$false → zip root = the portable folder, matching the old _staging\* behavior
$sz = (Get-Item $Out).Length
Write-Host "Done: $([math]::Round($sz/1MB,1)) MB"
