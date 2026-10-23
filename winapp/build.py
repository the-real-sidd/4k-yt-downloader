"""Builds the Windows app and its installer. Runs on Windows (GitHub Actions does it, see
.github/workflows/windows.yml):

    pip install -r winapp/requirements.txt
    python winapp/build.py

Output:
    dist/4K YT Downloader/              the app (PyInstaller, one folder)
    dist/4K-YT-Downloader-Setup.exe     the installer people download (Inno Setup 6)
The version comes from the VERSION file.
"""

from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build" / "windows"
NAME = "4K YT Downloader"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'the.real.sidd'),
      StringStruct('FileDescription', '4K YT Downloader'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '4K YT Downloader'),
      StringStruct('LegalCopyright', 'Built by the.real.sidd. Uses yt-dlp and FFmpeg.'),
      StringStruct('OriginalFilename', '4K YT Downloader.exe'),
      StringStruct('ProductName', '4K YT Downloader'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def find_iscc() -> str:
    """Inno Setup's compiler (preinstalled on GitHub's Windows machines; else choco install innosetup)."""
    for pattern in (r"C:\Program Files (x86)\Inno Setup*\ISCC.exe", r"C:\Program Files\Inno Setup*\ISCC.exe"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    sys.exit("Inno Setup not found. Install it with:  choco install innosetup")


def main() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    numbers = tuple(([int(part) for part in re.findall(r"\d+", version)] + [0, 0, 0, 0])[:4])
    BUILD.mkdir(parents=True, exist_ok=True)
    version_file = BUILD / "version_info.txt"
    version_file.write_text(VERSION_INFO.format(numbers=numbers, version=version), encoding="utf-8")

    print(f"→ Building {NAME} {version} with PyInstaller")
    from PyInstaller.__main__ import run
    run([
        str(HERE / "app.py"),
        "--name", NAME, "--windowed", "--noconfirm", "--clean",
        "--icon", str(HERE / "AppIcon.ico"),
        "--version-file", str(version_file),
        "--paths", str(ROOT), "--paths", str(HERE),
        "--hidden-import", "ytdl", "--hidden-import", "ytdl_web",
        "--hidden-import", "winsys", "--hidden-import", "wintools",
        "--add-data", f"{ROOT / 'ytdl_web.html'}{os.pathsep}.",
        "--add-data", f"{ROOT / 'VERSION'}{os.pathsep}.",
        "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"), "--specpath", str(BUILD),
    ])
    app = DIST / NAME
    if not (app / f"{NAME}.exe").is_file():
        sys.exit("PyInstaller didn't produce the app.")

    print("→ Building the installer with Inno Setup")
    subprocess.run([
        find_iscc(), "/Qp",
        f"/DAppVersion={version}", f"/DFileVersion={'.'.join(map(str, numbers))}",
        f"/DSourceDir={app}", f"/DOutputDir={DIST}", f"/DIconFile={HERE / 'AppIcon.ico'}",
        str(HERE / "installer.iss"),
    ], check=True)
    setup = DIST / "4K-YT-Downloader-Setup.exe"
    print(f"✔ {setup} ({setup.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
