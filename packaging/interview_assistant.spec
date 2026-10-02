# -*- mode: python ; coding: utf-8 -*-
# Build with:  python scripts/build.py   (which also writes packaging/version_info.txt)
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

ROOT = Path(SPECPATH).parent
VERSION_FILE = ROOT / "packaging" / "version_info.txt"
ICON = ROOT / "packaging" / "app.ico"

datas = collect_data_files("faster_whisper") + collect_data_files("certifi")
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
hiddenimports = (
    collect_submodules("keyring.backends")
    + collect_submodules("interview_assistant")
    + ["pyaudiowpatch"]
)

# Things the app never imports; leaving them out keeps the download smaller.
excludes = [
    "PIL", "tkinter", "matplotlib", "IPython", "pytest", "mypy", "ruff", "pandas", "scipy",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtWebSockets",
    "PySide6.QtWebChannel", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtSpatialAudio", "PySide6.QtGraphs", "PySide6.QtVirtualKeyboard",
]

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# Bundled Qt files a widgets-only app never loads (software OpenGL fallback, Quick/QML/PDF runtimes,
# Qt's own UI translations): ~60 MB.
DROP_BINARIES = (
    "opengl32sw", "Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6VirtualKeyboard", "Qt6OpenGL",
    "qpdf.dll", "qtvirtualkeyboardplugin",  # plugins that need the DLLs above
)
a.binaries = [b for b in a.binaries if not any(d in Path(b[0]).name for d in DROP_BINARIES)]
a.datas = [d for d in a.datas if "PySide6/translations" not in d[0].replace("\\", "/")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InterviewAssistant",
    debug=False,
    strip=False,
    upx=False,  # UPX-packed binaries trigger more antivirus false positives
    console=False,  # windowed app; CLI switches write to --out / the parent console
    icon=str(ICON) if ICON.exists() else None,
    version=str(VERSION_FILE) if VERSION_FILE.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="InterviewAssistant")
