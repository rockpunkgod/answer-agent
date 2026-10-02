"""Fixed, read-only foreground probe executed by official Windows-MCP PowerShell.

No window switching, keyboard input, clipboard access or arbitrary script input.
"""
import json
import re


# Used only by the local screen-2 activation entry, never as a free-form tool.
# Coordinates are validated integers before substitution. All native calls are
# reads; the actual single click remains an official Windows-MCP call.
_SCREEN2_CAPTION_COMMAND = r'''$ErrorActionPreference='Stop'
if (-not ('HelpdeskScreen2CaptionProbe' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class HelpdeskScreen2CaptionProbe {
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X,Y; }
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left,Top,Right,Bottom; }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] public struct MonitorInfo {
  public int Size; public Rect Monitor,Work; public uint Flags;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string Device;
 }
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr window,uint flags);
 [DllImport("user32.dll")] public static extern IntPtr MonitorFromPoint(Point point,uint flags);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="GetMonitorInfoW")]
 public static extern bool GetMonitorInfo(IntPtr monitor,ref MonitorInfo info);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window,out Rect rect);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr window);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
 public delegate bool EnumWindowProc(IntPtr window,IntPtr parameter);
 [DllImport("user32.dll",SetLastError=true)] public static extern bool EnumWindows(EnumWindowProc callback,IntPtr parameter);
 [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr window,uint command);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="GetWindowTextW")]
 private static extern int GetWindowText(IntPtr window,StringBuilder text,int limit);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="GetClassNameW")]
 private static extern int GetClassName(IntPtr window,StringBuilder text,int limit);
 public static string WindowTitle(IntPtr window) { var text=new StringBuilder(512); GetWindowText(window,text,512); return text.ToString(); }
 public static string WindowClass(IntPtr window) { var text=new StringBuilder(256); GetClassName(window,text,256); return text.ToString(); }
 public static IntPtr[] ApplicationRoots(uint[] processIds) {
  var ids=new HashSet<uint>(processIds); var windows=new List<IntPtr>();
  if (!EnumWindows((window,parameter)=> {
   uint pid; GetWindowThreadProcessId(window,out pid);
   if (ids.Contains(pid) && GetWindow(window,4)==IntPtr.Zero && IsWindowVisible(window) && !IsIconic(window)) windows.Add(window);
   return true;
  },IntPtr.Zero)) throw new InvalidOperationException("WECOM_WINDOW_ENUMERATION_UNAVAILABLE");
  return windows.ToArray();
 }
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr SendMessageTimeout(
  IntPtr window,uint message,UIntPtr wparam,IntPtr lparam,uint flags,uint timeout,out UIntPtr result);
}
'@
}
$taskDpiContext=[HelpdeskScreen2CaptionProbe]::SetThreadDpiAwarenessContext([IntPtr](-4))
if ($taskDpiContext -eq [IntPtr]::Zero) { throw 'DPI_CONTEXT_UNAVAILABLE' }
try {
 $taskPoint=New-Object HelpdeskScreen2CaptionProbe+Point
 $taskPoint.X=__X__; $taskPoint.Y=__Y__
 $taskMonitor=[HelpdeskScreen2CaptionProbe]::MonitorFromPoint($taskPoint,0)
 $taskInfo=New-Object HelpdeskScreen2CaptionProbe+MonitorInfo
 $taskInfo.Size=[Runtime.InteropServices.Marshal]::SizeOf($taskInfo)
 if (-not [HelpdeskScreen2CaptionProbe]::GetMonitorInfo($taskMonitor,[ref]$taskInfo)) { throw 'MONITOR_UNAVAILABLE' }
 if ($taskInfo.Device -cne '\\.\DISPLAY2') { throw 'OUTSIDE_SCREEN2' }
 $taskWindow=[HelpdeskScreen2CaptionProbe]::GetAncestor([HelpdeskScreen2CaptionProbe]::WindowFromPoint($taskPoint),2)
 [uint32]$taskTargetPid=0
 [void][HelpdeskScreen2CaptionProbe]::GetWindowThreadProcessId($taskWindow,[ref]$taskTargetPid)
 $taskProcess=(Get-Process -Id $taskTargetPid -ErrorAction Stop).ProcessName
 if ($taskProcess -cne '__PROCESS__') { throw 'APP_NOT_AT_POINT' }
 $taskRect=New-Object HelpdeskScreen2CaptionProbe+Rect
 if (-not [HelpdeskScreen2CaptionProbe]::GetWindowRect($taskWindow,[ref]$taskRect)) { throw 'WINDOW_UNAVAILABLE' }
 [long]$taskPackedPoint=(([long]$taskPoint.Y -band 65535) -shl 16) -bor ([long]$taskPoint.X -band 65535)
 [UIntPtr]$taskHit=[UIntPtr]::Zero
 $taskHitCall=[HelpdeskScreen2CaptionProbe]::SendMessageTimeout($taskWindow,132,[UIntPtr]::Zero,[IntPtr]$taskPackedPoint,2,1000,[ref]$taskHit)
 if ($taskHitCall -eq [IntPtr]::Zero -or $taskHit.ToUInt64() -ne 2) { throw 'POINT_NOT_TITLE_BAR' }
 @{device=$taskInfo.Device;x=$taskPoint.X;y=$taskPoint.Y;process=$taskProcess;handle=$taskWindow.ToInt64();hit_test=[long]$taskHit.ToUInt64();
   screen_left=$taskInfo.Monitor.Left;screen_top=$taskInfo.Monitor.Top;screen_right=$taskInfo.Monitor.Right;screen_bottom=$taskInfo.Monitor.Bottom;
   window_left=$taskRect.Left;window_top=$taskRect.Top;window_right=$taskRect.Right;window_bottom=$taskRect.Bottom} | ConvertTo-Json -Compress
} finally { [void][HelpdeskScreen2CaptionProbe]::SetThreadDpiAwarenessContext($taskDpiContext) }
'''


