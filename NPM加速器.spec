# -*- mode: python ; coding: utf-8 -*-
import datetime
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

# ---------------- 编译期版本信息（唯一来源：version.py）----------------
# 每次编译都生成两个文件（都已在 .gitignore 里忽略）：
#   build_info.py     —— 运行时读取，用于窗口标题与自检输出
#   version_info.txt  —— 写进 exe 的 Windows 文件属性（右键 → 属性 → 详细信息）
sys.path.insert(0, SPECPATH)
import version as app_version  # noqa: E402

_now = datetime.datetime.now()
_ver_quad = tuple(int(x) for x in (app_version.__version__.split(".") + ["0", "0", "0"])[:4])

with open(os.path.join(SPECPATH, "build_info.py"), "w", encoding="utf-8", newline="\n") as _fh:
    _fh.write("\n".join([
        "# -*- coding: utf-8 -*-",
        '"""由 NPM加速器.spec 编译时自动生成，请勿手改（已在 .gitignore 中忽略）。"""',
        f'VERSION = "{app_version.__version__}"',
        f'BUILD_DATE = "{_now:%Y-%m-%d}"',
        f'BUILD_STAMP = "{_now:%Y-%m-%d %H:%M:%S}"',
        "",
    ]))

with open(os.path.join(SPECPATH, "version_info.txt"), "w", encoding="utf-8", newline="\n") as _fh:
    _fh.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_ver_quad}, prodvers={_ver_quad},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404b0', [
      StringStruct('CompanyName', '{app_version.AUTHOR}'),
      StringStruct('FileDescription', '{app_version.APP_NAME} ({app_version.PROJECT_NAME})'),
      StringStruct('FileVersion', '{app_version.__version__}'),
      StringStruct('InternalName', '{app_version.PROJECT_NAME}'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'NPM加速器.exe'),
      StringStruct('ProductName', '{app_version.APP_NAME}'),
      StringStruct('ProductVersion', '{app_version.__version__} (build {_now:%Y-%m-%d})'),
    ])]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""")

print(f"[spec] {app_version.APP_NAME} v{app_version.__version__} · 编译日期 {_now:%Y-%m-%d}")

# 必须显式收集 certifi 的 CA 证书包（certifi/cacert.pem）。
# 实测：本机 PyInstaller 6.10 + pyinstaller-hooks-contrib 2025.9 下，
# 社区 hook 并没有把 cacert.pem 打进 exe，导致打包后所有 HTTPS 请求失败：
#   requests -> OSError: Could not find a suitable TLS CA certificate bundle
# 表现为「镜像测速全部失败」「本地代理回源全部 502」。
datas = collect_data_files('certifi')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['build_info'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NPM加速器',
    version=os.path.join(SPECPATH, 'version_info.txt'),
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
