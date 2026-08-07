param(
    [string]$VenvPath = ".venv",
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $($Arguments -join ' ')"
    }
}

# SHA256 pins for every binary this script downloads. All URLs are immutable
# (versioned release assets / a tagged ref), so these must not drift.
$script:ExpectedHashes = @{
    "tesseract-installer" = "C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9"
    "7zip-msi"            = "DB407A4F6D4999E5C7BC00CE8A882BE94717B56E7FA68140FE3F12605D91643E"
    "eng"                 = "7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2"
    "vie"                 = "79DF64CAF7BCFB2A27DF5042ECB6121E196EADA34DA774956995747636D5BFA1"
    "chi_sim"             = "A5FCB6F0DB1E1D6D8522F39DB4E848F05984669172E584E8D76B6B3141E1F730"
}

function Assert-FileHash {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Key
    )
    $expected = $script:ExpectedHashes[$Key]
    if (-not $expected) { throw "No pinned hash is configured for '$Key'." }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($actual -ne $expected) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        throw "Checksum mismatch for '$Key'.`n  expected: $expected`n  actual:   $actual`nThe download was deleted. Do not use it."
    }
    Write-Host "  verified SHA256 ${Key}: $actual"
}

function Refresh-Path {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = @($machinePath, $userPath) -join ";"
}

function Find-Python311 {
    $candidates = @(
        @{ Command = "py"; Args = @("-3.11") },
        @{ Command = "python"; Args = @() },
        @{ Command = "python3"; Args = @() }
    )
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.Command -ErrorAction SilentlyContinue)) { continue }
        $version = & $candidate.Command @($candidate.Args + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")) 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq "3.11") { return $candidate }
    }
    throw "Python 3.11 was not found. Install Python 3.11 and make sure py.exe or python.exe is on PATH."
}

function Install-WingetPackage {
    param([string]$Id, [string]$DisplayName)
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$DisplayName is missing and Winget is unavailable. Install App Installer, then rerun this script."
    }
    Write-Step "Installing $DisplayName with Winget"
    Invoke-Checked "winget" @(
        "install", "--exact", "--id", $Id, "--silent",
        "--accept-package-agreements", "--accept-source-agreements",
        "--disable-interactivity"
    )
    Refresh-Path
}

function Find-Tesseract {
    $portable = Join-Path $PSScriptRoot ".runtime\tesseract\tesseract.exe"
    if (Test-Path -LiteralPath $portable) { return $portable }
    $command = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
        "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
    )
    return $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

function Install-PortableTesseract {
    Write-Step "Installing portable Tesseract without administrator rights"
    $runtimeDir = Join-Path $PSScriptRoot ".runtime"
    $toolsDir = Join-Path $runtimeDir "tools"
    $portableDir = Join-Path $runtimeDir "tesseract"
    New-Item -ItemType Directory -Force -Path $toolsDir, $portableDir | Out-Null

    $installer = Join-Path $toolsDir "tesseract-ocr-w64-setup-5.4.0.20240606.exe"
    if (-not (Test-Path -LiteralPath $installer)) {
        Invoke-WebRequest -UseBasicParsing `
            -Uri "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe" `
            -OutFile $installer
    }
    Assert-FileHash -Path $installer -Key "tesseract-installer"

    $sevenZip = Join-Path $toolsDir "7zip\Files\7-Zip\7z.exe"
    if (-not (Test-Path -LiteralPath $sevenZip)) {
        $sevenZipMsi = Join-Path $toolsDir "7z-x64.msi"
        Invoke-WebRequest -UseBasicParsing -Uri "https://www.7-zip.org/a/7z2602-x64.msi" -OutFile $sevenZipMsi
        Assert-FileHash -Path $sevenZipMsi -Key "7zip-msi"
        $adminImage = Join-Path $toolsDir "7zip"
        New-Item -ItemType Directory -Force -Path $adminImage | Out-Null
        $process = Start-Process -FilePath "msiexec.exe" -ArgumentList @(
            "/a", "`"$sevenZipMsi`"", "/qn", "TARGETDIR=`"$adminImage`""
        ) -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $sevenZip)) {
            throw "Unable to prepare the portable 7-Zip extractor (exit $($process.ExitCode))."
        }
    }

    & $sevenZip x $installer "-o$portableDir" -y | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $portableDir "tesseract.exe"))) {
        throw "Portable Tesseract extraction failed."
    }
    return (Join-Path $portableDir "tesseract.exe")
}

