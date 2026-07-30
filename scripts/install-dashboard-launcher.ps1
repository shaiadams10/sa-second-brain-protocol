param(
    [Parameter(Mandatory = $true)]
    [string]$VaultRoot,
    [string]$LauncherName = "Second Brain",
    [ValidatePattern("^[A-Za-z0-9]{1,3}$")]
    [string]$Initials = "SB",
    [string]$BatPath = ""
)

$ErrorActionPreference = "Stop"
$VaultRoot = (Resolve-Path -LiteralPath $VaultRoot).Path.TrimEnd("\")
$batPath = $BatPath
if (-not $batPath) {
    $batPath = @(
        (Join-Path $VaultRoot "Start SA Second Brain.bat"),
        (Join-Path $VaultRoot "Start Second Brain.bat"),
        (Join-Path $VaultRoot "start-dashboard.bat")
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $batPath -or -not (Test-Path -LiteralPath $batPath -PathType Leaf)) {
    throw "The dashboard BAT launcher was not found."
}

$assetDirectory = Join-Path $VaultRoot "System\Assets"
$iconPath = Join-Path $assetDirectory "SA-Second-Brain-Pink.ico"
$previewPath = Join-Path $assetDirectory "SA-Second-Brain-Pink.png"
New-Item -ItemType Directory -Path $assetDirectory -Force | Out-Null

Add-Type -AssemblyName System.Drawing
if (-not ("SecondBrainNativeMethods" -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class SecondBrainNativeMethods {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern bool DestroyIcon(IntPtr handle);

    [DllImport("shell32.dll")]
    public static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
"@
}

$bitmap = New-Object System.Drawing.Bitmap 256, 256, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

$ink = [System.Drawing.ColorTranslator]::FromHtml("#17151D")
$paper = [System.Drawing.ColorTranslator]::FromHtml("#FFFDF7")
$purple = [System.Drawing.ColorTranslator]::FromHtml("#8C7BD0")
$purpleDeep = [System.Drawing.ColorTranslator]::FromHtml("#322B5B")
$pink = [System.Drawing.ColorTranslator]::FromHtml("#ED3F91")

$graphics.Clear($pink)
$panelBrush = New-Object System.Drawing.SolidBrush $ink
$graphics.FillRectangle($panelBrush, 24, 24, 207, 207)
$framePen = New-Object System.Drawing.Pen $paper, 6
$graphics.DrawRectangle($framePen, 14, 14, 227, 227)

$brainBrush = New-Object System.Drawing.SolidBrush $purple
$brainPen = New-Object System.Drawing.Pen $purpleDeep, 7
$lobes = @(
    @(46, 42, 77, 76),
    @(91, 31, 78, 84),
    @(139, 43, 72, 78),
    @(35, 88, 88, 83),
    @(86, 79, 86, 94),
    @(139, 91, 81, 83),
    @(58, 137, 88, 68),
    @(119, 137, 83, 69)
)
foreach ($lobe in $lobes) {
    $graphics.FillEllipse($brainBrush, $lobe[0], $lobe[1], $lobe[2], $lobe[3])
    $graphics.DrawEllipse($brainPen, $lobe[0], $lobe[1], $lobe[2], $lobe[3])
}
$graphics.FillRectangle($brainBrush, 105, 165, 47, 54)
$graphics.DrawRectangle($brainPen, 105, 165, 47, 54)

$badgeBrush = New-Object System.Drawing.SolidBrush $ink
$badgePen = New-Object System.Drawing.Pen $pink, 6
$graphics.FillRectangle($badgeBrush, 51, 91, 154, 82)
$graphics.DrawRectangle($badgePen, 51, 91, 154, 82)

$font = New-Object System.Drawing.Font "Arial", 58, ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
$textBrush = New-Object System.Drawing.SolidBrush $paper
$format = New-Object System.Drawing.StringFormat
$format.Alignment = [System.Drawing.StringAlignment]::Center
$format.LineAlignment = [System.Drawing.StringAlignment]::Center
$graphics.DrawString($Initials.ToUpperInvariant(), $font, $textBrush, ([System.Drawing.RectangleF]::new(51, 91, 154, 82)), $format)

$nodeBrush = New-Object System.Drawing.SolidBrush $paper
$graphics.FillRectangle($nodeBrush, 31, 31, 15, 15)
$graphics.FillRectangle($nodeBrush, 210, 31, 15, 15)
$graphics.FillRectangle($nodeBrush, 31, 210, 15, 15)
$graphics.FillRectangle($nodeBrush, 210, 210, 15, 15)

$bitmap.Save($previewPath, [System.Drawing.Imaging.ImageFormat]::Png)
$handle = $bitmap.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($handle)
$iconCopy = $icon.Clone()
$stream = [System.IO.File]::Open($iconPath, [System.IO.FileMode]::Create)
try {
    $iconCopy.Save($stream)
}
finally {
    $stream.Dispose()
    $iconCopy.Dispose()
    $icon.Dispose()
    [SecondBrainNativeMethods]::DestroyIcon($handle) | Out-Null
    $format.Dispose()
    $textBrush.Dispose()
    $font.Dispose()
    $badgePen.Dispose()
    $badgeBrush.Dispose()
    $brainPen.Dispose()
    $brainBrush.Dispose()
    $framePen.Dispose()
    $panelBrush.Dispose()
    $nodeBrush.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()
}

$shell = New-Object -ComObject WScript.Shell
function Write-SecondBrainShortcut([string]$Path) {
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $batPath
    $shortcut.WorkingDirectory = $VaultRoot
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Description = "Open the private SA Second Brain dashboard"
    $shortcut.Save()
}

$desktop = [Environment]::GetFolderPath("Desktop")
$programs = [Environment]::GetFolderPath("Programs")
$desktopShortcut = Join-Path $desktop "$LauncherName.lnk"
$startMenuShortcut = Join-Path $programs "$LauncherName.lnk"

$legacyShortcut = Join-Path $desktop "YOUR_NAME Second Brain.lnk"
if ((Test-Path -LiteralPath $legacyShortcut) -and $legacyShortcut -ne $desktopShortcut) {
    $legacy = $shell.CreateShortcut($legacyShortcut)
    if ($legacy.Description -eq "Open the private local second-brain dashboard" -or $legacy.Arguments -like "*open-dashboard.ps1*") {
        Remove-Item -LiteralPath $legacyShortcut -Force
    }
}

Write-SecondBrainShortcut $desktopShortcut
Write-SecondBrainShortcut $startMenuShortcut
[SecondBrainNativeMethods]::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)

[pscustomobject]@{
    Icon = $iconPath
    Preview = $previewPath
    DesktopShortcut = $desktopShortcut
    StartMenuShortcut = $startMenuShortcut
} | ConvertTo-Json -Compress
