param(
    [Parameter(Mandatory = $true)] [string]$Directory,
    [switch]$Force
)

# Offline OCR of already captured screenshots. Raw text is written only beside
# the private source image. Stdout contains counts and paths, never chat text.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $Directory).Path
$manifestPath = Join-Path $root 'manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath)) { throw 'History manifest missing' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$frames = @($manifest.frames)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$storageType = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$accessType = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
$streamType = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$decoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$bitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$engineType = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$ocrResultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
$languageType = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

function Await-WinRT {
    param($Operation, [Type]$ResultType)
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

$language = $languageType::new('zh-Hans')
$engine = $engineType::TryCreateFromLanguage($language)
if (-not $engine) { $engine = $engineType::TryCreateFromUserProfileLanguages() }
if (-not $engine) { throw 'Windows OCR language pack unavailable' }

$done = 0
$skipped = 0
$failed = 0
foreach ($frame in $frames) {
    $image = [IO.Path]::GetFullPath([string]$frame.file)
    if ([IO.Path]::GetDirectoryName($image) -ne $root -or -not [IO.File]::Exists($image)) {
        $failed++
        continue
    }
    $output = [IO.Path]::ChangeExtension($image, '.ocr.json')
    $hash = (Get-FileHash -LiteralPath $image -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($frame.sha256 -and $hash -ne ([string]$frame.sha256).ToLowerInvariant()) {
        $failed++
        continue
    }
    if (-not $Force -and [IO.File]::Exists($output)) {
        try {
            $old = [IO.File]::ReadAllText($output, [Text.Encoding]::UTF8) | ConvertFrom-Json
            if ($old.image_sha256 -eq $hash) { $skipped++; continue }
        } catch { }
    }
    $stream = $null
    $bitmap = $null
    try {
        $file = Await-WinRT ($storageType::GetFileFromPathAsync($image)) $storageType
        $stream = Await-WinRT ($file.OpenAsync($accessType::Read)) $streamType
        $decoder = Await-WinRT ($decoderType::CreateAsync($stream)) $decoderType
        $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) $bitmapType
        $ocr = Await-WinRT ($engine.RecognizeAsync($bitmap)) $ocrResultType
        $lines = @()
        $lineIndex = 0
        foreach ($line in $ocr.Lines) {
            $words = @()
            foreach ($word in $line.Words) {
                $r = $word.BoundingRect
                $words += [pscustomobject]@{ text = $word.Text; rect = [pscustomobject]@{
                    x = [double]$r.X; y = [double]$r.Y; width = [double]$r.Width; height = [double]$r.Height } }
            }
            if ($words.Count -eq 0) { continue }
            $left = ($words | ForEach-Object { $_.rect.x } | Measure-Object -Minimum).Minimum
            $top = ($words | ForEach-Object { $_.rect.y } | Measure-Object -Minimum).Minimum
            $right = ($words | ForEach-Object { $_.rect.x + $_.rect.width } | Measure-Object -Maximum).Maximum
            $bottom = ($words | ForEach-Object { $_.rect.y + $_.rect.height } | Measure-Object -Maximum).Maximum
            if ($bitmap.PixelWidth -gt 1000 -and $left -lt 1572) { continue }
            $lines += [pscustomobject]@{ index = $lineIndex; text = $line.Text;
                rect = [pscustomobject]@{ x = [double]$left; y = [double]$top;
                    width = [double]($right-$left); height = [double]($bottom-$top) }; words = $words }
            $lineIndex++
        }
        $record = [pscustomobject]@{
            image = $image; image_sha256 = $hash; captured_at = $frame.captured_at;
            source = 'Windows.Media.Ocr'; language = 'zh-Hans';
            width = $bitmap.PixelWidth; height = $bitmap.PixelHeight;
            lines = $lines
        }
        $json = $record | ConvertTo-Json -Depth 9
        [IO.File]::WriteAllText($output, $json, [Text.UTF8Encoding]::new($false))
        $done++
    } catch {
        $failed++
    } finally {
        if ($bitmap) { $bitmap.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}
[pscustomobject]@{ directory = $root; manifest_frames = $frames.Count; written = $done;
    reused = $skipped; failed = $failed; manifest_complete = [bool]$manifest.complete } |
    ConvertTo-Json -Compress
