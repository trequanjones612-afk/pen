# Launch the MSYS2 build of NeoMutt from Windows PowerShell 5.1 or Windows Terminal.
#
# Examples, from this directory:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run-neomutt.ps1 -Build
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run-neomutt.ps1 -v
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\run-neomutt.ps1 -F ..\proton-bridge.neomuttrc
#
# -Build runs scripts\build-msys2.sh inside the MSYS shell (not UCRT64).
# Any other arguments are passed to neomutt.exe.
#
# The executable is a Windows console program, and it loads msys-2.0.dll from
# C:\msys64\usr\bin. This script puts that directory on Path for the process.

$Build = $false
$MsysRoot = "C:\msys64"
$NeomuttArgs = @()

for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq "-Build") {
        $Build = $true
        continue
    }
    if ($args[$i] -eq "-MsysRoot") {
        $i++
        if ($i -ge $args.Count) {
            Write-Error "-MsysRoot needs a path, for example -MsysRoot D:\msys64"
            exit 1
        }
        $MsysRoot = $args[$i]
        continue
    }
    $NeomuttArgs += $args[$i]
}

$bash = Join-Path $MsysRoot "usr\bin\bash.exe"
$neomutt = Join-Path $MsysRoot "usr\bin\neomutt.exe"
$cygpath = Join-Path $MsysRoot "usr\bin\cygpath.exe"

if (-not (Test-Path $bash)) {
    Write-Host "MSYS2 was not found at $MsysRoot (missing usr\bin\bash.exe)."
    Write-Host "Install MSYS2 from https://www.msys2.org/ with the default location C:\msys64,"
    Write-Host "open the MSYS2 MSYS shell once so it can finish setup, then re-run this script."
    Write-Host "If you installed it somewhere else, pass -MsysRoot."
    exit 1
}

if ($Build) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $buildScript = Join-Path $scriptDir "build-msys2.sh"
    if (-not (Test-Path $buildScript)) {
        Write-Error "Cannot find $buildScript"
        exit 1
    }
    $msysScript = (& $cygpath -u $buildScript | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $msysScript) {
        Write-Error "cygpath failed on $buildScript"
        exit 1
    }
    & $bash -lc "bash '$msysScript'"
    exit $LASTEXITCODE
}

if (-not (Test-Path $neomutt)) {
    Write-Host "neomutt.exe is not in $MsysRoot\usr\bin."
    Write-Host "Either re-run this script with -Build, or in the MSYS2 MSYS shell run:"
    Write-Host "    pacman -S neomutt"
    exit 1
}

$usrBin = Join-Path $MsysRoot "usr\bin"
if ($env:Path -notlike "*${usrBin}*") {
    $env:Path = $usrBin + ";" + $env:Path
}
if (-not $env:TERM) {
    $env:TERM = "xterm-256color"
}
if (-not $env:LANG) {
    $env:LANG = "C.UTF-8"
}

& $neomutt @NeomuttArgs
exit $LASTEXITCODE
