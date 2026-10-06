$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = "C:\Users\xuboy\plastic-waste-project"
$python = "C:\Users\xuboy\AppData\Local\Programs\Python\Python313\python.exe"
$routeDb = Join-Path $projectRoot "04_16_M2_route_layer\m2_route_layer.sqlite"

Set-Location $scriptDir

if (-not (Test-Path $routeDb)) {
    Write-Host "Route provenance layer not found. Building from v3.1 classification..." -ForegroundColor Cyan
    & $python (Join-Path $scriptDir "04_16_build_M2_route_layer_shared_KG_v3_1.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $python (Join-Path $scriptDir "05_14_waste_plastic_fcc_KG_M3_INTERACTION_FIX.py")
