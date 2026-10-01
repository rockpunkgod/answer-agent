"""Fixed read-only executor desktop metadata; never proves application identity.

Microsoft ABI references (Unicode W structures, default Windows pack 8):
https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ne-wtsapi32-wts_info_class
https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoexw
https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w
Win8+/Win11 flags: 0 locked, 1 unlocked; Win7 reversed flags are rejected.
"""
import json
from dataclasses import dataclass
from datetime import timedelta

from .worker_contracts import HealthState, WorkerHealth, instant, utc_now


DESKTOP_STATUS_COMMAND = r'''$ErrorActionPreference='Stop'
if (-not ('HelpdeskDesktopStatusV2' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public static class HelpdeskDesktopStatusV2 {
 public const int WTS_CONNECT_STATE=8;
 public const int WTS_CLIENT_PROTOCOL=16;
 public const int WTS_SESSION_INFO_EX=25;
 [DllImport("kernel32.dll")] static extern bool ProcessIdToSessionId(uint pid,out uint sid);
 [DllImport("wtsapi32.dll",EntryPoint="WTSQuerySessionInformationW",CharSet=CharSet.Unicode,ExactSpelling=true)] static extern bool WTSQuerySessionInformation(IntPtr server,uint sid,int kind,out IntPtr buffer,out uint bytes);
 [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
 [DllImport("user32.dll",SetLastError=true)] static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr obj,int index,StringBuilder value,uint size,out uint needed);
 delegate bool WindowCallback(IntPtr hwnd,IntPtr data);
 [DllImport("user32.dll")] static extern bool EnumWindows(WindowCallback callback,IntPtr data);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hwnd);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hwnd,out uint pid);
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode,Pack=8)] struct Level1 {
  public uint SessionId; public int State; public int Flags;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=33)] public string Station;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=21)] public string User;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=18)] public string Domain;
  public long Logon,Connect,Disconnect,LastInput,Current;
  public uint Incoming,Outgoing,IncomingFrames,OutgoingFrames,IncomingCompressed,OutgoingCompressed;
 }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode,Pack=8)] struct InfoEx { public uint Level; public Level1 Data; }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct Version {
  public uint Size,Major,Minor,Build,Platform;
  [MarshalAs(UnmanagedType.ByValTStr,SizeConst=128)] public string Service;
 }
 [DllImport("ntdll.dll",CharSet=CharSet.Unicode)] static extern int RtlGetVersion(ref Version v);
 public static int Session() { try {uint sid;return ProcessIdToSessionId((uint)Process.GetCurrentProcess().Id,out sid)?(int)sid:-1;}catch{return -1;} }
 public static int Query(uint sid,int kind) {IntPtr p=IntPtr.Zero;try {uint n;if(!WTSQuerySessionInformation(IntPtr.Zero,sid,kind,out p,out n))return -1;
  if(kind==WTS_CONNECT_STATE && n>=4)return Marshal.ReadInt32(p);
  if(kind==WTS_CLIENT_PROTOCOL && n>=2)return Marshal.ReadInt16(p);
  if(kind==WTS_SESSION_INFO_EX && n>=Marshal.SizeOf(typeof(InfoEx))) {
   if(Marshal.OffsetOf(typeof(InfoEx),"Data").ToInt32()!=8 || Marshal.SizeOf(typeof(InfoEx))!=232 ||
      Marshal.OffsetOf(typeof(Level1),"Flags").ToInt32()!=8 || Marshal.SizeOf(typeof(Level1))!=224)return -1;
   Version v=new Version();v.Size=(uint)Marshal.SizeOf(typeof(Version));
   if(RtlGetVersion(ref v)!=0 || v.Major<6 || (v.Major==6 && v.Minor<2))return -1;
   InfoEx info=(InfoEx)Marshal.PtrToStructure(p,typeof(InfoEx));
   if(info.Level==1 && info.Data.SessionId==sid)return info.Data.Flags;
  }return -1;}catch{return -1;}finally{if(p!=IntPtr.Zero)WTSFreeMemory(p);} }
 public static string Desktop() {IntPtr p=IntPtr.Zero;try {p=OpenInputDesktop(0,false,1);if(p==IntPtr.Zero)return null;
  StringBuilder name=new StringBuilder(256);uint n;return GetUserObjectInformation(p,2,name,512,out n)?name.ToString():null;
 }catch{return null;}finally{if(p!=IntPtr.Zero)CloseDesktop(p);} }
 public static int[] Windows(int sid) {try {int wx=0,edge=0;bool uncertain=false;
  bool ok=EnumWindows(delegate(IntPtr h,IntPtr d){try {if(!IsWindowVisible(h))return true;uint pid;GetWindowThreadProcessId(h,out pid);
   using(Process p=Process.GetProcessById((int)pid)){if(p.SessionId==sid){if(p.ProcessName=="WXWork")wx++;if(p.ProcessName=="msedge")edge++;}}
  }catch{uncertain=true;}return true;},IntPtr.Zero);
  return ok&&!uncertain?new int[]{wx,edge}:null;
 }catch{return null;} }
}
'@
}
$probeSession=[HelpdeskDesktopStatusV2]::Session()
$probeState=-1; $probeFlags=-1; $probeProtocol=-1; $probeWindows=$null
if($probeSession -ge 0){
 $probeState=[HelpdeskDesktopStatusV2]::Query($probeSession,[HelpdeskDesktopStatusV2]::WTS_CONNECT_STATE)
 $probeFlags=[HelpdeskDesktopStatusV2]::Query($probeSession,[HelpdeskDesktopStatusV2]::WTS_SESSION_INFO_EX)
 $probeProtocol=[HelpdeskDesktopStatusV2]::Query($probeSession,[HelpdeskDesktopStatusV2]::WTS_CLIENT_PROTOCOL)
 $probeWindows=[HelpdeskDesktopStatusV2]::Windows($probeSession)
}
$probeDesktop=[HelpdeskDesktopStatusV2]::Desktop()
$probeRemote=$null
if($probeProtocol -eq 0){$probeRemote=$false}; if($probeProtocol -eq 2){$probeRemote=$true}
$probeName=$null; if($null -ne $probeDesktop){if($probeDesktop -ieq 'Default'){$probeName='DEFAULT'}else{$probeName='OTHER'}}
$probeCounts=$null; if($null -ne $probeWindows){$probeCounts=@{wxwork=$probeWindows[0];edge=$probeWindows[1]}}
@{schema_version=1;observed_at=[DateTimeOffset]::UtcNow.ToString('o');session_id=$probeSession;wts_state=$probeState;session_flags=$probeFlags;remote=$probeRemote;input_desktop=$probeName;input_accessible=($null -ne $probeDesktop);windows=$probeCounts} | ConvertTo-Json -Compress
'''


