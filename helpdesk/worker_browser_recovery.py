"""Bounded, local-only proof that a dedicated profile has no live executor.

This is a startup maintenance seam, not a server command or a business health
claim. Native process command lines are filtered in the trusted subprocess;
only counts, a session number, hashes and a timestamp are returned.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

from .worker_contracts import digest, instant, reference, utc_now


def profile_path_digest(path):
    return sha256(str(Path(path).resolve()).casefold().encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ProfileQuiescence:
    profile_id: str
    account_id: str
    profile_path_sha256: str
    marker_sha256: str
    observed_at: str
    session_id: int
    matching_browser_processes: int
    unknown_processes: int
    other_executor_processes: int
    scan_completed: bool
    scan_scope: str = 'UNCONFIRMED'

    def validate(self, profile_id, account_id, path, marker_sha256, *, after, now=None):
        now = utc_now() if now is None else now
        if (reference(self.profile_id) != profile_id or reference(self.account_id) != account_id
                or digest(self.profile_path_sha256) != profile_path_digest(path)
                or digest(self.marker_sha256) != marker_sha256
                or type(self.session_id) is not int or self.session_id <= 0
                or self.scan_completed is not True
                or self.scan_scope != 'MACHINE_CONFIGURED_EXECUTORS'):
            raise ValueError('PROFILE_RECOVERY_BINDING_UNCONFIRMED')
        if (instant(self.observed_at) < instant(after)
                or not 0 <= (now - instant(self.observed_at)).total_seconds() <= 5):
            raise ValueError('PROFILE_RECOVERY_PROOF_STALE')
        if any(type(n) is not int or n != 0 for n in (
                self.matching_browser_processes, self.unknown_processes,
                self.other_executor_processes)):
            raise ValueError('PROFILE_EXECUTOR_NOT_QUIESCENT')


class WindowsProfileQuiescenceObserver:
    """Fixed native metadata probe. It never closes or kills any process.

    Caller holds the desktop and profile occupancy locks and separately checks
    the server stop/takeover state and local native quarantine. Missing command
    lines, inaccessible scans, any Edge whose profile cannot be positively
    excluded, or another configured executor leave recovery unavailable. The
    scan covers the machine, including other logon/RDP sessions. It deliberately
    blocks on unrelated Edge rather than guessing a default or aliased profile.
    This is a conservative maintenance check, not a hostile-process detector.
    """

    _PREFIX = r'''
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Import-Module ([System.IO.Path]::Combine($PSHOME,'Modules','CimCmdlets','CimCmdlets.psd1')) -ErrorAction Stop
$inputData = ConvertFrom-Json ([Console]::In.ReadToEnd())
$path = [System.IO.Path]::GetFullPath([string]$inputData.path).TrimEnd('\').ToLowerInvariant()
$ownPid = [int]$inputData.owner_pid
$session = [System.Diagnostics.Process]::GetCurrentProcess().SessionId
if ($session -le 0) { throw 'INTERACTIVE_SESSION_REQUIRED' }
$matchingBrowsers = 0
$unknown = 0
$executors = 0
'''
    # Kept separate so regression tests can execute the exact classifier with
    # anonymous rows. Those tests must never run the native inventory query.
    _CLASSIFY = r'''
$owner = @($rows | Where-Object { [int64]$_.ProcessId -eq $ownPid })
$scanner = @($rows | Where-Object { [int64]$_.ProcessId -eq $PID })
if ($owner.Count -ne 1 -or $scanner.Count -ne 1 -or
    [int]$owner[0].SessionId -ne $session -or
    $owner[0].Name -notmatch '(?i)^python(?:\d+(?:\.\d+)*)?w?\.exe$' -or
    [int64]$scanner[0].ParentProcessId -ne $ownPid -or
    [int]$scanner[0].SessionId -ne $session) { throw 'PROFILE_PROCESS_SCAN_UNAVAILABLE' }
foreach ($row in $rows) {
  # The two exceptions are the owned helper and its direct scanner child.
  # Ancestor shells and other sessions are not silently exempted.
  if ([int64]$row.ProcessId -eq $ownPid -or [int64]$row.ProcessId -eq $PID) { continue }
  if ($null -eq $row.Name -or [string]::IsNullOrWhiteSpace([string]$row.Name) -or
      $null -eq $row.ProcessId -or $null -eq $row.SessionId) { $unknown++; continue }
  $name = ([string]$row.Name).ToLowerInvariant()
  $browser = $name -eq 'msedge.exe'
  $actor = $name -match '^(?:python(?:\d+(?:\.\d+)*)?w?|py|pythonw|powershell|powershell_ise|pwsh|node|uv|msedgedriver|chromedriver|wscript|cscript|autohotkey(?:u32|u64|a32|64|32)?)\.exe$'
  if (-not $browser -and -not $actor) { continue }
  $line = [string]$row.CommandLine
  if ([string]::IsNullOrWhiteSpace($line)) { $unknown++; continue }
  if ($browser) {
    # Only the approved absolute profile path is compared. Nothing is emitted
    # from command lines, including unrelated browser URLs or private flags.
    $normalized = $line.ToLowerInvariant().Replace('/','\')
    $needle = $path.Replace('/','\')
    if ($normalized.Contains($needle)) { $matchingBrowsers++ } else { $unknown++ }
  } elseif ($actor) {
    # An absent module name does not prove a Python/shell/Node actor harmless.
    $executors++
  }
}
$result = [ordered]@{session_id=$session;matching_browser_processes=$matchingBrowsers;unknown_processes=$unknown;other_executor_processes=$executors;scan_completed=$true;scan_scope='MACHINE_CONFIGURED_EXECUTORS';observed_at=[DateTimeOffset]::UtcNow.ToString('o')}
[Console]::Out.Write(($result | ConvertTo-Json -Compress))
'''
    _SCRIPT = (_PREFIX + r'''
$rows = @(CimCmdlets\Get-CimInstance -ClassName Win32_Process -Property Name,ProcessId,ParentProcessId,SessionId,CommandLine -ErrorAction Stop)
''' + _CLASSIFY)

    def __call__(self, profile_id, account_id, path, marker_sha256):
        if os.name != 'nt':
            raise RuntimeError('WINDOWS_PROFILE_PROOF_REQUIRED')
        reference(profile_id); reference(account_id); digest(marker_sha256)
        payload = json.dumps({'path': str(path), 'owner_pid': os.getpid()})
        reply = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', self._SCRIPT],
            input=payload, text=True, encoding='utf-8', errors='strict',
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if reply.returncode != 0 or len(reply.stdout.encode('utf-8')) > 2048:
            raise RuntimeError('PROFILE_PROCESS_SCAN_UNAVAILABLE')
        try:
            value = json.loads(reply.stdout)
            keys = {'session_id', 'matching_browser_processes', 'unknown_processes',
                    'other_executor_processes', 'scan_completed', 'scan_scope', 'observed_at'}
            if type(value) is not dict or set(value) != keys:
                raise ValueError()
            return ProfileQuiescence(profile_id, account_id, profile_path_digest(path),
                                     marker_sha256, **value)
        except (TypeError, ValueError):
            raise RuntimeError('PROFILE_PROCESS_SCAN_UNAVAILABLE') from None