def _display_device(display_device):
    if not isinstance(display_device, str) or not re.fullmatch(r'\\\\\.\\DISPLAY[1-9]\d{0,2}', display_device):
        raise ValueError('INVALID_DISPLAY_DEVICE')
    return display_device


def screen2_caption_command(loc, *, target_process='msedge', display_device=r'\\.\DISPLAY2'):
    _display_device(display_device)
    if target_process not in ('msedge', 'WXWork'):
        raise ValueError('INVALID_SCREEN2_APP')
    if (not isinstance(loc, list) or len(loc) != 2
            or any(type(value) is not int or not -32768 <= value <= 32767 for value in loc)):
        raise ValueError('INVALID_SCREEN2_POINT')
    return (_SCREEN2_CAPTION_COMMAND.replace('__X__', str(loc[0])).replace('__Y__', str(loc[1]))
            .replace('__PROCESS__', target_process).replace(r'\\.\DISPLAY2', display_device))


def parse_screen2_caption(record, loc, *, target_process='msedge', display_device=r'\\.\DISPLAY2'):
    screen2_caption_command(loc, target_process=target_process, display_device=display_device)
    if (record.get('tool') != 'PowerShell' or record.get('is_error') is not False
            or len(record.get('content', [])) != 1):
        raise ValueError('SCREEN2_PROBE_UNCONFIRMED')
    block = record['content'][0]
    raw = block.get('text', '')
    if (block.get('type') != 'text' or not isinstance(raw, str)
            or not raw.startswith('Response: ') or not raw.rstrip().endswith('Status Code: 0')):
        raise ValueError('SCREEN2_PROBE_UNCONFIRMED')
    value = json.loads(raw[len('Response: '):].rsplit('\nStatus Code:', 1)[0].strip())
    integers = {'x', 'y', 'handle', 'hit_test', 'screen_left', 'screen_top', 'screen_right',
                'screen_bottom', 'window_left', 'window_top', 'window_right', 'window_bottom'}
    if (not isinstance(value, dict) or set(value) != integers | {'device', 'process'}
            or any(type(value[key]) is not int for key in integers)
            or value['device'] != display_device or value['process'] != target_process
            or value['handle'] <= 0 or value['hit_test'] != 2 or [value['x'], value['y']] != loc):
        raise ValueError('SCREEN2_TARGET_UNCONFIRMED')
    for prefix in ('screen', 'window'):
        if not (value[f'{prefix}_left'] <= loc[0] < value[f'{prefix}_right']
                and value[f'{prefix}_top'] <= loc[1] < value[f'{prefix}_bottom']):
            raise ValueError('SCREEN2_POINT_OUTSIDE_TARGET')
    return value


