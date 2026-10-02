param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8767,
    [string]$Db = 'data/native-demo.db',
    [string]$CollectorConfig = 'data/private/windows-native-demo/collector.local.toml',
    [string]$AnswerReviewRoot = 'data/private/answer-review-packets',
    [string]$SourceReviewManifest = '',
    [switch]$NoAutoCollect,
    [string]$ReferenceLookupConfig = '',
    [string]$AutomaticDeliveryConfig = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$LocalUrl = "http://127.0.0.1:$Port"
try {
    $ExistingState = Invoke-RestMethod -Uri ($LocalUrl + '/api/state') -TimeoutSec 5 -MaximumRedirection 0
} catch {
    $ExistingState = $null
}
if ($null -ne $ExistingState) {
    if ($ExistingState.processing_mode -ne 'ACK_ONLY' -or
            ($ExistingState.application -ne 'wecom-english-helpdesk' -and
             $ExistingState.collector.collection_kind -ne 'NATIVE_CLIPBOARD_IMPORT')) {
        throw "Another service is using port $Port; select a different port."
    }
    # Opening the workbench must not undo a deliberate pause or retry a blocked source.
    if ($SourceReviewManifest -and -not $ExistingState.source_review_enabled) {
        throw 'The existing workbench has source review disabled. Stop that service before enabling the reviewed teaching manifest; no duplicate was started.'
    }
    if ($ReferenceLookupConfig) {
        throw 'Reference lookup config is read at service startup. Stop the existing service or choose another port; its configuration was not changed.'
    }
    if ($AutomaticDeliveryConfig) {
        throw 'Automatic delivery config is read at startup. Stop the existing service or choose another port; no sender was enabled.'
    }
    Write-Output "Current demo is available: $LocalUrl/"
    if (-not $ExistingState.collector.control.worker_alive) {
        Write-Output 'Collection is not running. Review its status and start it from the workbench when ready.'
    }
    return
}
$Listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
if ($Listeners | Where-Object { $_.Port -eq $Port }) {
    throw "Port $Port is still listening but its status was not verified; no duplicate process was started."
}
$LaunchArgs = @('-X', 'utf8', '-B', '-m', 'helpdesk.demo_server', '--port', "$Port",
    '--db', $Db, '--processing-mode', 'ACK_ONLY', '--enable-performance')
if ($AutomaticDeliveryConfig) {
    $LaunchArgs += @('--automatic-delivery-config', $AutomaticDeliveryConfig)
} else {
    $LaunchArgs += '--worker-boundary'
}
if (Test-Path -LiteralPath $CollectorConfig -PathType Leaf) {
    $LaunchArgs += @('--collector-config', $CollectorConfig)
    if (-not $NoAutoCollect) { $LaunchArgs += '--auto-start-collector' }
} else {
    Write-Output 'Collector config is missing. Opening the local workbench only; collection and teaching remain disconnected.'
}
if (Test-Path -LiteralPath $AnswerReviewRoot -PathType Container) {
    $LaunchArgs += @('--answer-review-root', $AnswerReviewRoot)
}
if ($SourceReviewManifest) {
    $LaunchArgs += @('--source-review-manifest', $SourceReviewManifest)
}
if ($ReferenceLookupConfig) {
    $LaunchArgs += @('--reference-lookup-config', $ReferenceLookupConfig)
}
python @LaunchArgs
if ($LASTEXITCODE -ne 0) {
    throw "Demo process exited with code $LASTEXITCODE"
}