@dataclass(frozen=True)
class DesktopStatus:
    observed_at: str
    session_id: int
    wts_state: int
    session_flags: int
    remote: bool | None
    input_desktop: str | None
    input_accessible: bool
    windows: dict | None

    @property
    def interactive(self):
        return self.session_id > 0 and self.wts_state == 0 and self.remote is not None

    @property
    def unlocked(self):
        return (self.interactive and self.session_flags == 1
                and self.input_accessible and self.input_desktop == 'DEFAULT')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('DESKTOP_PROBE_DUPLICATE_FIELD')
        result[key] = value
    return result


def parse_desktop_status(text, *, observed_after, now=None, max_age_seconds=30):
    """Accept one JSON response, optionally the exact Windows-MCP text envelope."""
    now = now or utc_now()
    after = instant(observed_after)
    if now.tzinfo is None or not 0 < max_age_seconds <= 120:
        raise ValueError('DESKTOP_PROBE_TIME_BOUND_INVALID')
    if not isinstance(text, str) or len(text.encode('utf-8')) > 4096:
        raise ValueError('DESKTOP_PROBE_SIZE_INVALID')
    if text.startswith('Response: '):
        suffix = '\nStatus Code: 0'
        if not text.rstrip().endswith(suffix):
            raise ValueError('DESKTOP_PROBE_EXIT_FAILED')
        text = text.rstrip()[len('Response: '):-len(suffix)].strip()
    value = json.loads(text, object_pairs_hook=_unique)
    keys = {'schema_version', 'observed_at', 'session_id', 'wts_state', 'session_flags',
            'remote', 'input_desktop', 'input_accessible', 'windows'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('DESKTOP_PROBE_FIELDS_INVALID')
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('DESKTOP_PROBE_VERSION_INVALID')
    for key, low, high in (('session_id', -1, 1000000), ('wts_state', -1, 9), ('session_flags', -1, 1)):
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError('DESKTOP_PROBE_ENUM_INVALID')
    if value['remote'] is not None and type(value['remote']) is not bool:
        raise ValueError('DESKTOP_PROBE_BOOLEAN_INVALID')
    if type(value['input_accessible']) is not bool or value['input_desktop'] not in (None, 'DEFAULT', 'OTHER'):
        raise ValueError('DESKTOP_PROBE_DESKTOP_INVALID')
    if value['input_accessible'] != (value['input_desktop'] is not None):
        raise ValueError('DESKTOP_PROBE_DESKTOP_INCONSISTENT')
    windows = value['windows']
    if windows is not None and (not isinstance(windows, dict) or set(windows) != {'wxwork', 'edge'}
            or any(type(n) is not int or not 0 <= n <= 10000 for n in windows.values())):
        raise ValueError('DESKTOP_PROBE_WINDOWS_INVALID')
    at = instant(value['observed_at'])
    if at < after or at > now or now - at > timedelta(seconds=max_age_seconds):
        raise ValueError('DESKTOP_PROBE_STALE_OR_UNBOUND')
    return DesktopStatus(**{k: value[k] for k in keys if k != 'schema_version'})


def parse_desktop_record(record, **time_bounds):
    if not isinstance(record, dict) or record.get('tool') != 'PowerShell' or record.get('is_error') is not False:
        raise ValueError('DESKTOP_PROBE_MCP_FAILED')
    content = record.get('content')
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
            or set(content[0]) != {'type', 'text'} or content[0]['type'] != 'text'):
        raise ValueError('DESKTOP_PROBE_MCP_AMBIGUOUS')
    return parse_desktop_status(content[0]['text'], **time_bounds)


