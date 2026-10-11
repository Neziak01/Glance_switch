# PyInstaller spec: pyinstaller packaging/GlanceSwitch.spec  (run from repo root)
# Expects models/face_landmarker.task (and packaging/glance_switch.ico: run packaging/prepare_build.py first).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))


def _keep(name):
    return ".test" not in name and "benchmark" not in name


hiddenimports = collect_submodules("mediapipe", filter=_keep) + collect_submodules("pystray")
datas = collect_data_files("mediapipe", excludes=["**/test*/**", "**/*_test.py"])
datas.append((os.path.join(ROOT, "models", "face_landmarker.task"), "models"))
binaries = collect_dynamic_libs("mediapipe")

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["sounddevice", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GlanceSwitch",
    console=False,
    icon=os.path.join(ROOT, "packaging", "glance_switch.ico"),
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GlanceSwitch")
