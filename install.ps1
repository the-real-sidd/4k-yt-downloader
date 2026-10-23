# 4K YT Downloader: install or update with one command (Windows 10 and 11).
#
#   irm https://raw.githubusercontent.com/the-real-sidd/4k-yt-downloader/main/install.ps1 | iex
#
# Paste that into PowerShell (Start menu, type PowerShell, press Enter) and press Enter.
# What it does, every time it runs:
#   1. Downloads the latest Windows installer from this project's GitHub releases.
#   2. Closes the app if it's open and installs the new version, for this Windows user only
#      (no administrator password), with Start menu and desktop shortcuts.
#   3. Opens the app. The first time, the app then downloads the free tools it uses
#      (yt-dlp, ffmpeg, deno; about 150 MB) by itself.
# Run it again any time to update. Nothing has to be uninstalled by hand. Installed this way,
# Windows doesn't show its "Windows protected your PC" warning.
#
# Options (environment variables), mostly for testing:
#   $env:YTD_SETUP = '<path or url>'   use this installer instead of the latest release
#   $env:YTD_VERSION = '3.1'           install that release instead of the latest
#   $env:YTD_NO_OPEN = '1'             don't open the app at the end
#   $env:YTD_FORCE = '1'               reinstall even if this version is already installed

& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'  # Windows PowerShell's progress bar makes downloads very slow
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

  $Repo = 'the-real-sidd/4k-yt-downloader'
  $App = '4K YT Downloader'
  $Asset = '4K-YT-Downloader-Setup.exe'
  $UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C5E2D47-3F1B-4C7A-9E64-2B7D41A9C3F5}_is1'

  function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
  function Done($text) { Write-Host "[OK] $text" -ForegroundColor Green }
  function Installed { Get-ItemProperty -Path $UninstallKey -ErrorAction SilentlyContinue }

  try {
    Write-Host ''
    Write-Host "$App installer" -ForegroundColor White
    Write-Host ''
    if ([Environment]::OSVersion.Version.Major -lt 10) { throw "$App needs Windows 10 or 11." }
    if (-not [Environment]::Is64BitOperatingSystem) { throw "$App needs 64-bit Windows." }

    $before = Installed
    $latest = $null
    $url = $null
    if ($env:YTD_SETUP) {
      $url = $env:YTD_SETUP
    } else {
      if ($env:YTD_VERSION) {
        $tag = 'v' + $env:YTD_VERSION.TrimStart('v')
      } else {
        try {
          $release = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = '4K-YT-Downloader-installer' }
          $tag = $release.tag_name
        } catch {
          $tag = $null  # GitHub's API limits how often it's asked; the download link below still works
        }
      }
      if ($tag) {
        $latest = $tag.TrimStart('v')
        $url = "https://github.com/$Repo/releases/download/$tag/$Asset"
      } else {
        $url = "https://github.com/$Repo/releases/latest/download/$Asset"
      }
    }

    if ($latest -and $before -and $before.DisplayVersion -eq $latest -and -not $env:YTD_FORCE) {
      Done "$App $latest is already installed and up to date."
    } else {
      $setup = Join-Path $env:TEMP ("4K-YT-Downloader-Setup-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.exe')
      if ($url -match '^https?://') {
        Step "Downloading $App$(if ($latest) { " $latest" })"
        try {
          Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $setup
        } catch {
          throw "Couldn't download $url. Check your internet connection and try again."
        }
      } else {
        Copy-Item -LiteralPath $url -Destination $setup
      }

      # Close the copy being replaced (only the one in the install folder). Downloads in progress stop.
      if ($before -and $before.InstallLocation) {
        $folder = $before.InstallLocation.TrimEnd('\')
        $running = Get-Process -Name $App -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($folder, [StringComparison]::OrdinalIgnoreCase) }
        if ($running) {
          Step "Closing $App to update it"
          $running | Stop-Process -Force
          Start-Sleep -Seconds 1
        }
      }

      Step "Installing $App$(if ($before) { " (replacing $($before.DisplayVersion))" })"
      $process = Start-Process -FilePath $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', '/FORCECLOSEAPPLICATIONS' -Wait -PassThru
      Remove-Item -LiteralPath $setup -Force -ErrorAction SilentlyContinue
      if ($process.ExitCode -ne 0) { throw "The installer stopped (code $($process.ExitCode)). Run the command again." }
      $now = Installed
      if (-not $now) { throw "The installer finished but $App isn't installed. Run the command again." }
      Done "Installed $App $($now.DisplayVersion) in $($now.InstallLocation.TrimEnd('\'))"
    }

    $now = Installed
    $exe = Join-Path $now.InstallLocation "$App.exe"
    if (-not $env:YTD_NO_OPEN) {
      Start-Process -FilePath $exe
      Done "Opened $App. It's also in the Start menu."
    }
    Write-Host ''
    Write-Host "$App $($now.DisplayVersion) is ready. Run the same command again any time to update." -ForegroundColor Green
    Write-Host ''
  } catch {
    Write-Host ''
    Write-Host "Something went wrong: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
  }
}
