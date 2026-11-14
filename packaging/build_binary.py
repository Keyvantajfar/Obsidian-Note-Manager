"""Build the desktop app for the current OS:  python packaging/build_binary.py
  Windows  dist/vaultmgr-windows-<arch>.exe          single file, no console window
  macOS    dist/vaultmgr-macos-<arch>.app.zip        VaultMgr.app bundle (zipped)
  Linux    dist/vaultmgr-linux-<arch>                single file
(pip install -e ".[dev]" first.)  The command line still works from a terminal:  vaultmgr-... serve --demo | cli ... | version"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
src = root / "src"
static = src / "vaultweb" / "static"
dist = root / "dist"
sep = ";" if sys.platform == "win32" else ":"
os_name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
arch = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")

# nothing in the desktop app uses these (the TUI is not bundled: a windowed app has no terminal); leaving them out shrinks the binary
EXCLUDES = ["tkinter", "unittest", "pydoc", "doctest", "test", "lib2to3", "distutils", "setuptools", "pkg_resources",
            "pip", "wheel", "numpy", "PIL", "yaml", "IPython", "matplotlib", "scipy", "pandas", "cryptography",
            "textual", "rich", "vaulttui", "pytest", "webview"]

cmd = [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--name", "VaultMgr" if os_name == "macos" else "vaultmgr",
       "--paths", str(src),
       "--add-data", f"{static}{sep}vaultweb/static",
       "--collect-submodules", "vaultcore", "--collect-submodules", "vaultweb", "--collect-submodules", "uvicorn",
       "--distpath", str(dist / "pyi"), "--workpath", str(root / "build"), "--specpath", str(root / "build")]
for m in EXCLUDES:
    cmd += ["--exclude-module", m]
icon = root / "packaging" / "icon.png"
if os_name == "windows":
    cmd += ["--onefile", "--noconsole", "--icon", str(icon)]
elif os_name == "macos":
    cmd += ["--onedir", "--windowed", "--icon", str(icon), "--osx-bundle-identifier", "app.vaultmgr.VaultMgr"]
else:
    cmd += ["--onefile", "--strip"]
cmd.append(str(root / "packaging" / "vaultmgr_entry.py"))
subprocess.check_call(cmd, cwd=root)

dist.mkdir(exist_ok=True)
if os_name == "macos":
    app = dist / "pyi" / "VaultMgr.app"
    out = dist / f"vaultmgr-macos-{arch}.app.zip"
    subprocess.check_call(["ditto", "-c", "-k", "--keepParent", str(app), str(out)])   # keeps symlinks and permissions
else:
    exe = dist / "pyi" / ("vaultmgr.exe" if os_name == "windows" else "vaultmgr")
    out = dist / f"vaultmgr-{os_name}-{arch}{'.exe' if os_name == 'windows' else ''}"
    shutil.move(str(exe), out)
shutil.rmtree(dist / "pyi", ignore_errors=True)
print("built", out, f"{out.stat().st_size / 1e6:.1f} MB")