# The main WeCom window exposes a native title/handle even when its chat UIA
# tree is empty. Locate a visible caption without model guesses or GUI inputs;
# the existing activation entry independently checks it again before clicking.
_WECOM_SCREEN2_WINDOW_QUERY = _SCREEN2_CAPTION_COMMAND.split('$taskDpiContext=', 1)[0] + r'''
$taskDpiContext=[HelpdeskScreen2CaptionProbe]::SetThreadDpiAwarenessContext([IntPtr](-4))
if ($taskDpiContext -eq [IntPtr]::Zero) { throw 'DPI_CONTEXT_UNAVAILABLE' }
try {
 $taskApps=@(Get-Process -Name WXWork -ErrorAction Stop)
 if ($taskApps.Count -gt 64) { throw 'WECOM_PROCESS_CANDIDATE_LIMIT' }
 $taskRoots=@([HelpdeskScreen2CaptionProbe]::ApplicationRoots([uint32[]]@($taskApps | ForEach-Object { $_.Id })))
 if ($taskRoots.Count -gt 16) { throw 'WECOM_MAIN_WINDOW_CANDIDATE_LIMIT' }
 $taskScreenWindows=@()
 $taskDiagnostics=@()
 foreach ($taskCandidate in $taskRoots) {
  if (-not [HelpdeskScreen2CaptionProbe]::IsWindowVisible($taskCandidate) -or [HelpdeskScreen2CaptionProbe]::IsIconic($taskCandidate)) { continue }
  $taskCandidateRect=New-Object HelpdeskScreen2CaptionProbe+Rect
  if (-not [HelpdeskScreen2CaptionProbe]::GetWindowRect($taskCandidate,[ref]$taskCandidateRect)) { continue }
  if ($taskCandidateRect.Right -le $taskCandidateRect.Left -or $taskCandidateRect.Bottom -le $taskCandidateRect.Top) { continue }
  $taskCenter=New-Object HelpdeskScreen2CaptionProbe+Point
  $taskCenter.X=$taskCandidateRect.Left+[int](($taskCandidateRect.Right-$taskCandidateRect.Left)/2)
  $taskCenter.Y=$taskCandidateRect.Top+[int](($taskCandidateRect.Bottom-$taskCandidateRect.Top)/2)
  $taskCandidateMonitor=[HelpdeskScreen2CaptionProbe]::MonitorFromPoint($taskCenter,0)
  $taskCandidateInfo=New-Object HelpdeskScreen2CaptionProbe+MonitorInfo
  $taskCandidateInfo.Size=[Runtime.InteropServices.Marshal]::SizeOf($taskCandidateInfo)
  if (-not [HelpdeskScreen2CaptionProbe]::GetMonitorInfo($taskCandidateMonitor,[ref]$taskCandidateInfo)) { continue }
  $taskTitle=[HelpdeskScreen2CaptionProbe]::WindowTitle($taskCandidate)
  $taskDiagnostics+=@{handle=$taskCandidate.ToInt64();title=$taskTitle;class=[HelpdeskScreen2CaptionProbe]::WindowClass($taskCandidate);
   device=$taskCandidateInfo.Device;left=$taskCandidateRect.Left;top=$taskCandidateRect.Top;right=$taskCandidateRect.Right;bottom=$taskCandidateRect.Bottom}
  if ($taskCandidateInfo.Device -cne '\\.\DISPLAY2' -or $taskTitle -cne '企业微信') { continue }
  if ($taskCandidateRect.Left -lt $taskCandidateInfo.Monitor.Left -or $taskCandidateRect.Top -lt $taskCandidateInfo.Monitor.Top -or $taskCandidateRect.Right -gt $taskCandidateInfo.Monitor.Right -or $taskCandidateRect.Bottom -gt $taskCandidateInfo.Monitor.Bottom) { continue }
  $taskScreenWindows+=@{Window=$taskCandidate;Rect=$taskCandidateRect;Info=$taskCandidateInfo}
 }
 if ($taskScreenWindows.Count -eq 0) { throw ('VISIBLE_SCREEN2_WECOM_MAIN_WINDOW_UNAVAILABLE: '+(ConvertTo-Json -InputObject @($taskDiagnostics) -Compress)) }
 if ($taskScreenWindows.Count -ne 1) { throw ('WECOM_SCREEN2_MAIN_WINDOW_AMBIGUOUS: '+(ConvertTo-Json -InputObject @($taskDiagnostics) -Compress)) }
 $taskWindow=$taskScreenWindows[0].Window
 $taskRect=$taskScreenWindows[0].Rect
'''

