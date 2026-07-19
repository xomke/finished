param(
    [Parameter(Mandatory = $true)][int]$WaitPid,
    [Parameter(Mandatory = $true)][string]$Blender,
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$Package,
    [Parameter(Mandatory = $true)][string]$ResultFile,
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"

function Write-Result([string]$Status) {
    $temporary = "$ResultFile.part"
    $json = "{`"schema_version`":1,`"status`":`"$Status`"}"
    try {
        [System.IO.File]::WriteAllText($temporary, $json, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $ResultFile -Force
    } catch {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

try {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
        if ((Get-Date) -ge $deadline) {
            throw "Timed out waiting for Blender to close."
        }
        Start-Sleep -Milliseconds 500
    }
    & $Blender --command extension install-file --repo $Repository --enable $Package
    if ($LASTEXITCODE -eq 0) {
        Write-Result "installed"
        exit 0
    }
} catch {
    # The receipt is intentionally the only user-visible result; do not write paths or secrets.
}

Write-Result "failed"
exit 1
