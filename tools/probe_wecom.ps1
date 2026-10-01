param([string]$Output = "data/private/wecom-window.png", [switch]$CaptureVisible)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WecomProbeNative {
 [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int max);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int max);
 public delegate bool EnumCallback(IntPtr h, IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h, EnumCallback cb, IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback cb, IntPtr l);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint processId);
}
'@
[void][WecomProbeNative]::SetProcessDPIAware()
$script:wecomProcessIds = @(Get-Process -Name WXWork | ForEach-Object { $_.Id })
$script:visibleWecomWindows = @()
$topLevelCallback = [WecomProbeNative+EnumCallback]{ param($candidate,$unused)
 $candidateProcessId = [uint32]0
 [void][WecomProbeNative]::GetWindowThreadProcessId($candidate,[ref]$candidateProcessId)
 if (($script:wecomProcessIds -contains $candidateProcessId) -and [WecomProbeNative]::IsWindowVisible($candidate)) {
  $bounds = New-Object WecomProbeNative+RECT
  [void][WecomProbeNative]::GetWindowRect($candidate,[ref]$bounds)
  $candidateWidth = $bounds.R-$bounds.L
  $candidateHeight = $bounds.B-$bounds.T
  if ($candidateWidth -gt 400 -and $candidateHeight -gt 300) {
   $script:visibleWecomWindows += [pscustomobject]@{ Handle=$candidate; Area=($candidateWidth*$candidateHeight) }
  }
 }
 return $true
}
[void][WecomProbeNative]::EnumWindows($topLevelCallback,[IntPtr]::Zero)
$selectedWindow = $script:visibleWecomWindows | Sort-Object Area -Descending | Select-Object -First 1
if ($null -eq $selectedWindow) { throw 'Visible WXWork window not found' }
$window = $selectedWindow.Handle
$rect = New-Object WecomProbeNative+RECT
[void][WecomProbeNative]::GetWindowRect($window, [ref]$rect)
$path = [IO.Path]::GetFullPath((Join-Path (Get-Location) $Output))
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
$bitmap = New-Object Drawing.Bitmap(($rect.R-$rect.L),($rect.B-$rect.T))
$graphics = [Drawing.Graphics]::FromImage($bitmap)
if ($CaptureVisible) {
 if ([WecomProbeNative]::IsIconic($window) -or [WecomProbeNative]::GetForegroundWindow() -ne $window) { $graphics.Dispose(); $bitmap.Dispose(); throw 'WXWork not foreground; refusing screen capture' }
 $graphics.CopyFromScreen($rect.L,$rect.T,0,0,$bitmap.Size)
 $captured=$true
 $graphics.Dispose()
} else {
 $dc = $graphics.GetHdc()
 try { $captured=[WecomProbeNative]::PrintWindow($window,$dc,2) } finally { $graphics.ReleaseHdc($dc); $graphics.Dispose() }
}
try { $bitmap.Save($path,[Drawing.Imaging.ImageFormat]::Png) } finally { $bitmap.Dispose() }
$script:children = @()
$callback = [WecomProbeNative+EnumCallback]{ param($handle,$unused)
 $class = New-Object Text.StringBuilder 256
 $name = New-Object Text.StringBuilder 512
 [void][WecomProbeNative]::GetClassName($handle,$class,256)
 [void][WecomProbeNative]::GetWindowText($handle,$name,512)
 $script:children += [pscustomobject]@{ Handle=$handle.ToInt64(); Class=$class.ToString(); TargetMatch=($name.ToString() -eq '苇中鹤'); TextLength=$name.Length }
 return $true
}
[void][WecomProbeNative]::EnumChildWindows($window,$callback,[IntPtr]::Zero)
[pscustomobject]@{ App='WXWork'; Handle=$window.ToInt64(); Minimized=[WecomProbeNative]::IsIconic($window); CaptureResult=$captured; ImagePath=$path; Children=$script:children } | ConvertTo-Json -Depth 4
