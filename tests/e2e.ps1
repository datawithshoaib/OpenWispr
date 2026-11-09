# End-to-end test: runs OpenWispr with a WAV file standing in for the microphone,
# opens a throwaway text box, presses the real hotkey with synthesized key events,
# and checks the transcription lands in the text box.
#
#   powershell -ExecutionPolicy Bypass -File tests\e2e.ps1 -Wav speech.wav -Expect "words in it"
param(
    [Parameter(Mandatory)] [string]$Wav,
    [Parameter(Mandatory)] [string]$Expect,
    [string]$Exe,
    [switch]$Type
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Keys2 {
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    public static void Down(byte vk) { keybd_event(vk, 0, 0, UIntPtr.Zero); }
    public static void Up(byte vk) { keybd_event(vk, 0, 2, UIntPtr.Zero); }
}
"@
$LCTRL = 0xA2; $LWIN = 0x5B; $ESC = 0x1B

if (-not $Exe) { $Exe = Join-Path $PSScriptRoot "..\target\release\openwispr.exe" }
$Exe = (Resolve-Path $Exe).Path
$Wav = (Resolve-Path $Wav).Path
$log = Join-Path $env:TEMP "openwispr-e2e.log"
$runArgs = @("run", "--fake-mic", "`"$Wav`"", "--no-sound")
if ($Type) { $runArgs += "--type" }
$proc = Start-Process -FilePath $Exe -ArgumentList $runArgs -RedirectStandardError $log -PassThru -WindowStyle Hidden
$failures = @()
try {
    $deadline = (Get-Date).AddSeconds(30)
    while (-not ((Test-Path $log) -and (Get-Content $log -Raw) -match "Ready")) {
        if ((Get-Date) -gt $deadline -or $proc.HasExited) { throw "OpenWispr did not start:`n$(Get-Content $log -Raw)" }
        Start-Sleep -Milliseconds 200
    }

    [System.Windows.Forms.Clipboard]::SetText("ORIGINAL CLIPBOARD")
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "OpenWispr E2E"; $form.Width = 700; $form.Height = 300; $form.TopMost = $true
    $box = New-Object System.Windows.Forms.TextBox
    $box.Multiline = $true; $box.Dock = "Fill"; $box.Text = ">"
    $form.Controls.Add($box)
    $form.Show(); $form.Activate(); $box.Focus() | Out-Null; $box.SelectionStart = $box.Text.Length

    function Pump([int]$ms) {
        $end = (Get-Date).AddMilliseconds($ms)
        while ((Get-Date) -lt $end) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 15 }
    }
    function WaitForChange([string]$before, [int]$ms) {
        $end = (Get-Date).AddMilliseconds($ms)
        while ((Get-Date) -lt $end -and $box.Text -eq $before) { Pump 50 }
        Pump 400  # let the paste/typing finish
    }
    function Normalize([string]$s) { ($s.ToLower() -replace "[^a-z0-9 ]", "" -replace "\s+", " ").Trim() }
    Pump 300
    # Windows only lets the process that sent the last input take the foreground,
    # so tap an unassigned key first.
    [Keys2]::Down(0xE8); [Keys2]::Up(0xE8)
    [Keys2]::SetForegroundWindow($form.Handle) | Out-Null; $box.Focus() | Out-Null
    Pump 300
    if ([Keys2]::GetForegroundWindow() -ne $form.Handle) { throw "test window could not take focus" }

    # 1. Hold to talk: hold Ctrl+Win for 1.5 s, release.
    $before = $box.Text
    [Keys2]::Down($LCTRL); [Keys2]::Down($LWIN); Pump 1500; [Keys2]::Up($LWIN); [Keys2]::Up($LCTRL)
    WaitForChange $before 15000
    $got = $box.Text.Substring($before.Length)
    Write-Host "hold-to-talk inserted: '$got'"
    if ((Normalize $got) -notlike "*$(Normalize $Expect)*") { $failures += "hold-to-talk text mismatch" }
    if ([Keys2]::GetForegroundWindow() -ne $form.Handle) { $failures += "focus was stolen (Start menu opened?)" }
    if (-not $Type) {
        Pump 600
        $clip = [System.Windows.Forms.Clipboard]::GetText()
        if ($clip -ne "ORIGINAL CLIPBOARD") { $failures += "clipboard not restored (got '$clip')" }
    }

    # 2. Hands-free: tap to start, tap again to stop.
    $before = $box.Text
    [Keys2]::Down($LCTRL); [Keys2]::Down($LWIN); Pump 100; [Keys2]::Up($LWIN); [Keys2]::Up($LCTRL)
    Pump 1000
    if ($box.Text -ne $before) { $failures += "hands-free inserted text before being stopped" }
    [Keys2]::Down($LCTRL); [Keys2]::Down($LWIN); Pump 100; [Keys2]::Up($LWIN); [Keys2]::Up($LCTRL)
    WaitForChange $before 15000
    $got = $box.Text.Substring($before.Length)
    Write-Host "hands-free inserted:   '$got'"
    if ((Normalize $got) -notlike "*$(Normalize $Expect)*") { $failures += "hands-free text mismatch" }

    # 3. Cancel: tap to start hands-free, then Esc. Nothing should be inserted.
    $before = $box.Text
    [Keys2]::Down($LCTRL); [Keys2]::Down($LWIN); Pump 100; [Keys2]::Up($LWIN); [Keys2]::Up($LCTRL)
    Pump 500; [Keys2]::Down($ESC); [Keys2]::Up($ESC); Pump 2500
    if ($box.Text -ne $before) { $failures += "cancel still inserted text" }
    else { Write-Host "cancel: nothing inserted (correct)" }

    # 4. Plain typing is untouched by the hook.
    $before = $box.Text
    [Keys2]::Down(0x41); [Keys2]::Up(0x41); Pump 300
    if ($box.Text -ne "${before}a") { $failures += "normal typing was affected (added '$($box.Text.Substring([Math]::Min($before.Length, $box.Text.Length)))')" }
    else { Write-Host "normal typing: unaffected (correct)" }

    $form.Close()
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    Write-Host "--- app log ---"; Get-Content $log | Write-Host
}
if ($failures.Count) { Write-Host "FAIL: $($failures -join '; ')"; exit 1 }
Write-Host "PASS"