WECOM_SCREEN2_WINDOW_COMMAND = _WECOM_SCREEN2_WINDOW_QUERY + r'''
 $taskInfo=$taskScreenWindows[0].Info
 @{device=$taskInfo.Device;process='WXWork';title='企业微信';handle=$taskWindow.ToInt64();
  screen_left=$taskInfo.Monitor.Left;screen_top=$taskInfo.Monitor.Top;screen_right=$taskInfo.Monitor.Right;screen_bottom=$taskInfo.Monitor.Bottom;
  window_left=$taskRect.Left;window_top=$taskRect.Top;window_right=$taskRect.Right;window_bottom=$taskRect.Bottom} | ConvertTo-Json -Compress
} finally { [void][HelpdeskScreen2CaptionProbe]::SetThreadDpiAwarenessContext($taskDpiContext) }
'''

WECOM_SCREEN2_CAPTION_COMMAND = _WECOM_SCREEN2_WINDOW_QUERY + r'''
 $taskFound=$null
 foreach ($taskOffset in @(8,16,24,32,40)) {
  foreach ($taskFraction in @(0.4,0.5,0.6)) {
   $taskPoint=New-Object HelpdeskScreen2CaptionProbe+Point
   $taskPoint.X=$taskRect.Left+[int](($taskRect.Right-$taskRect.Left)*$taskFraction)
   $taskPoint.Y=$taskRect.Top+$taskOffset
   if ([HelpdeskScreen2CaptionProbe]::GetAncestor([HelpdeskScreen2CaptionProbe]::WindowFromPoint($taskPoint),2) -ne $taskWindow) { continue }
   $taskMonitor=[HelpdeskScreen2CaptionProbe]::MonitorFromPoint($taskPoint,0)
   $taskInfo=New-Object HelpdeskScreen2CaptionProbe+MonitorInfo
   $taskInfo.Size=[Runtime.InteropServices.Marshal]::SizeOf($taskInfo)
   if (-not [HelpdeskScreen2CaptionProbe]::GetMonitorInfo($taskMonitor,[ref]$taskInfo) -or $taskInfo.Device -cne '\\.\DISPLAY2') { continue }
   if ($taskRect.Left -lt $taskInfo.Monitor.Left -or $taskRect.Top -lt $taskInfo.Monitor.Top -or $taskRect.Right -gt $taskInfo.Monitor.Right -or $taskRect.Bottom -gt $taskInfo.Monitor.Bottom) { continue }
   [long]$taskPackedPoint=(([long]$taskPoint.Y -band 65535) -shl 16) -bor ([long]$taskPoint.X -band 65535)
   [UIntPtr]$taskHit=[UIntPtr]::Zero
   $taskHitCall=[HelpdeskScreen2CaptionProbe]::SendMessageTimeout($taskWindow,132,[UIntPtr]::Zero,[IntPtr]$taskPackedPoint,2,100,[ref]$taskHit)
   if ($taskHitCall -eq [IntPtr]::Zero -or $taskHit.ToUInt64() -ne 2) { continue }
   $taskFound=@{device=$taskInfo.Device;x=$taskPoint.X;y=$taskPoint.Y;process='WXWork';handle=$taskWindow.ToInt64();hit_test=2;
    screen_left=$taskInfo.Monitor.Left;screen_top=$taskInfo.Monitor.Top;screen_right=$taskInfo.Monitor.Right;screen_bottom=$taskInfo.Monitor.Bottom;
    window_left=$taskRect.Left;window_top=$taskRect.Top;window_right=$taskRect.Right;window_bottom=$taskRect.Bottom}
   break
  }
  if ($null -ne $taskFound) { break }
 }
 if ($null -eq $taskFound) { throw 'VISIBLE_SCREEN2_WECOM_CAPTION_UNAVAILABLE' }
 $taskFound | ConvertTo-Json -Compress
} finally { [void][HelpdeskScreen2CaptionProbe]::SetThreadDpiAwarenessContext($taskDpiContext) }
'''


