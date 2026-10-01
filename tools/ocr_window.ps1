param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath,
    [string]$ExpectedText = '',
    [switch]$ProbeTextOnly
)

# Read-only Windows.Media.Ocr probe. Never prints raw OCR text or a chat history.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding

function Await-WinRT {
    param($Operation, [Type]$ResultType)
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    if (-not $method) { throw 'WinRT AsTask bridge unavailable' }
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}

function Result($available, $size, $targetBoxes, $textBox, $reason) {
    [pscustomobject]@{
        available = $available
        image_size = $size
        target_matches = @($targetBoxes)
        header_match = [bool](@($targetBoxes | Where-Object { $_.region -eq 'header' }).Count)
        sidebar_match = [bool](@($targetBoxes | Where-Object { $_.region -eq 'sidebar' }).Count)
        text_box_ocr = $textBox
        editor_match = $script:editorMatch
        receipt_match = $script:receiptMatch
        receipt_probe_id_match = $script:receiptProbeIdMatch
        editor_probe_id_match = $script:editorProbeIdMatch
        probe_editor_text = $script:probeEditorText
        reason = $reason
    } | ConvertTo-Json -Compress -Depth 3
}

try {
    $script:editorMatch = $false
    $script:receiptMatch = $false
    $script:receiptProbeIdMatch = $false
    $script:editorProbeIdMatch = $false
    $script:probeEditorText = $null
    $resolved = (Resolve-Path -LiteralPath $ImagePath).Path
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $storageType = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $accessType = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
    $streamType = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
    $decoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $engineType = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $ocrResultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
    $languageType = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

    $file = Await-WinRT ($storageType::GetFileFromPathAsync($resolved)) $storageType
    $stream = Await-WinRT ($file.OpenAsync($accessType::Read)) $streamType
    try {
        $decoder = Await-WinRT ($decoderType::CreateAsync($stream)) $decoderType
        $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) $bitmapType
        try {
            $engine = $engineType::TryCreateFromLanguage(($languageType::new('zh-Hans')))
            if (-not $engine) { $engine = $engineType::TryCreateFromUserProfileLanguages() }
            if (-not $engine) { throw 'No Windows OCR language pack available' }
            $ocr = Await-WinRT ($engine.RecognizeAsync($bitmap)) $ocrResultType
            if ($ProbeTextOnly) {
                if (-not $ExpectedText.StartsWith('TEST ')) { throw 'Only the known probe text is eligible' }
                $observed = $ocr.Text.Normalize([Text.NormalizationForm]::FormKC) -replace '\s+', ''
                $expected = $ExpectedText.Normalize([Text.NormalizationForm]::FormKC) -replace '\s+', ''
                [pscustomobject]@{ available=$true; exact_probe_text_match=($observed -ceq $expected) } | ConvertTo-Json -Compress
                return
            }

            # The OCR may have read private conversation text. Keep it in memory only;
            # emit only the exact target match and a conservative input-box signal.
            $target = '苇中鹤'
            $targetBoxes = @()
            $inputSignal = $null
            $editorLines = @()
            $receiptLines = @()
            foreach ($line in $ocr.Lines) {
                $value = ($line.Text -replace '\s+', '')
                if ($line.Words.Count -gt 0) {
                    $left = [math]::Floor(($line.Words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum)
                    $top = [math]::Floor(($line.Words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum)
                    $right = [math]::Ceiling(($line.Words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum)
                    $bottom = [math]::Ceiling(($line.Words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum)
                    if ($left -gt 480 -and $right -lt 1560 -and $top -gt 800 -and $bottom -lt 915) { $editorLines += $value }
                    if ($left -gt 480 -and $right -lt 1560 -and $top -gt 110 -and $bottom -lt 735) { $receiptLines += $value }
                }
                if (($value -eq $target -or $value -eq ($target+'@微信')) -and $line.Words.Count -gt 0) {
                    $region = if ($left -gt 460 -and $top -lt 110) { 'header' }
                              elseif ($left -lt 460 -and $top -lt 200) { 'sidebar' }
                              else { 'other' }
                    $targetBoxes += [pscustomobject]@{ text = $target; x = $left; y = $top;
                        width = $right - $left; height = $bottom - $top; region = $region }
                }
                if (-not $inputSignal -and $value -match '^(发送消息|输入消息|请输入消息)$') { $inputSignal = $line }
            }
            if ($ExpectedText) {
                $englishEngine = $engineType::TryCreateFromLanguage(($languageType::new('en-US')))
                if ($englishEngine) {
                    $english = Await-WinRT ($englishEngine.RecognizeAsync($bitmap)) $ocrResultType
                    $editorLines = @()
                    $receiptLines = @()
                    foreach ($line in $english.Lines) {
                        if ($line.Words.Count -eq 0) { continue }
                        $value = $line.Text.Normalize([Text.NormalizationForm]::FormKC) -replace '\s+', ''
                        $left = ($line.Words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum
                        $top = ($line.Words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
                        $right = ($line.Words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum
                        $bottom = ($line.Words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
                        if ($left -gt 480 -and $right -lt 1560 -and $top -gt 800 -and $bottom -lt 915) { $editorLines += $value }
                        if ($left -gt 480 -and $right -lt 1560 -and $top -gt 110 -and $bottom -lt 735) { $receiptLines += $value }
                    }
                }
                $needle = $ExpectedText -replace '\s+', ''
                $script:editorMatch = (($editorLines -join '').Contains($needle))
                $script:receiptMatch = (($receiptLines -join '').Contains($needle))
                $parts = $ExpectedText.Split(' ')
                if ($parts.Count -ge 3 -and $parts[0] -eq 'TEST') {
                    $probeId = ($parts[0]+$parts[1]+$parts[2]).TrimEnd('.')
                    $script:receiptProbeIdMatch = (($receiptLines -join '').Contains($probeId))
                    $script:editorProbeIdMatch = (($editorLines -join '').Contains($probeId))
                }
                if (($editorLines -join '').StartsWith('TEST')) { $script:probeEditorText = $editorLines -join '' }
            }
            if ($inputSignal) {
                $boxState = 'label_detected_only'
            } else {
                $boxState = 'not_verifiable_from_ocr'
            }
            $size = [pscustomobject]@{ width = $bitmap.PixelWidth; height = $bitmap.PixelHeight }
            $headerFound = [bool](@($targetBoxes | Where-Object { $_.region -eq 'header' }).Count)
            Result $true $size $targetBoxes $boxState $(if ($headerFound) { 'Header target recognized. OCR cannot prove the compose box is editable.' } else { 'No header target recognized; a sidebar match alone does not establish the active chat.' })
        } finally { if ($bitmap) { $bitmap.Dispose() } }
    } finally { if ($stream) { $stream.Dispose() } }
} catch {
    Result $false $null @() 'not_verifiable_from_ocr' $_.Exception.GetType().Name
    exit 1
}
