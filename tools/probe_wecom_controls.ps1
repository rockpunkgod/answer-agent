param([ValidateRange(1,12)][int]$MaxDepth=8,[ValidateRange(1,2000)][int]$MaxNodes=500,[switch]$IncludeOwnedHelpers)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$wecomTitle= -join @([char]0x4f01,[char]0x4e1a,[char]0x5fae,[char]0x4fe1)
$apps=@(Get-Process -Name WXWork -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -eq $wecomTitle })
if($apps.Count -ne 1){ @{status='UNAVAILABLE';reason='WECOM_MAIN_WINDOW_NOT_UNIQUE';window_count=$apps.Count} | ConvertTo-Json -Compress;exit 0 }
$app=$apps[0]
$allowedProcessIds=New-Object 'System.Collections.Generic.HashSet[int]'
[void]$allowedProcessIds.Add($app.Id)
if($IncludeOwnedHelpers){
 $installRoot=[IO.Path]::GetDirectoryName($app.Path).TrimEnd('\')+'\'
 $helpers=@(Get-CimInstance Win32_Process -Filter "Name='WXWorkWeb.exe'")
 for($generation=0;$generation -lt 8;$generation++){
  $added=$false
  foreach($helper in $helpers){
   if($allowedProcessIds.Contains([int]$helper.ParentProcessId) -and $helper.ExecutablePath -and
      $helper.ExecutablePath.StartsWith($installRoot,[StringComparison]::OrdinalIgnoreCase)){
    if($allowedProcessIds.Add([int]$helper.ProcessId)){$added=$true}
   }
  }
  if(-not $added){break}
 }
}
$skippedProcessIds=New-Object 'System.Collections.Generic.HashSet[int]'
$root=[System.Windows.Automation.AutomationElement]::FromHandle($app.MainWindowHandle)
$walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
$queue=New-Object System.Collections.Queue
$queue.Enqueue(@($root,0))
$nodes=New-Object System.Collections.ArrayList
$watch=[Diagnostics.Stopwatch]::StartNew()
$truncated=$false
while($queue.Count -gt 0){
 if($nodes.Count -ge $MaxNodes -or $watch.Elapsed.TotalSeconds -gt 8){$truncated=$true;break}
 $item=$queue.Dequeue();$element=$item[0];$depth=[int]$item[1]
 $current=$element.Current
 if(-not $allowedProcessIds.Contains($current.ProcessId)){[void]$skippedProcessIds.Add($current.ProcessId);continue}
 $rect=$current.BoundingRectangle
 $patterns=@($element.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName })
 [void]$nodes.Add(@{name=$current.Name;automation_id=$current.AutomationId;control_type=$current.ControlType.ProgrammaticName.Replace('ControlType.','');class_name=$current.ClassName;process_id=$current.ProcessId;depth=$depth;enabled=$current.IsEnabled;offscreen=$current.IsOffscreen;patterns=$patterns;bounds=@($rect.Left,$rect.Top,$rect.Width,$rect.Height)})
 if($depth -lt $MaxDepth){
  $child=$walker.GetFirstChild($element)
  while($null -ne $child){
   $queue.Enqueue(@($child,($depth+1)))
   if($queue.Count+$nodes.Count -ge $MaxNodes){$truncated=$true;break}
   $child=$walker.GetNextSibling($child)
  }
 }
}
@{status='OK';backend='WINDOWS_UIA_RAW_VIEW';window=@{handle=$app.MainWindowHandle.ToInt64();process_id=$app.Id;name=$app.MainWindowTitle;client_version=$app.MainModule.FileVersionInfo.FileVersion};truncated=$truncated;include_owned_helpers=[bool]$IncludeOwnedHelpers;allowed_process_ids=@($allowedProcessIds);skipped_process_ids=@($skippedProcessIds);controls=@($nodes.ToArray());observed_at=[DateTimeOffset]::UtcNow.ToString('o')} | ConvertTo-Json -Depth 8 -Compress