def wecom_window_command(display_device=r'\\.\DISPLAY2'):
    """Fixed native reads on one explicitly configured Windows display."""
    _display_device(display_device)
    return WECOM_SCREEN2_WINDOW_COMMAND.replace(r'\\.\DISPLAY2', display_device)


def parse_wecom_window_location(record, *, display_device=r'\\.\DISPLAY2'):
    """A covered title bar may still be switched by App; no click is authorized."""
    wecom_window_command(display_device)
    if record.get('tool') != 'PowerShell' or record.get('is_error') is not False:
        raise ValueError('WECOM_WINDOW_PROBE_FAILED')
    content = record.get('content', [])
    if len(content) != 1 or content[0].get('type') != 'text':
        raise ValueError('WECOM_WINDOW_PROBE_AMBIGUOUS')
    raw = content[0].get('text', '')
    if not raw.startswith('Response: ') or not raw.rstrip().endswith('Status Code: 0'):
        raise ValueError('WECOM_WINDOW_PROBE_EXIT_FAILED')
    value = json.loads(raw[len('Response: '):].rsplit('\nStatus Code:', 1)[0].strip())
    integers = {'handle'} | {prefix + side for prefix in ('screen_', 'window_')
                           for side in ('left', 'top', 'right', 'bottom')}
    if (not isinstance(value, dict) or set(value) != integers | {'device', 'process', 'title'}
            or any(type(value[key]) is not int for key in integers)
            or value['device'] != display_device or value['process'] != 'WXWork'
            or value['title'] != '企业微信' or value['handle'] <= 0):
        raise ValueError('WECOM_WINDOW_PROBE_FORMAT_CHANGED')
    for prefix in ('screen_', 'window_'):
        if (value[prefix + 'right'] <= value[prefix + 'left']
                or value[prefix + 'bottom'] <= value[prefix + 'top']):
            raise ValueError('WECOM_WINDOW_INVALID')
    if any(value['window_' + side] < value['screen_' + side] for side in ('left', 'top')) or any(
            value['window_' + side] > value['screen_' + side] for side in ('right', 'bottom')):
        raise ValueError('WECOM_WINDOW_NOT_WITHIN_SCREEN2')
    return value


def verify_wecom_switch_snapshot(record, target, *, display_index=1):
    """Pin App's exact cached window name/handle on the configured display."""
    if type(display_index) is not int or display_index < 0:
        raise ValueError('INVALID_DISPLAY_INDEX')
    wecom_window_command(target['device'])
    from .mcp_page_contract import snapshot_text
    summary = snapshot_text(record).split('UI Tree:', 1)[0]
    bounds = tuple(target['screen_' + side] for side in ('left', 'top', 'right', 'bottom'))
    regions = re.findall(r'^Screenshot Region: \((-?\d+),(-?\d+),(-?\d+),(-?\d+)\)\s*$', summary, re.M)
    if (re.findall(r'^Selected Displays: (.*)$', summary, re.M) != [str(display_index)]
            or len(regions) != 1 or tuple(map(int, regions[0])) != bounds
            or not re.search(r'(?<!\d)' + str(display_index) + r':\s*' + re.escape(target['device']) + r'\s', summary)):
        raise ValueError('SWITCH_SNAPSHOT_OUTSIDE_SCREEN2')
    tables = summary.split('Focused Window:', 1)
    if len(tables) != 2 or tables[1].count('Opened Windows:') != 1:
        raise ValueError('SWITCH_WINDOW_LIST_UNAVAILABLE')
    opened = tables[1].split('Opened Windows:', 1)[1]
    # Windows-MCP refreshes the whole desktop when its cached opened list is
    # empty. Never permit that implicit expansion beyond this screen-2 read.
    if not re.search(r'^\s*\S[^\n]*\s+\d+\s+(?:Normal|Maximized|Minimized|Hidden)\s+\d+\s+\d+\s+\d+\s*$', opened, re.M):
        raise ValueError('SWITCH_WINDOW_CACHE_EMPTY')
    # App searches its opened-window cache, not the focused-window summary.
    # A focused target absent from that cache must not authorize fuzzy lookup.
    matches = re.findall(r'^\s*企业微信\s+(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$', opened, re.M)
    if len(matches) != 1:
        raise ValueError('SWITCH_EXACT_WINDOW_AMBIGUOUS')
    _, status, width, height, handle = matches[0]
    if (status not in {'Normal', 'Maximized'} or int(handle) != target['handle']
            or int(width) != target['window_right'] - target['window_left']
            or int(height) != target['window_bottom'] - target['window_top']):
        raise ValueError('SWITCH_WINDOW_CHANGED')
    return target


