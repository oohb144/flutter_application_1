# Workaround for media_kit Windows build failure.
#
# Problem: media_kit_libs_windows_video extracts .7z archives via
# `cmake -E tar xzf`, but the CMake bundled with Visual Studio has a
# libarchive build without 7z support, so extraction fails (MSB3073).
#
# This script pre-extracts ANGLE.7z and the libmpv .7z with system 7-Zip
# into the target dirs, so media_kit's CMakeLists skips extraction
# (check_directory_exists_and_not_empty).
#
# Prereq: install 7-Zip (winget install 7zip.7zip), run
# `flutter build windows --debug` once so the .7z archives are downloaded
# (extraction failure is fine), then run this script, then re-run the build.
#
# Usage: pwsh scripts/prepare_windows_build.ps1

$ErrorActionPreference = "Stop"

$buildDir = "build/windows/x64"
if (-not (Test-Path $buildDir)) {
    Write-Error "Not found: $buildDir. Run 'flutter build windows --debug' once first to download archives."
    exit 1
}

# Locate 7-Zip
$sevenz = $null
foreach ($p in @(
    "C:\Program Files\7-Zip\7z.exe",
    "C:\Program Files (x86)\7-Zip\7z.exe"
)) { if (Test-Path $p) { $sevenz = $p; break } }
if (-not $sevenz) { $sevenz = (Get-Command 7z.exe -ErrorAction SilentlyContinue).Source }
if (-not $sevenz) {
    Write-Error "7-Zip not found. Install: winget install 7zip.7zip"
    exit 1
}
Write-Host "Using 7-Zip: $sevenz"

# ---- ANGLE ----
$angleDir = Join-Path $buildDir "ANGLE"
$angleArchive = Join-Path $buildDir "ANGLE.7z"
if ((Test-Path $angleArchive) -and -not (Test-Path (Join-Path $angleDir "include"))) {
    Write-Host "Extracting ANGLE.7z -> $angleDir"
    if (Test-Path $angleDir) { Remove-Item $angleDir -Recurse -Force }
    & $sevenz x $angleArchive "-o$angleDir" -y | Out-Null
} else {
    Write-Host "ANGLE already extracted, skipping"
}

# ---- libmpv ----
$libmpvDir = Join-Path $buildDir "libmpv"
$libmpvArchive = Get-ChildItem (Join-Path $buildDir "mpv-dev-*.7z") -ErrorAction SilentlyContinue | Select-Object -First 1
if ($libmpvArchive -and -not (Test-Path (Join-Path $libmpvDir "include"))) {
    Write-Host "Extracting $($libmpvArchive.Name) -> $libmpvDir"
    if (Test-Path $libmpvDir) { Remove-Item $libmpvDir -Recurse -Force }
    & $sevenz x $libmpvArchive.FullName "-o$libmpvDir" -y | Out-Null
    # Re-arrange (equivalent to CMakeLists xcopy/rmdir/ren):
    # original libmpv/include/mpv/* -> libmpv/include/*
    $mpvSub = Join-Path $libmpvDir "include/mpv"
    if (Test-Path $mpvSub) {
        $tmp = Join-Path $libmpvDir "mpv_tmp"
        Move-Item $mpvSub $tmp
        Remove-Item (Join-Path $libmpvDir "include") -Recurse -Force
        Rename-Item $tmp "include"
    }
} else {
    Write-Host "libmpv already extracted, skipping"
}

Write-Host "Done. Re-run: flutter build windows --debug"
