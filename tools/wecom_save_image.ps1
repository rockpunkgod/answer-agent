param(
    [Parameter(Mandatory=$true)][long]$MainHandle,
    [Parameter(Mandatory=$true)][long]$PreviewHandle,
    [Parameter(Mandatory=$true)][string]$Destination
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class WeComSaveForeground {
 public delegate bool Callback(IntPtr h,IntPtr l);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h,Callback cb,IntPtr l);
 [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageTimeoutW")]
 public static extern IntPtr SetText(IntPtr h,uint m,IntPtr w,string text,uint flags,uint timeout,out UIntPtr result);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageTimeoutW")]
 public static extern IntPtr ReadText(IntPtr h,uint m,IntPtr w,StringBuilder text,uint flags,uint timeout,out UIntPtr result);
 [DllImport("user32.dll",EntryPoint="SendMessageTimeoutW")]
 public static extern IntPtr Send(IntPtr h,uint m,IntPtr w,IntPtr l,uint flags,uint timeout,out UIntPtr result);
}
'@

# This helper is internal to the local business CLI, never an arbitrary MCP tool.
# Keep all writes in the existing private attachment tree, with no overwrite.
$privateRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../data/private'))
$target=[IO.Path]::GetFullPath($Destination)
if(-not $target.StartsWith($privateRoot+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){
 throw 'SAVE_PATH_OUTSIDE_PRIVATE_ROOT'
}
$directory=Split-Path -Parent $target
if((Split-Path -Leaf $directory) -ne 'attachments' -or [IO.Path]::GetExtension($target) -ne '.jpg'){
 throw 'SAVE_ATTACHMENT_PATH_REQUIRED'
}
if(Test-Path -LiteralPath $target){throw 'SAVE_TARGET_ALREADY_EXISTS'}
if(-not (Test-Path -LiteralPath $directory -PathType Container)){throw 'SAVE_DIRECTORY_UNAVAILABLE'}
$ancestor=Get-Item -LiteralPath $directory
while($null -ne $ancestor){
 if(($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'SAVE_DIRECTORY_REDIRECTED'}
 $ancestor=$ancestor.Parent
}

$main=[System.Windows.Automation.AutomationElement]::FromHandle([intptr]$MainHandle)
$preview=[System.Windows.Automation.AutomationElement]::FromHandle([intptr]$PreviewHandle)
$pidValue=$main.Current.ProcessId
if((Get-Process -Id $pidValue).ProcessName -ne 'WXWork' -or $main.Current.ClassName -ne 'WeWorkWindow' -or
   $preview.Current.ClassName -ne 'ImagePreview' -or $preview.Current.ProcessId -ne $pidValue){
 throw 'SAVE_WINDOW_IDENTITY_CHANGED'
}
$condition=New-Object System.Windows.Automation.PropertyCondition(
 [System.Windows.Automation.AutomationElement]::ClassNameProperty,'#32770')
$saveTitle=-join @([char]0x53e6,[char]0x5b58,[char]0x4e3a)
$dialogs=@($preview.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition) |
 Where-Object {$_.Current.Name -eq $saveTitle -and $_.Current.ProcessId -eq $pidValue})
if($dialogs.Count -ne 1){throw 'SAVE_DIALOG_NOT_UNIQUE'}
$dialog=$dialogs[0]
function ConfirmForeground {
 if([WeComSaveForeground]::GetForegroundWindow().ToInt64() -ne $dialog.Current.NativeWindowHandle){
  throw 'SAVE_DIALOG_NOT_FOREGROUND'
 }
}
ConfirmForeground
$script:saveControls=New-Object System.Collections.ArrayList
$callback=[WeComSaveForeground+Callback]{param($handle,$unused)
 $class=New-Object Text.StringBuilder 128
 [void][WeComSaveForeground]::GetClassName($handle,$class,128)
 [void]$script:saveControls.Add(@{handle=$handle;id=[WeComSaveForeground]::GetDlgCtrlID($handle);class=$class.ToString()})
 return $true
}
[void][WeComSaveForeground]::EnumChildWindows([intptr]$dialog.Current.NativeWindowHandle,$callback,[intptr]::Zero)
$editors=@($script:saveControls | Where-Object {$_.id -eq 1001 -and $_.class -eq 'Edit'})
$buttons=@($script:saveControls | Where-Object {$_.id -eq 1 -and $_.class -eq 'Button'})
if($editors.Count -ne 1 -or $buttons.Count -ne 1){throw 'SAVE_CONTROL_NOT_UNIQUE'}
$editor=$editors[0].handle;$button=$buttons[0].handle
$result=[uintptr]::Zero
if([WeComSaveForeground]::SetText($editor,12,[intptr]::Zero,$target,2,2000,[ref]$result) -eq [intptr]::Zero){
 throw 'SAVE_FILENAME_SET_UNCONFIRMED'
}
ConfirmForeground
$readback=New-Object Text.StringBuilder 32768
if([WeComSaveForeground]::ReadText($editor,13,[intptr]32768,$readback,2,2000,[ref]$result) -eq [intptr]::Zero){
 throw 'SAVE_FILENAME_READ_UNCONFIRMED'
}
if($readback.ToString() -ne $target){throw 'SAVE_FILENAME_READBACK_MISMATCH'}
if(Test-Path -LiteralPath $target){throw 'SAVE_TARGET_ALREADY_EXISTS'}
if([WeComSaveForeground]::Send($button,245,[intptr]::Zero,[intptr]::Zero,2,2000,[ref]$result) -eq [intptr]::Zero){
 throw 'SAVE_CLICK_OUTCOME_UNCONFIRMED'
}
# Never repeat Invoke after a timeout or an unexpected confirmation dialog.
$deadline=[DateTime]::UtcNow.AddSeconds(10)
while(-not (Test-Path -LiteralPath $target) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100}
if(-not (Test-Path -LiteralPath $target -PathType Leaf)){throw 'SAVE_RESULT_UNCONFIRMED'}
@{status='FILE_CREATED_REQUIRES_CONTENT_CHECK';path=$target;new_save=$true;automatic_retry=$false} |
 ConvertTo-Json -Compress
