# -*- coding: utf-8 -*-
"""主窗口：加速中心 / 镜像源 / 本地缓存代理 / GitHub 加速 / 项目助手 / 日志。"""
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QStatusBar, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

import github_boost
import npm_manager
import proxy_server
import theme
import version
from workers import (EnvInfoThread, GithubActionThread, GithubSpeedThread,
                     NpmCommandThread, ProxyActionThread, SpeedTestThread)


def card(title: str = None) -> tuple:
    """创建卡片容器，返回 (frame, 内部 vbox)。

    外观（底色、边框、标题字号）全部交给 theme.py 的 QSS 规则
    （QFrame.card / QLabel[cardTitle]），这里不写任何色值。
    """
    frame = QFrame()
    frame.setProperty("class", "card")
    vbox = QVBoxLayout(frame)
    vbox.setContentsMargins(16, 14, 16, 14)
    vbox.setSpacing(8)
    if title:
        lbl = QLabel(title)
        lbl.setProperty("cardTitle", True)
        vbox.addWidget(lbl)
    return frame, vbox

class MainWindow(QMainWindow):
    log_signal = Signal(str)

    def __init__(self, theme_manager=None):
        super().__init__()
        self.setWindowTitle(version.window_title())
        self.resize(980, 680)
        # 后台线程引用：QThread 运行中被 Python 回收会让进程直接崩溃
        self._jobs = []
        self.speed_thread = None
        self.cmd_thread = None
        self.env_thread = None
        self.proxy_thread = None
        self._prev_registry = None  # 启动代理前的 registry（None = 原本没配）
        # 主题管理器：外部可注入（测试用临时配置），默认读用户设置并跟随系统
        self.theme = theme_manager if theme_manager is not None else theme.ThemeManager(parent=self)
        self.theme.changed.connect(self._on_theme_changed)

        self._build_ui()
        self.log_signal.connect(self._append_log)
        self._repair_dangling_proxy_registry()
        self.refresh_env()
        self.refresh_proxy_panel()
        self.refresh_github_panel()

        # 代理统计刷新定时器
        self.stat_timer = QTimer(self)
        self.stat_timer.timeout.connect(self.refresh_proxy_stats)
        self.stat_timer.start(2000)

        # GitHub 加速：状态轻量刷新 + 优选 IP 定期重测（GitHub 的 IP 会失效）
        self.github_thread = None
        self.github_speed_thread = None
        self.github_git_thread = None
        self.github_timer = QTimer(self)
        self.github_timer.timeout.connect(self.refresh_github_stats)
        self.github_timer.start(2000)
        self.github_speed_timer = QTimer(self)
        self.github_speed_timer.timeout.connect(self._auto_refresh_github)
        self.github_speed_timer.start(600000)

    # ================= UI 构建 =================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 10)
        root.setSpacing(12)

        # ---- 顶部标题栏 ----
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        t = QLabel("NPM 加速器")
        t.setObjectName("appTitle")
        st = QLabel("一键加速 npm 安装与 GitHub 访问：国内镜像 · 本地缓存 · 极速参数 · GitHub 优选 IP")
        st.setObjectName("appSubtitle")
        title_box.addWidget(t)
        title_box.addWidget(st)
        self.version_label = QLabel(version.version_text())
        self.version_label.setProperty("role", "hint")
        self.version_label.setToolTip(version.about_text())
        title_box.addWidget(self.version_label)
        header.addLayout(title_box)
        header.addStretch()
        self.theme_combo = QComboBox()
        for mode in theme.MODES:
            self.theme_combo.addItem(theme.MODE_LABELS[mode], mode)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.theme.mode())))
        self.theme_combo.setToolTip("主题：跟随系统 / 浅色 / 深色")
        self.theme_combo.currentIndexChanged.connect(self._on_theme_mode_changed)
        header.addWidget(self.theme_combo)
        self.badge = QLabel("检查中…")
        self.badge.setObjectName("statusBadge")
        header.addWidget(self.badge)
        root.addLayout(header)

        # ---- 标签页 ----
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._tab_home(), "加速中心")
        self.tabs.addTab(self._tab_registry(), "镜像源")
        self.tabs.addTab(self._tab_proxy(), "本地缓存代理")
        self.tabs.addTab(self._tab_github(), "GitHub 加速")
        self.tabs.addTab(self._tab_project(), "项目助手")
        self.tabs.addTab(self._tab_log(), "日志")

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")

    # ---------- 加速中心 ----------
    def _tab_home(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(12)

        # 环境信息卡
        env_card, ev = card("当前环境")
        grid = QGridLayout()
        grid.setHorizontalSpacing(30)
        self.env_labels = {}
        for i, key in enumerate(["npm 版本", "Node 版本", "当前镜像源", "缓存目录", "缓存大小"]):
            k = QLabel(key)
            k.setProperty("role", "hint")
            val = QLabel("…")
            val.setProperty("role", "value")
            self.env_labels[key] = val
            grid.addWidget(k, i // 3, (i % 3) * 2)
            grid.addWidget(val, i // 3, (i % 3) * 2 + 1)
        ev.addLayout(grid)
        v.addWidget(env_card)

        # 一键加速卡
        fast_card, fv = card("一键加速（推荐配置）")
        hint = QLabel(
            "写入国内镜像 + 开启极速参数（提高并发、关闭耗时的 audit/fund 检查）+ 二进制包国内镜像。\n"
            "首次加速前会自动把 ~/.npmrc 备份为 ~/.npmrc.bak，随时可一键还原。"
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "hint")
        fv.addWidget(hint)

        row = QHBoxLayout()
        self.chk_fast = QCheckBox("极速参数（maxsockets=15、prefer-offline 等）")
        self.chk_fast.setChecked(True)
        self.chk_binary = QCheckBox("二进制包国内镜像（electron / node-sass / puppeteer 等）")
        self.chk_binary.setChecked(True)
        row.addWidget(self.chk_fast)
        row.addWidget(self.chk_binary)
        row.addStretch()
        fv.addLayout(row)

        btn_row = QHBoxLayout()
        self.btn_apply_fast = QPushButton("⚡ 一键加速")
        self.btn_apply_fast.setObjectName("primaryBtn")
        self.btn_apply_fast.clicked.connect(self.apply_fast)
        self.btn_restore = QPushButton("恢复备份（~/.npmrc.bak）")
        self.btn_restore.setToolTip("把 ~/.npmrc 还原为首次加速之前的配置")
        self.btn_restore.clicked.connect(self.restore_backup)
        btn_row.addWidget(self.btn_apply_fast)
        btn_row.addWidget(self.btn_restore)
        btn_row.addStretch()
        fv.addLayout(btn_row)
        v.addWidget(fast_card)

        # 测速卡
        speed_card, sv = card("镜像源测速")
        self.btn_speed = QPushButton("开始测速")
        self.btn_speed.clicked.connect(self.run_speedtest)
        self.speed_result = QLabel("点击「开始测速」比较各镜像源延迟")
        self.speed_result.setProperty("role", "hint")
        sv.addWidget(self.speed_result)
        sv.addWidget(self.btn_speed)
        v.addWidget(speed_card)

        # 缓存管理卡
        cache_card, cv = card("npm 缓存管理")
        crow = QHBoxLayout()
        self.btn_verify = QPushButton("校验并修复缓存")
        self.btn_verify.clicked.connect(lambda: self.run_bg_npm(["cache", "verify"], "正在校验 npm 缓存…"))
        btn_clean = QPushButton("清理 npm 缓存")
        btn_clean.setObjectName("dangerBtn")
        btn_clean.clicked.connect(self.clean_cache)
        self.btn_clear_proxy_cache = QPushButton("清空代理缓存")
        self.btn_clear_proxy_cache.setObjectName("dangerBtn")
        self.btn_clear_proxy_cache.clicked.connect(self.clear_proxy_cache)
        crow.addWidget(self.btn_verify)
        crow.addWidget(btn_clean)
        crow.addWidget(self.btn_clear_proxy_cache)
        crow.addStretch()
        cv.addLayout(crow)
        v.addWidget(cache_card)

        v.addStretch()
        return w

    # ---------- 镜像源 ----------
    def _tab_registry(self):
        w = QWidget()
        v = QVBoxLayout(w)
        c, cv = card("选择镜像源（写入 .npmrc 的 registry）")
        v.addWidget(c)
        self.reg_layout = cv
        self.reg_group = QButtonGroup(self)
        tip = QLabel("切换后立即生效；首次操作时会自动备份原配置到 ~/.npmrc.bak")
        tip.setProperty("role", "hint")
        cv.addWidget(tip)
        self.refresh_registry_list()
        v.addStretch()
        return w

    def refresh_registry_list(self, initial=False):
        """重建镜像源单选列表。

        只清理 QRadioButton，卡片里的说明文字保持在原位。
        """
        for i in reversed(range(self.reg_layout.count())):
            item = self.reg_layout.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, QRadioButton):
                self.reg_layout.takeAt(i)
                self.reg_group.removeButton(widget)
                widget.deleteLater()
        current = npm_manager.current_registry()
        for reg in npm_manager.REGISTRIES:
            rb = QRadioButton(f"{reg['name']}   ·   {reg['url']}")
            rb.setChecked(current.rstrip("/") == reg["url"].rstrip("/"))
            rb.toggled.connect(lambda on, r=reg: self.pick_registry(r, on))
            self.reg_group.addButton(rb)
            self.reg_layout.addWidget(rb)

    def pick_registry(self, reg, checked):
        if not checked:
            return
        if proxy_server.proxy_stats()["running"]:
            self._log("本地代理正在运行，请先停止代理再切换镜像源。")
            self.refresh_registry_list()
            return
        npm_manager.backup_npmrc()
        npm_manager.apply_registry(reg["url"])
        self._log(f"镜像源切换为 {reg['name']}（{reg['url']}）")
        self.statusBar().showMessage(f"已切换镜像源：{reg['name']}", 5000)
        self.refresh_env()

    # ---------- 本地缓存代理 ----------
    def _tab_proxy(self):
        w = QWidget()
        v = QVBoxLayout(w)

        c1, v1 = card("本地缓存代理")
        desc = QLabel(
            "在本机启动一个缓存代理：首次下载的包缓存到本地，之后同一台机器上的重复安装直接秒取，"
            "团队多人共用时也能大幅省流量。开启后自动把 npm registry 指向 127.0.0.1，"
            "元数据与 tarball 都会走本地缓存。"
        )
        desc.setWordWrap(True)
        desc.setProperty("role", "hint")
        v1.addWidget(desc)

        prow = QHBoxLayout()
        self.btn_proxy_toggle = QPushButton("启动本地代理")
        self.btn_proxy_toggle.setObjectName("primaryBtn")
        self.btn_proxy_toggle.clicked.connect(self.toggle_proxy)
        prow.addWidget(self.btn_proxy_toggle)
        self.proxy_status = QLabel("未运行")
        self.proxy_status.setProperty("role", "hint")
        prow.addWidget(self.proxy_status)
        prow.addStretch()
        v1.addLayout(prow)

        srow = QHBoxLayout()
        self.lbl_hits = QLabel("命中 0")
        self.lbl_miss = QLabel("回源 0")
        self.lbl_rate = QLabel("命中率 —")
        self.lbl_saved = QLabel("省下 0 MB")
        self.lbl_rewrite = QLabel("tarball 走缓存 0 个")
        for l in (self.lbl_hits, self.lbl_miss, self.lbl_rate, self.lbl_saved, self.lbl_rewrite):
            l.setProperty("role", "stat")
            srow.addWidget(l)
        srow.addStretch()
        v1.addLayout(srow)
        v.addWidget(c1)

        c2, v2 = card("代理日志")
        self.proxy_log = QPlainTextEdit()
        self.proxy_log.setReadOnly(True)
        self.proxy_log.setMaximumHeight(220)
        self.proxy_log.setPlaceholderText("启动代理后，npm 的每次请求都会显示在这里…")
        v2.addWidget(self.proxy_log)
        v.addWidget(c2)

        v.addStretch()
        return w

    def toggle_proxy(self):
        running = proxy_server.proxy_stats()["running"]
        if not running:
            # 记住用户原本的源，停止代理时才能精确还原
            self._prev_registry = npm_manager.raw_registry()
            npm_manager.backup_npmrc()
        self.proxy_thread = self._keep(ProxyActionThread("stop" if running else "start", parent=self))
        self.proxy_thread.finished_sig.connect(self._on_proxy_toggled)
        self.btn_proxy_toggle.setEnabled(False)
        self.proxy_thread.start()

    def _on_proxy_toggled(self, ok, msg):
        self.btn_proxy_toggle.setEnabled(True)
        self._log(msg)
        if not ok:
            QMessageBox.warning(self, "代理", msg)
            return
        if proxy_server.proxy_stats()["running"]:
            npm_manager.apply_registry(proxy_server.proxy_registry_url())
            self.btn_proxy_toggle.setText("停止本地代理")
            self._log("已把 npm 源指向本地代理，安装时元数据与 tarball 都会走本地缓存。")
        else:
            npm_manager.restore_registry(self._prev_registry)
            self.btn_proxy_toggle.setText("启动本地代理")
            self._log(f"代理已停止，npm 源恢复为 {npm_manager.current_registry()}")
        self.refresh_registry_list()
        self.refresh_env()
        self.refresh_proxy_stats()

    def refresh_proxy_panel(self):
        st = proxy_server.proxy_stats()
        self.btn_proxy_toggle.setText("停止本地代理" if st["running"] else "启动本地代理")
        self.refresh_proxy_stats()

    def refresh_proxy_stats(self):
        st = proxy_server.proxy_stats()
        running = st["running"]
        self.proxy_status.setText(
            f"运行中 · 端口 {proxy_server.PROXY_PORT} · 上游 {st['upstream']}" if running else "未运行"
        )
        mb = st["bytes_saved"] / 1024 ** 2
        self.lbl_hits.setText(f"命中 {st['hits']}")
        self.lbl_miss.setText(f"回源 {st['misses']}")
        self.lbl_rate.setText(f"命中率 {st['hit_rate']}")
        self.lbl_saved.setText(f"省下 {mb:.1f} MB" if mb >= 0.1 else "省下 0 MB")
        self.lbl_rewrite.setText(f"tarball 走缓存 {st.get('rewrites', 0)} 个")
        if running:
            self.proxy_log.setPlainText("\n".join(st["log"][-14:]))
            sb = self.proxy_log.verticalScrollBar()
            sb.setValue(sb.maximum())

    def clear_proxy_cache(self):
        self.btn_clear_proxy_cache.setEnabled(False)
        thread = self._keep(ProxyActionThread("clear_cache", parent=self))
        thread.finished_sig.connect(self._on_proxy_cache_cleared)
        thread.start()

    def _on_proxy_cache_cleared(self, ok, msg):
        self.btn_clear_proxy_cache.setEnabled(True)
        self._log(msg if ok else f"清空代理缓存失败：{msg}")
        self.refresh_proxy_stats()

    # ---------- 项目助手 ----------
    def _tab_project(self):
        w = QWidget()
        v = QVBoxLayout(w)

        c1, v1 = card("项目目录")
        prow = QHBoxLayout()
        self.proj_edit = QLineEdit()
        self.proj_edit.setPlaceholderText("选择包含 package.json 的项目文件夹…")
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self.browse_project)
        prow.addWidget(self.proj_edit, 1)
        prow.addWidget(btn_browse)
        v1.addLayout(prow)
        v.addWidget(c1)

        c2, v2 = card("加速安装")
        crow = QHBoxLayout()
        lbl = QLabel("安装命令：")
        self.cmd_combo = QComboBox()
        self.cmd_combo.addItems(["npm install", "npm install <包名>", "npm ci", "npm update", "自定义命令"])
        self.pkg_edit = QLineEdit()
        self.pkg_edit.setPlaceholderText("包名（可选，如 lodash typescript@latest）")
        crow.addWidget(lbl)
        crow.addWidget(self.cmd_combo, 2)
        crow.addWidget(self.pkg_edit, 3)
        v2.addLayout(crow)

        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("自定义完整命令，如：npm install --no-audit --no-fund")
        self.custom_edit.setVisible(False)
        v2.addWidget(self.custom_edit)
        self.cmd_combo.currentTextChanged.connect(
            lambda t: self.custom_edit.setVisible(t == "自定义命令"))

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("▶ 运行（走当前镜像源加速）")
        self.btn_run.setObjectName("primaryBtn")
        self.btn_run.clicked.connect(self.run_install)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("dangerBtn")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_install)
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch()
        v2.addLayout(btn_row)
        v.addWidget(c2)

        c3, v3 = card("输出")
        self.out_box = QPlainTextEdit()
        self.out_box.setReadOnly(True)
        self.out_box.setPlaceholderText("命令输出将显示在这里…")
        v3.addWidget(self.out_box)
        v.addWidget(c3, 1)
        return w

    def browse_project(self):
        d = QFileDialog.getExistingDirectory(self, "选择项目目录")
        if d:
            self.proj_edit.setText(d)

    def run_install(self):
        cwd = self.proj_edit.text().strip()
        if not cwd or not Path(cwd, "package.json").exists():
            QMessageBox.warning(self, "项目助手", "请先选择一个包含 package.json 的项目文件夹。")
            return
        if self.cmd_thread is not None and self.cmd_thread.isRunning():
            QMessageBox.information(self, "项目助手", "上一个命令还在运行，请先等待或点「停止」。")
            return
        cmd_text = self.cmd_combo.currentText()
        if cmd_text == "自定义命令":
            args = self.custom_edit.text().split()
        elif cmd_text == "npm install":
            args = ["install"]
        elif cmd_text == "npm install <包名>":
            args = ["install"] + self.pkg_edit.text().split()
        elif cmd_text == "npm ci":
            args = ["ci"]
        else:
            args = ["update"]
        if not args:
            QMessageBox.warning(self, "项目助手", "命令为空。")
            return
        self._log(f"在 {cwd} 执行 npm {' '.join(args)}")
        self.cmd_thread = self._keep(NpmCommandThread(args, cwd, parent=self))
        self.cmd_thread.output.connect(self._append_log)
        self.cmd_thread.done.connect(self._on_install_done)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.statusBar().showMessage("正在执行…")
        self.cmd_thread.start()

    def run_bg_npm(self, args, msg):
        """执行不占用安装按钮的 npm 命令（如 cache verify）。"""
        self._log(msg)
        thread = self._keep(NpmCommandThread(args, parent=self))
        thread.output.connect(self._append_log)
        thread.done.connect(lambda c: self._log(f"完成（退出码 {c}）"))
        thread.start()

    def _on_install_done(self, code):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.statusBar().showMessage("完成" if code == 0 else f"失败（退出码 {code}）", 8000)
        self._log(f"npm 命令结束，退出码 {code}")

    def stop_install(self):
        if self.cmd_thread is not None and self.cmd_thread.isRunning():
            self.cmd_thread.stop()
            self._log("已请求终止当前命令（含 npm 派生的子进程）。")
        else:
            self.statusBar().showMessage("当前没有正在运行的命令", 4000)

    # ---------- 日志 ----------
    def _tab_log(self):
        w = QWidget()
        v = QVBoxLayout(w)
        c, cv = card("运行日志")
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("应用操作日志…")
        cv.addWidget(self.log_box)
        btn_clear = QPushButton("清空日志")
        btn_clear.clicked.connect(self.log_box.clear)
        cv.addWidget(btn_clear)
        v.addWidget(c)
        return w

    def _append_log(self, text):
        """写日志：同时进入日志页与项目助手输出框（只经信号调用，避免跨线程操作控件）。"""
        self.log_box.appendPlainText(text)
        self.out_box.appendPlainText(text)

    def _log(self, text):
        self.log_signal.emit(text)

    # ================= 主题 =================
    def _on_theme_mode_changed(self, index):
        mode = self.theme_combo.itemData(index)
        if mode is None:
            return
        self.theme.set_mode(mode)

    def _on_theme_changed(self, scheme):
        name = "浅色" if scheme == theme.LIGHT else "深色"
        suffix = "（跟随系统）" if self.theme.mode() == theme.SYSTEM else ""
        self._log(f"主题已切换为{name}{suffix}")

    # ================= 线程管理 =================
    def _keep(self, thread):
        """登记后台线程并在其结束后注销，确保 QThread 不会被提前回收。"""
        self._jobs.append(thread)
        thread.finished.connect(lambda: self._jobs.remove(thread) if thread in self._jobs else None)
        return thread

    # ================= 动作 =================
    def refresh_env(self):
        self.env_thread = self._keep(EnvInfoThread(parent=self))
        self.env_thread.finished_sig.connect(self._on_env_ready)
        self.env_thread.start()

    def _on_env_ready(self, info):
        self.env_labels["npm 版本"].setText(info.get("npm", "未知"))
        self.env_labels["Node 版本"].setText(info.get("node", "未知"))
        self.env_labels["当前镜像源"].setText(info.get("registry", "未知"))
        self.env_labels["缓存目录"].setText(info.get("cache_dir", "未知"))
        self.env_labels["缓存大小"].setText(info.get("cache_size", "—"))
        cfg = info.get("config", {})
        self.chk_fast.setChecked(any(k in cfg for k in npm_manager.FAST_PRESETS))
        self.chk_binary.setChecked(any(k in cfg for k in npm_manager.BINARY_MIRRORS))
        reg = info.get("registry", "")
        accelerated = npm_manager.is_accelerated(reg)
        if not info.get("ca_ok", True):
            # 打包漏掉 CA 证书包时所有 HTTPS 都会失败，必须在界面上明确暴露
            self._log("⚠ 缺少 TLS CA 证书包（%s）：镜像测速与本地缓存代理都会失败，"
                      "请改用最新打包的 exe。" % info.get("ca_path"))
            self.badge.setText("证书包缺失")
            self.badge.setProperty("bad", "true")
        else:
            self.badge.setText("已加速 ✓" if accelerated else "未加速")
            self.badge.setProperty("bad", "false" if accelerated else "true")
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    def apply_fast(self):
        fast = self.chk_fast.isChecked()
        binary = self.chk_binary.isChecked()
        if not fast and not binary:
            QMessageBox.information(self, "一键加速", "请至少勾选一项加速选项。")
            return
        npm_manager.backup_npmrc()
        if fast:
            npm_manager.apply_fast_mode(True)
            self._log("极速参数已写入 .npmrc。")
        if binary:
            npm_manager.apply_binary_mirrors(True)
            self._log("二进制包国内镜像已写入 .npmrc。")
        reg = npm_manager.raw_registry()
        if reg is None or "npmjs.org" in reg or "yarnpkg" in reg:
            npm_manager.apply_registry("https://registry.npmmirror.com")
            self._log("已把 npm 源切换为 npmmirror（国内镜像）。")
        elif not npm_manager.is_accelerated(reg):
            self._log(f"当前源（{reg}）不是国内镜像，已保留不改动；需要切换请到「镜像源」页。")
        self.statusBar().showMessage("一键加速完成 ✓", 6000)
        self.refresh_registry_list()
        self.refresh_env()

    def restore_backup(self):
        if npm_manager.restore_npmrc():
            self._log("已从 ~/.npmrc.bak 恢复原配置。")
            self.statusBar().showMessage("已恢复备份", 5000)
        else:
            QMessageBox.information(
                self, "恢复备份",
                "还没有备份文件。首次执行「一键加速」或切换镜像源时会自动生成 ~/.npmrc.bak。")
        self.refresh_registry_list()
        self.refresh_env()

    def run_speedtest(self):
        self.btn_speed.setEnabled(False)
        self.speed_result.setText("测速中…（并发请求各镜像源）")
        self.speed_thread = self._keep(SpeedTestThread(parent=self))
        self.speed_thread.finished_sig.connect(self._on_speed_done)
        self.speed_thread.start()

    def _on_speed_done(self, results):
        self.btn_speed.setEnabled(True)
        if not results:
            self.speed_result.setText("测速失败：所有镜像源都没有响应，请检查网络。")
            return
        results.sort(key=lambda r: r["ms"] if r["ok"] else 99999)
        lines = []
        for i, r in enumerate(results, 1):
            icon = "🟢" if i == 1 and r["ok"] else ("✅" if r["ok"] else "❌")
            latency = f"{r['ms']} ms" if r["ok"] else f"失败（{str(r['status'])[:20]}）"
            lines.append(f"{icon} {r['name']}: {latency}")
        best = next((r for r in results if r["ok"]), None)
        if best and best["url"].rstrip("/") != npm_manager.current_registry().rstrip("/"):
            lines.append(f"\n💡 建议：当前最快的源是「{best['name']}」（{best['ms']} ms），可在「镜像源」页切换。")
        self.speed_result.setText("\n".join(lines))

    def clean_cache(self):
        ret = QMessageBox.question(self, "清理缓存", "确定清理 npm 下载缓存吗？\n（不影响已安装的 node_modules）")
        if ret == QMessageBox.Yes:
            npm_manager.clear_cache(lambda: self._log("npm 缓存清理完成。"))
            self._log("正在清理 npm 缓存…")

    # ================= 开机自愈与退出 =================
    def _repair_dangling_proxy_registry(self):
        """上次异常退出可能把源留在本地代理上（代理已停 ⇒ npm 安装全挂），开机自愈。"""
        current = npm_manager.raw_registry() or ""
        if not current.startswith(proxy_server.proxy_registry_url()):
            return
        if proxy_server.proxy_stats()["running"]:
            return
        fallback = self._registry_from_backup() or "https://registry.npmmirror.com"
        npm_manager.restore_registry(fallback)
        self._log(f"检测到上次退出遗留的本地代理源，已自动恢复为 {fallback}")

    @staticmethod
    def _registry_from_backup():
        """从 ~/.npmrc.bak 里取一个非代理的 registry（没有则返回 None）。"""
        if not npm_manager.has_backup():
            return None
        try:
            text = npm_manager.NPMRC_BAK.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "registry":
                value = value.strip().strip('"')
                if value and not value.startswith(proxy_server.proxy_registry_url()):
                    return value
        return None

    def _restore_registry_if_proxied(self):
        """仅当 registry 确实指向本代理时才还原，避免覆盖用户自己的源。"""
        current = npm_manager.raw_registry() or ""
        if current.startswith(proxy_server.proxy_registry_url()):
            npm_manager.restore_registry(self._prev_registry)
            self._log(f"已把 npm 源恢复为 {npm_manager.current_registry()}")

    def closeEvent(self, event):
        """退出前收敛后台工作：终止命令、等线程收尾、停代理并还原源。

        不等线程结束就退出会让进程带着运行中的 QThread 走向 abort（闪退）。
        """
        if self.cmd_thread is not None and self.cmd_thread.isRunning():
            self.cmd_thread.stop()
        for thread in list(self._jobs):
            if thread.isRunning():
                thread.wait(8000)
        if proxy_server.proxy_stats()["running"]:
            proxy_server.stop_proxy()
        if github_boost.github_stats()["running"]:
            github_boost.stop_proxy()
        self._restore_registry_if_proxied()
        event.accept()

    # ---------- GitHub 加速 ----------
    def _tab_github(self):
        w = QWidget()
        v = QVBoxLayout(w)

        c1, v1 = card("GitHub 加速（DoH 优选 IP + 本地 HTTPS 隧道）")
        desc = QLabel(
            "GitHub 打不开、clone 卡住，多半是 DNS 被污染或解析到的 IP 不通。这里用 DoH"
            "（阿里/腾讯/360）绕开本机 DNS 取候选 IP，逐个做 TCP + TLS 握手实测，"
            "挑出真正能服务该域名的那个。\n"
            "只做 TCP 隧道转发，不改证书、不改 hosts：不需要装自签 CA，也不会和 Steam++ 这类"
            "同样改写 hosts 的工具抢域名，普通用户权限就能跑。"
        )
        desc.setWordWrap(True)
        desc.setProperty("role", "hint")
        v1.addWidget(desc)

        grow = QHBoxLayout()
        self.btn_github_toggle = QPushButton("启动 GitHub 加速")
        self.btn_github_toggle.setObjectName("primaryBtn")
        self.btn_github_toggle.clicked.connect(self.toggle_github)
        grow.addWidget(self.btn_github_toggle)
        self.btn_github_speed = QPushButton("重新测速优选 IP")
        self.btn_github_speed.clicked.connect(self.refresh_github_ips)
        grow.addWidget(self.btn_github_speed)
        self.github_status = QLabel("未启动")
        self.github_status.setProperty("role", "hint")
        grow.addWidget(self.github_status)
        grow.addStretch()
        v1.addLayout(grow)

        srow = QHBoxLayout()
        self.lbl_gh_tunnels = QLabel("隧道 0")
        self.lbl_gh_hits = QLabel("优选命中 0")
        self.lbl_gh_fallback = QLabel("回退 0")
        self.lbl_gh_bytes = QLabel("转发 0 MB")
        self.lbl_gh_last = QLabel("最近：—")
        for lbl in (self.lbl_gh_tunnels, self.lbl_gh_hits, self.lbl_gh_fallback,
                    self.lbl_gh_bytes, self.lbl_gh_last):
            lbl.setProperty("role", "stat")
            srow.addWidget(lbl)
        srow.addStretch()
        v1.addLayout(srow)
        v.addWidget(c1)

        c2, v2 = card("优选 IP（每个 IP 都验过证书与 HTTP 响应）")
        self.gh_table = QTableWidget(len(github_boost.GITHUB_HOSTS), 4)
        self.gh_table.setHorizontalHeaderLabels(["域名", "优选 IP", "握手", "状态"])
        self.gh_table.verticalHeader().setVisible(False)
        self.gh_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.gh_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.gh_table.setAlternatingRowColors(False)
        header = self.gh_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.gh_table.setMinimumHeight(220)
        v2.addWidget(self.gh_table)
        self.gh_progress = QProgressBar()
        self.gh_progress.setVisible(False)
        v2.addWidget(self.gh_progress)
        hint2 = QLabel(
            "IP 会随 GitHub 的调度变化，失效时点「重新测速优选 IP」；启动后每 10 分钟自动重测一次。"
        )
        hint2.setWordWrap(True)
        hint2.setProperty("role", "hint")
        v2.addWidget(hint2)
        v.addWidget(c2)

        c3, v3 = card("让 git 也走加速（只影响 GitHub 仓库）")
        gdesc = QLabel(
            "只给 git 的 GitHub 请求配置代理，其它仓库不受影响；设置前会把 git 原有的代理配置"
            "备份到 ~/.npm-boost-git-proxy.json，随时可一键还原。"
        )
        gdesc.setWordWrap(True)
        gdesc.setProperty("role", "hint")
        v3.addWidget(gdesc)
        gitrow = QHBoxLayout()
        self.btn_git_apply = QPushButton("为 git 启用加速")
        self.btn_git_apply.clicked.connect(self.apply_git_proxy_ui)
        gitrow.addWidget(self.btn_git_apply)
        self.btn_git_restore = QPushButton("还原 git 配置")
        self.btn_git_restore.setObjectName("dangerBtn")
        self.btn_git_restore.clicked.connect(self.restore_git_proxy_ui)
        gitrow.addWidget(self.btn_git_restore)
        self.git_status = QLabel("未配置")
        self.git_status.setProperty("role", "hint")
        gitrow.addWidget(self.git_status)
        gitrow.addStretch()
        v3.addLayout(gitrow)
        self.git_cmd_hint = QLabel("")
        self.git_cmd_hint.setWordWrap(True)
        self.git_cmd_hint.setProperty("role", "hint")
        v3.addWidget(self.git_cmd_hint)
        v.addWidget(c3)

        v.addStretch()
        return w

    def refresh_github_panel(self):
        st = github_boost.github_stats()
        self.btn_github_toggle.setText("停止 GitHub 加速" if st["running"] else "启动 GitHub 加速")
        self.github_status.setText(
            f"运行中 · 代理 {st['url']} · 优选 {st['hosts_ready']}/{st['hosts_total']} 个域名"
            if st["running"] else "未启动")
        self.refresh_github_stats()
        self._fill_github_table()
        self._refresh_git_status()

    def refresh_github_stats(self):
        st = github_boost.github_stats()
        self.lbl_gh_tunnels.setText(f"隧道 {st['tunnels']}")
        self.lbl_gh_hits.setText(f"优选命中 {st['hits']}")
        self.lbl_gh_fallback.setText(f"回退 {st['fallbacks']}")
        self.lbl_gh_bytes.setText(f"转发 {st['bytes'] / 1024 ** 2:.1f} MB")
        self.lbl_gh_last.setText(f"最近：{st['last_connect'] or '—'}")

    def _fill_github_table(self):
        for r, row in enumerate(github_boost.ip_table()):
            if row["ip"] and row["tls"]:
                handshake, status = f"{row['ms']:.0f} ms", "可用"
            elif row["ip"]:
                handshake, status = "—", "仅 TCP 可用"
            else:
                handshake, status = "—", row["note"]
            for col, text in enumerate((row["host"], row["ip"] or "—", handshake, status)):
                item = QTableWidgetItem(str(text))
                item.setToolTip(row["note"] or "尚未测速")
                self.gh_table.setItem(r, col, item)

    def _refresh_git_status(self):
        status = github_boost.git_proxy_status()
        if status["points_to_us"]:
            self.git_status.setText("已指向本地加速")
        elif status["configured"]:
            self.git_status.setText(f"git 用了别的代理：{status['configured']}")
        else:
            self.git_status.setText("未配置（git 直连 GitHub）")
        self.git_cmd_hint.setText(f"等价命令：{github_boost.git_proxy_command()}")

    def _auto_refresh_github(self):
        running = github_boost.github_stats()["running"]
        busy = self.github_speed_thread is not None and self.github_speed_thread.isRunning()
        if running and not busy:
            self._log("GitHub 优选 IP 定时重测…")
            self.refresh_github_ips(silent=True)

    def toggle_github(self):
        running = github_boost.github_stats()["running"]
        self.btn_github_toggle.setEnabled(False)
        self.github_thread = self._keep(
            GithubActionThread("stop" if running else "start", parent=self))
        self.github_thread.finished_sig.connect(self._on_github_toggled)
        self.github_thread.start()

    def _on_github_toggled(self, ok, msg):
        self.btn_github_toggle.setEnabled(True)
        self._log(msg)
        if not ok:
            QMessageBox.warning(self, "GitHub 加速", msg)
            return
        self.refresh_github_panel()
        if github_boost.github_stats()["running"] and github_boost.github_stats()["hosts_ready"] == 0:
            self.refresh_github_ips()

    def refresh_github_ips(self, silent=False):
        if self.github_speed_thread is not None and self.github_speed_thread.isRunning():
            return
        self.btn_github_speed.setEnabled(False)
        self.gh_progress.setVisible(True)
        self.gh_progress.setRange(0, 0)
        self.github_speed_thread = self._keep(GithubSpeedThread(parent=self))
        self.github_speed_thread.progress.connect(self._on_github_progress)
        self.github_speed_thread.finished_sig.connect(self._on_github_ips_ready)
        if not silent:
            self._log("开始解析并实测 GitHub 候选 IP…")
        self.github_speed_thread.start()

    def _on_github_progress(self, done, total, label):
        if total:
            self.gh_progress.setRange(0, total)
            self.gh_progress.setValue(done)
        self.statusBar().showMessage(f"GitHub 优选 {done}/{total}：{label}", 3000)

    def _on_github_ips_ready(self, summary):
        self.gh_progress.setVisible(False)
        self.btn_github_speed.setEnabled(True)
        if "error" in summary:
            self._log(f"优选失败：{summary['error']}")
            QMessageBox.warning(self, "GitHub 加速", f"优选 IP 失败：{summary['error']}")
        else:
            ready = sum(1 for entry in summary.values() if entry.get("ips"))
            self._log(f"优选完成：{ready}/{len(summary)} 个域名可用")
        self.refresh_github_panel()

    def apply_git_proxy_ui(self):
        self.btn_git_apply.setEnabled(False)
        self.github_git_thread = self._keep(GithubActionThread("git_apply", parent=self))
        self.github_git_thread.finished_sig.connect(self._on_git_proxy_changed)
        self.github_git_thread.start()

    def restore_git_proxy_ui(self):
        self.btn_git_restore.setEnabled(False)
        self.github_git_thread = self._keep(GithubActionThread("git_restore", parent=self))
        self.github_git_thread.finished_sig.connect(self._on_git_proxy_changed)
        self.github_git_thread.start()

    def _on_git_proxy_changed(self, ok, msg):
        self.btn_git_apply.setEnabled(True)
        self.btn_git_restore.setEnabled(True)
        self._log(msg if ok else f"git 配置操作失败：{msg}")
        if not ok:
            QMessageBox.warning(self, "GitHub 加速", msg)
        self._refresh_git_status()
