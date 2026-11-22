param(
    [Parameter(Mandatory)] [string]$Python,
    [Parameter(Mandatory)] [string]$Root
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function New-Link {
    param([string]$Path, [string]$Target, [string]$Arguments, [string]$WorkingDirectory)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    if (-not (Test-Path -LiteralPath $WorkingDirectory)) { New-Item -ItemType Directory -Path $WorkingDirectory -Force | Out-Null }
    & $Python -m openwispr.shortcut create $Path $Target --arguments=$Arguments --working-directory=$WorkingDirectory
    if ($LASTEXITCODE -ne 0) { throw "shortcut create failed with exit code $LASTEXITCODE" }
}

function Get-Link {
    param([string]$Path)
    $json = & $Python -m openwispr.shortcut read $Path
    if ($LASTEXITCODE -ne 0) { throw "shortcut read failed with exit code $LASTEXITCODE" }
    return ($json | ConvertFrom-Json)
}

function Invoke-Installer {
    param([string]$Command, [string[]]$Common)
    & $Python -m openwispr.installer $Command @Common
    if ($LASTEXITCODE -ne 0) { throw "installer $Command failed with exit code $LASTEXITCODE" }
}

$work = Join-Path $Root "installer-e2e"
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
$payload = Join-Path $work "payload"
$payloadModels = Join-Path $payload "models"
New-Item -ItemType Directory -Path $payloadModels -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "fake_installed_app.py") -Destination (Join-Path $payload "OpenWispr.exe")

$dictationModel = (& $Python -c "from openwispr import model; print(model.file_name(model.DEFAULT_MODEL))").Trim()
$voiceModel = (& $Python -c "from openwispr import model; print(model.VAD_FILE)").Trim()
foreach ($name in @($dictationModel, $voiceModel)) {
    $bytes = New-Object byte[] 100004
    $bytes[0] = 108
    $bytes[1] = 109
    $bytes[2] = 103
    $bytes[3] = 103
    [IO.File]::WriteAllBytes((Join-Path $payloadModels $name), $bytes)
}

$installDir = Join-Path $work "install"
$startMenu = Join-Path $work "start menu"
$modelsDir = Join-Path $work "user models"
$desktop = Join-Path $work "desktop"
$startup = Join-Path $work "startup"
$common = @(
    "--install-dir", $installDir,
    "--start-menu-dir", $startMenu,
    "--models-dir", $modelsDir,
    "--desktop-dir", $desktop,
    "--startup-dir", $startup,
    "--payload-dir", $payload
)

Invoke-Installer "install" $common

$entry = Join-Path $startMenu "OpenWispr.lnk"
Assert-True (Test-Path -LiteralPath $entry) "Start Menu entry was not created"
$installedExe = Join-Path $installDir "OpenWispr.exe"
Assert-True ((Get-Link $entry).TargetPath -eq $installedExe) "Start Menu entry does not open the installed app"
Assert-True (-not (Test-Path -LiteralPath (Join-Path $desktop "OpenWispr.lnk"))) "Desktop shortcut was created"
Assert-True (-not (Test-Path -LiteralPath (Join-Path $startup "OpenWispr.lnk"))) "Startup shortcut was created"
foreach ($name in @($dictationModel, $voiceModel)) {
    $installedModel = Join-Path $modelsDir $name
    Assert-True (Test-Path -LiteralPath $installedModel) "model $name was not bundled: $installedModel"
    Assert-True ((Get-Item -LiteralPath $installedModel).Length -eq 100004) "model $name was not copied intact"
}

$stateDir = Join-Path $work "instance-state"
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$stopFile = Join-Path $stateDir "stop"
$first = Start-Process -FilePath $Python -ArgumentList @("`"$installedExe`"", "--state-dir", "`"$stateDir`"") -PassThru -WindowStyle Hidden
try {
    $instanceFile = Join-Path $stateDir "instance.json"
    $deadline = (Get-Date).AddSeconds(10)
    while (-not (Test-Path -LiteralPath $instanceFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 50 }
    Assert-True (Test-Path -LiteralPath $instanceFile) "installed app did not start"
    $instance = Get-Content -LiteralPath $instanceFile -Raw | ConvertFrom-Json
    & $Python $installedExe --state-dir $stateDir
    if ($LASTEXITCODE -ne 0) { throw "second launch failed with exit code $LASTEXITCODE" }
    $secondFile = Join-Path $stateDir "second.json"
    Assert-True (Test-Path -LiteralPath $secondFile) "second launch did not report focus"
    $second = Get-Content -LiteralPath $secondFile -Raw | ConvertFrom-Json
    Assert-True ($second.state -eq "focused") "second launch did not focus the existing Instance"
    Assert-True ($second.pid -ne $instance.pid) "second launch reused the same process"
    Assert-True (-not $first.HasExited) "second launch ended the running Instance"
    $stillRunning = Get-Content -LiteralPath $instanceFile -Raw | ConvertFrom-Json
    Assert-True ($stillRunning.pid -eq $instance.pid) "second launch created another Instance"
}
finally {
    Set-Content -LiteralPath $stopFile -Value "stop"
    $first.WaitForExit(5000) | Out-Null
}

$repoDir = Join-Path $work "repo"
$pythonw = Join-Path $repoDir ".venv\Scripts\pythonw.exe"
New-Item -ItemType Directory -Path (Split-Path -Parent $pythonw) -Force | Out-Null
Set-Content -LiteralPath $pythonw -Value "fake pythonw"
$desktopLegacy = Join-Path $desktop "OpenWispr.lnk"
$startupLegacy = Join-Path $startup "OpenWispr.lnk"
$emptyArgsLegacy = Join-Path $startup "OpenWispr Empty Args.lnk"
$unrelated = Join-Path $desktop "Other App.lnk"
New-Link $desktopLegacy $pythonw "-m openwispr" $repoDir
New-Link $startupLegacy $pythonw "-m openwispr" $repoDir
New-Link $emptyArgsLegacy $pythonw "" $repoDir
New-Link $unrelated $pythonw "-m other_app" $repoDir

Invoke-Installer "uninstall" $common

Assert-True (-not (Test-Path -LiteralPath $installDir)) "installed files were not removed"
Assert-True (-not (Test-Path -LiteralPath $entry)) "Start Menu entry was not removed"
foreach ($name in @($dictationModel, $voiceModel)) {
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $modelsDir $name))) "model $name was not removed"
}
Assert-True (-not (Test-Path -LiteralPath $desktopLegacy)) "legacy Desktop shortcut was not removed"
Assert-True (-not (Test-Path -LiteralPath $startupLegacy)) "legacy Startup shortcut was not removed"
Assert-True (-not (Test-Path -LiteralPath $emptyArgsLegacy)) "legacy shortcut with empty arguments was not removed"
Assert-True (Test-Path -LiteralPath $unrelated) "unrelated shortcut was removed"
Assert-True ((Get-Link $unrelated).Arguments -eq "-m other_app") "unrelated shortcut was changed"
Write-Host "PASS"
