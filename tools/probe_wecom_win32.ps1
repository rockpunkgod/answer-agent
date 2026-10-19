$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class WeComControlRead {
 public delegate bool Callback(IntPtr h,IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h,Callback cb,IntPtr l);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,StringBuilder b,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder b,int n);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("oleacc.dll")] public static extern int AccessibleObjectFromWindow(IntPtr h,uint id,ref Guid iid,[MarshalAs(UnmanagedType.Interface)]out object result);
}
'@
$title= -join @([char]0x4f01,[char]0x4e1a,[char]0x5fae,[char]0x4fe1)
$apps=@(Get-Process -Name WXWork | Where-Object {$_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -eq $title})
if($apps.Count -ne 1){throw 'WECOM_MAIN_WINDOW_NOT_UNIQUE'}
$app=$apps[0]
$script:nodes=New-Object System.Collections.ArrayList
$callback=[WeComControlRead+Callback]{param($handle,$unused)
 $cls=New-Object Text.StringBuilder 256;$text=New-Object Text.StringBuilder 512
 [void][WeComControlRead]::GetClassName($handle,$cls,256)
 [void][WeComControlRead]::GetWindowText($handle,$text,512)
 [void]$script:nodes.Add(@{handle=$handle.ToInt64();class_name=$cls.ToString();text_length=$text.Length;visible=[WeComControlRead]::IsWindowVisible($handle)})
 return ($script:nodes.Count -lt 1000)
}
[void][WeComControlRead]::EnumChildWindows($app.MainWindowHandle,$callback,[IntPtr]::Zero)
$iid=New-Object Guid '618736e0-3c3d-11cf-810c-00aa00389b71'
$accessible=$null
$hr=[WeComControlRead]::AccessibleObjectFromWindow($app.MainWindowHandle,[uint32]4294967292,[ref]$iid,[ref]$accessible)
$msaa=@{hresult=$hr;available=($hr -eq 0 -and $null -ne $accessible)}
if($msaa.available){
 try{$msaa.child_count=$accessible.accChildCount}catch{$msaa.child_count=$null;$msaa.error='MSAA_CHILDREN_UNAVAILABLE'}
}
@{status='OK';window_handle=$app.MainWindowHandle.ToInt64();process_id=$app.Id;controls=@($script:nodes.ToArray());msaa=$msaa;read_only=$true} | ConvertTo-Json -Depth 6 -Compress
