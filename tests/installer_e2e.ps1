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
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Save()
}

function Get-Link {
    param([string]$Path)
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
    [pscustomobject]@{ TargetPath = $shortcut.TargetPath; Arguments = $shortcut.Arguments }
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
Set-Content -LiteralPath (Join-Path $payload "OpenWispr.exe") -Value "fake app"

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

$repoDir = Join-Path $work "repo"
$pythonw = Join-Path $repoDir ".venv\Scripts\pythonw.exe"
New-Item -ItemType Directory -Path (Split-Path -Parent $pythonw) -Force | Out-Null
Set-Content -LiteralPath $pythonw -Value "fake pythonw"
$desktopLegacy = Join-Path $desktop "OpenWispr.lnk"
$startupLegacy = Join-Path $startup "OpenWispr.lnk"
$unrelated = Join-Path $desktop "Other App.lnk"
New-Link $desktopLegacy $pythonw "-m openwispr" $repoDir
New-Link $startupLegacy $pythonw "-m openwispr" $repoDir
New-Link $unrelated $pythonw "-m other_app" $repoDir

Invoke-Installer "uninstall" $common

Assert-True (-not (Test-Path -LiteralPath $installDir)) "installed files were not removed"
Assert-True (-not (Test-Path -LiteralPath $entry)) "Start Menu entry was not removed"
foreach ($name in @($dictationModel, $voiceModel)) {
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $modelsDir $name))) "model $name was not removed"
}
Assert-True (-not (Test-Path -LiteralPath $desktopLegacy)) "legacy Desktop shortcut was not removed"
Assert-True (-not (Test-Path -LiteralPath $startupLegacy)) "legacy Startup shortcut was not removed"
Assert-True (Test-Path -LiteralPath $unrelated) "unrelated shortcut was removed"
Assert-True ((Get-Link $unrelated).Arguments -eq "-m other_app") "unrelated shortcut was changed"
Write-Host "PASS"