def build_worker_health(status, *, worker_id, account_id, app_observation=None,
                        expected_platform=None, connected=True, now=None):
    """app_observation must come from an independently trusted app detector."""
    if not isinstance(status, DesktopStatus):
        raise ValueError('DESKTOP_PROBE_STATUS_REQUIRED')
    if expected_platform not in (None, 'wecom', 'deepseek'):
        raise ValueError('EXPECTED_PLATFORM_INVALID')
    now = now or utc_now()
    if not timedelta(0) <= now - instant(status.observed_at) <= timedelta(seconds=30):
        raise ValueError('DESKTOP_PROBE_STALE')
    state = HealthState.UNKNOWN
    profile = scope = session = None
    pending = False
    interactive, unlocked = status.interactive, status.unlocked
    if app_observation is not None:
        if not isinstance(app_observation, WorkerHealth):
            raise ValueError('TRUSTED_APP_HEALTH_REQUIRED')
        if app_observation.worker_id != worker_id or app_observation.account_id != account_id:
            raise ValueError('APP_OBSERVATION_IDENTITY_MISMATCH')
        if not timedelta(0) <= now - instant(app_observation.observed_at) <= timedelta(seconds=30):
            raise ValueError('APP_OBSERVATION_STALE')
        state = app_observation.state
        profile, scope, session = app_observation.profile_id, app_observation.observed_scope, app_observation.observed_session
        pending = app_observation.native_call_pending
        connected = connected and app_observation.connected
        interactive = interactive and app_observation.interactive_desktop
        unlocked = unlocked and app_observation.desktop_unlocked
        window_key = {'wecom': 'wxwork', 'deepseek': 'edge'}.get(expected_platform)
        if (window_key is None or status.windows is None
                or status.windows[window_key] < 1):
            state = HealthState.UNKNOWN
        if state == HealthState.HEALTHY and scope is None:
            state = HealthState.UNKNOWN
    if not (interactive and unlocked) and state == HealthState.HEALTHY:
        state = HealthState.DESKTOP_UNAVAILABLE
    return WorkerHealth(worker_id=worker_id, account_id=account_id, connected=connected,
        interactive_desktop=interactive, desktop_unlocked=unlocked, state=state,
        profile_id=profile, observed_scope=scope, observed_session=session,
        observed_at=min((status.observed_at, app_observation.observed_at), key=instant) if app_observation else status.observed_at,
        native_call_pending=pending)
