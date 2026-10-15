# -*- coding: utf-8 -*-
"""版本、作者与展示信息（唯一来源）。

编译日期由打包脚本在每次构建时写入 build_info.py（该文件不提交仓库）：
    VERSION / BUILD_DATE / BUILD_STAMP
源码直接运行时没有这个文件，标题里就显示「源码运行」而不是伪造一个构建日期。
"""
__version__ = "1.0.0"
APP_NAME = "NPM 加速器"
PROJECT_NAME = "npm-boost"
AUTHOR = "liruitao"
REPO_URL = "https://github.com/Liruitao1993/npm-boost"

try:
    from build_info import BUILD_DATE as _BUILD_DATE
    from build_info import BUILD_STAMP as _BUILD_STAMP
except Exception:  # 源码态：还没编译过
    _BUILD_DATE = ""
    _BUILD_STAMP = ""

BUILD_DATE = _BUILD_DATE
BUILD_STAMP = _BUILD_STAMP


def is_build() -> bool:
    """当前运行的是编译产物（而不是源码直跑）。"""
    return bool(BUILD_DATE)


def version_text() -> str:
    """形如：v1.0.0 · 构建 2026-10-15 · liruitao"""
    parts = [f"v{__version__}"]
    parts.append(f"构建 {BUILD_DATE}" if BUILD_DATE else "源码运行")
    parts.append(AUTHOR)
    return " · ".join(parts)


def window_title() -> str:
    """窗口标题栏文案（版本 / 编译日期 / 作者）。"""
    return f"{APP_NAME} {version_text()}"


def about_text() -> str:
    return (f"{APP_NAME}　{version_text()}\n"
            f"项目代号：{PROJECT_NAME}　作者：{AUTHOR}\n"
            f"{REPO_URL}")