Write-Host "AI Video Transcriber Windows 11 installer"
Write-Host "Working directory: $(Get-Location)"

$python = Find-Python311
$pythonVersion = & $python.Command @($python.Args + @("--version"))
if ($LASTEXITCODE -ne 0) { throw "Unable to run Python 3.11" }
Write-Host "Using $pythonVersion"

if (-not $SkipVenv) {
    Write-Step "Creating virtual environment"
    if (-not (Test-Path -LiteralPath $VenvPath)) {
        Invoke-Checked $python.Command ($python.Args + @("-m", "venv", $VenvPath))
    }
    $venvPython = Join-Path $VenvPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Virtual environment Python was not found at $venvPython"
    }
    $venvPythonArgs = @()
} else {
    $venvPython = $python.Command
    $venvPythonArgs = $python.Args
}

Write-Step "Installing Python dependencies"
Invoke-Checked $venvPython ($venvPythonArgs + @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"))
Invoke-Checked $venvPython ($venvPythonArgs + @("-m", "pip", "install", "-r", "requirements.txt"))
Invoke-Checked $venvPython ($venvPythonArgs + @("-m", "pip", "check"))

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Install-WingetPackage "Gyan.FFmpeg" "FFmpeg"
}
Write-Step "Checking FFmpeg and FFprobe"
Invoke-Checked "ffmpeg" @("-version")
Invoke-Checked "ffprobe" @("-version")

$tesseract = Find-Tesseract
if (-not $tesseract) {
    try {
        Install-WingetPackage "UB-Mannheim.TesseractOCR" "Tesseract OCR"
    } catch {
        Write-Warning "Winget could not complete Tesseract installation: $($_.Exception.Message)"
    }
    $tesseract = Find-Tesseract
}
if (-not $tesseract) { $tesseract = Install-PortableTesseract }

Write-Step "Preparing Tesseract language data"
$tessdataDir = Join-Path $PSScriptRoot ".runtime\tessdata"
New-Item -ItemType Directory -Force -Path $tessdataDir | Out-Null
$languages = @("eng", "vie", "chi_sim")
foreach ($language in $languages) {
    $target = Join-Path $tessdataDir "$language.traineddata"
    if (-not (Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -lt 100000) {
        # Pinned to the 4.1.0 tag rather than main so the bytes (and the hash) are stable.
        $uri = "https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/$language.traineddata"
        Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $target
        if (-not (Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -lt 100000) {
            throw "Failed to download Tesseract language: $language"
        }
    }
    Assert-FileHash -Path $target -Key $language
}
$listedLanguages = & $tesseract --tessdata-dir $tessdataDir --list-langs 2>&1
if ($LASTEXITCODE -ne 0) { throw "Tesseract language check failed." }
foreach ($language in $languages) {
    if ($listedLanguages -notcontains $language) { throw "Tesseract language is missing: $language" }
}
Write-Host "Tesseract: $tesseract"
Write-Host "Languages: $($languages -join ', ')"

Write-Step "Checking application imports"
$importCheck = "import importlib; modules=['fastapi','uvicorn','multipart','yt_dlp','faster_whisper','openai','pydantic','aiofiles','curl_cffi','vieneu','torch','torchaudio','rapidocr_onnxruntime']; [importlib.import_module(name) for name in modules]; print('All required Python imports are available')"
Invoke-Checked $venvPython ($venvPythonArgs + @("-c", $importCheck))
Invoke-Checked $venvPython ($venvPythonArgs + @("-m", "py_compile", "start.py", "backend\main.py", "backend\transcriber.py", "backend\video_processor.py", "backend\tts_engine.py"))

Write-Host ""
Write-Host "Install complete. Start the app with:"
if (-not $SkipVenv) { Write-Host "  .\$VenvPath\Scripts\Activate.ps1" }
Write-Host "  python start.py"
Write-Host "Development reload: python start.py --reload"
