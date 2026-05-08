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

Compress-Archive -Force -Path 'installer-dist\_staging\*' -DestinationPath $Out
$sz = (Get-Item $Out).Length
Write-Host "Done: $([math]::Round($sz/1MB,1)) MB"