def parse_wecom_caption_location(record):
    """Extract a read-only locator result, retaining the existing target checks."""
    try:
        raw = record['content'][0]['text']
        value = json.loads(raw[len('Response: '):].rsplit('\nStatus Code:', 1)[0].strip())
        target = parse_screen2_caption(record, [value['x'], value['y']], target_process='WXWork')
    except (KeyError, TypeError, IndexError, ValueError):
        raise ValueError('WECOM_CAPTION_LOCATION_UNCONFIRMED') from None
    if any(target['window_' + side] < target['screen_' + side] for side in ('left', 'top')) or any(
            target['window_' + side] > target['screen_' + side] for side in ('right', 'bottom')):
        raise ValueError('WECOM_WINDOW_NOT_WITHIN_SCREEN2')
    return target


FOREGROUND_COMMAND = r'''$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class HelpdeskForegroundProbe {
 [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr handle, out uint processId);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr handle, out Rect rect);
}
'@
$taskDpiContext=[HelpdeskForegroundProbe]::SetThreadDpiAwarenessContext([IntPtr](-4))
if ($taskDpiContext -eq [IntPtr]::Zero) { throw 'DPI_CONTEXT_UNAVAILABLE' }
try {
$taskWindowHandle=[HelpdeskForegroundProbe]::GetForegroundWindow()
[uint32]$taskWindowPid=0
[void][HelpdeskForegroundProbe]::GetWindowThreadProcessId($taskWindowHandle,[ref]$taskWindowPid)
$taskWindowRect=New-Object HelpdeskForegroundProbe+Rect
if (-not [HelpdeskForegroundProbe]::GetWindowRect($taskWindowHandle,[ref]$taskWindowRect)) { throw 'WINDOW_UNAVAILABLE' }
$taskWindowProcess=(Get-Process -Id $taskWindowPid -ErrorAction Stop).ProcessName
@{ process=$taskWindowProcess; handle=$taskWindowHandle.ToInt64(); width=($taskWindowRect.Right-$taskWindowRect.Left); height=($taskWindowRect.Bottom-$taskWindowRect.Top); left=$taskWindowRect.Left; top=$taskWindowRect.Top } | ConvertTo-Json -Compress
} finally { [void][HelpdeskForegroundProbe]::SetThreadDpiAwarenessContext($taskDpiContext) }
'''


def parse_foreground_process(record):
    if record.get('tool') != 'PowerShell' or record.get('is_error') is not False:
        raise ValueError('FOREGROUND_PROBE_FAILED')
    content = record.get('content', [])
    if len(content) != 1 or content[0].get('type') != 'text':
        raise ValueError('FOREGROUND_PROBE_AMBIGUOUS')
    raw = content[0].get('text', '')
    if not raw.startswith('Response: ') or not raw.rstrip().endswith('Status Code: 0'):
        raise ValueError('FOREGROUND_PROBE_EXIT_FAILED')
    value = json.loads(raw[len('Response: '):].rsplit('\nStatus Code:', 1)[0].strip())
    if set(value) != {'process', 'handle', 'width', 'height', 'left', 'top'}:
        raise ValueError('FOREGROUND_PROBE_FORMAT_CHANGED')
    if (not isinstance(value['process'], str) or not value['process'].strip()
            or any(type(value[k]) is not int for k in ('handle','width','height','left','top'))):
        raise ValueError('FOREGROUND_PROBE_FORMAT_CHANGED')
    if value['handle'] <= 0 or value['width'] <= 0 or value['height'] <= 0:
        raise ValueError('FOREGROUND_WINDOW_INVALID')
    return value


def parse_foreground(record):
    """Preserve the WeCom-only identity contract used by delivery preflight."""
    value = parse_foreground_process(record)
    if value['process'] != 'WXWork':
        raise ValueError('WECOM_NOT_FOREGROUND')
    return value
