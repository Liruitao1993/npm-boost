# NPM 加速器 · 项目上下文（AI Agent 补充说明）

> 本文件由 Trellis 托管区块 + 项目自定义说明组成。Trellis 区块内的内容可能被 `trellis update` 覆盖，项目约定写在区块之外。

## 项目是什么

**NPM 加速器**：基于 PySide6 的 Windows 桌面应用，让所有 `npm install` 更快完成下载与安装。中文界面、深色主题。

## 技术栈与运行环境

| 项 | 值 |
|----|-----|
| 语言 | Python 3.13（系统 Python：`D:\Program Files\Python313\python.exe`） |
| GUI | PySide6 6.9（**不要**引入 PyQt5 / Tkinter / Electron / Tauri） |
| HTTP | requests 2.33 |
| 打包 | PyInstaller 6.10（`--onefile --windowed`） |
| 目标系统 | Windows 10/11 |

**硬性约束**：禁止引入需要 Rust/MSVC 编译链的依赖（用户已明确拒绝 PyTauri 方案）；禁止引入新的重型第三方库。

## 文件结构与职责

```
main.py           # 入口：创建 QApplication，加载深色主题
main_window.py    # 主窗口，5 个标签页（加速中心/镜像源/本地缓存代理/项目助手/日志）
npm_manager.py    # .npmrc 读写、镜像源列表、测速、npm 命令执行（纯函数为主）
proxy_server.py   # 本地 HTTP 缓存代理 127.0.0.1:8046（线程式 ThreadingHTTPServer）
workers.py        # QThread 后台线程：测速/命令执行/环境采集/代理操作
theme.py          # 浅色/深色调色板 + QSS 模板 + 主题管理器（唯一配色来源）
启动NPM加速器.bat  # 开发态启动脚本
```

## 编码约定

1. **所有用户可见文案必须简体中文**（按钮、标签、提示、日志、QMessageBox）。
2. **UI 线程禁止阻塞操作**：任何网络请求、subprocess、文件扫描必须放进 `workers.py` 的 QThread，通过 Signal 回传结果。
3. **npm 配置只写用户级 `~/.npmrc`**；任何修改前必须先 `npm_manager.backup_npmrc()` 备份到 `.npmrc.bak`。
4. **代理只监听 127.0.0.1**，不得绑定 0.0.0.0。
5. 缓存目录固定 `~/.npm-accelerator-cache/`（metadata/ 10 分钟 TTL，tarballs/ 永久）。
6. 错误处理：subprocess 用 `errors="replace"` 解码，避免 GBK/UTF-8 混排崩溃；网络异常返回失败状态而不是抛到 UI。
7. 深色主题颜色统一走 `theme.py` 的 QSS，组件内不要散落硬编码色值（卡片背景 `#23252c`、主色 `#2f9e6e`）。

## 关键实现细节（改代码前必读）

- `proxy_server._state` 是模块级单例字典 + `_lock` 互斥，统计与启停都走它；不要为每个请求新建状态。
- **代理转发的铁律**：上游常返回 gzip 压缩体，其 `Content-Length` 是压缩后长度。必须把 body 解压成
  明文后按真实字节数写 `Content-Length`，否则客户端按错误长度读写，npm 报 `HPE_INVALID_CONSTANT`
  （这是 v1.1 修掉的头号事故）。缓存也一律存明文。
- `_cache_paths(cache_root, url, variant, is_tarball)` 用 SHA-256 前两位分片，并区分
  `abbrev`/`full` 两套元数据；命中回放依赖同名 `*.meta.json` 边车（Content-Type / ETag）。
- **tarball 走缓存靠改写**：回源元数据时把指向上游的 `dist.tarball` 改写成 `http://127.0.0.1:8046`，
  否则 npm 直连镜像，tarball 缓存永远不生效。
- 测速统一探测标准探活端点 `/-/ping`（6 家镜像实测均 200），失败再回退 `/px` 与根路径；
  **不要**用根路径判活 —— 腾讯云根路径返回 404，会把能用的源误判为失败。
- `NpmCommandThread` 用 Popen 增量读 stdout 逐行 emit；`stop()` 必须 `taskkill /F /T` 结束进程树，
  只 kill `npm.cmd` 外壳会留下孤儿 node 进程。
- **调用控制台程序必须走 `npm_manager.spawn_kwargs()`**（`creationflags=CREATE_NO_WINDOW`）：
  `--windowed` 的 exe 自身没有控制台，不抑制就会让每次 `npm.cmd` / `taskkill` 调用都新建一个
  黑色控制台窗口，在用户眼前闪一下（`main.py --selftest` 里的「子进程静默」项守着这条规则）。
- **QThread 生命周期**：线程对象必须由主窗口 `_keep()` 持有，否则运行中被 Python 回收会让进程
  直接 abort（退出码 `0xC0000409`）；`closeEvent` 里要等线程收尾再退出。
- `npm_manager` 写 `.npmrc` 走 `_update_npmrc` 的「按键最小改动」，保留注释/空行/顺序/鉴权行；
  备份固定为 `~/.npmrc.bak` 且只在首次操作时生成。
- 打包后 exe 是单文件模式，`Path.home()` 仍指向真实用户目录，缓存/配置不受影响。
- **配色只写在 `theme.py`**：组件里禁止出现 `#rrggbb`，语义化文字用动态属性
  （`role="hint"|"value"|"stat"`、`cardTitle=True`）交给 QSS 属性选择器；`tests/test_theme.py`
  会自动扫描组件文件阻止回归。两套主题共用同一份 QSS 模板（`string.Template`，用 `$name` 占位），
  别把样式再抄一遍。
- **主题切换要同时下发 QSS 与 QPalette**（`ThemeManager._apply`）：只改 QSS 时，下拉箭头、
  菜单、对话框、禁用态这些原生绘制部分不会跟着变。跟随系统优先用 `styleHints().colorScheme()`，
  Qt 报 Unknown 时回退 Windows 注册表 `AppsUseLightTheme`，并监听 `colorSchemeChanged` + 低频轮询兜底。
- `QWidget` 统一下发窗口底色，所以卡片内的标签必须靠 QSS 的 `background: transparent`（`QLabel`
  已有全局规则）才不会出现色块；新增控件时留意这条。
- **打包必须显式收集 certifi**：spec 里 `datas = collect_data_files('certifi')`。
  实测本机 PyInstaller 6.10 + pyinstaller-hooks-contrib 2025.9 的社区 hook 并**不会**把
  `cacert.pem` 打进 exe，缺了它 exe 里所有 HTTPS 请求报
  `OSError: Could not find a suitable TLS CA certificate bundle` —— 现象是「测速全部失败」
  「代理回源全部 502」，而源码直跑完全正常（很容易误判成网络问题）。
- `main.py --selftest`：打包后自检（CA 证书包 + 6 家镜像测速 + 代理缓存），结果写
  `%TEMP%\npm-accelerator-selftest.txt`，退出码 0 表示通过；windowed 模式没有 stdout，别再靠 print 判断。

## 测试方式

```bash
# 1) 编译检查
"D:\Program Files\Python313\python.exe" -m py_compile *.py

# 2) 代理缓存自检（gzip mock 上游，离线可跑；覆盖长度截断与 tarball 改写回归）
"D:\Program Files\Python313\python.exe" -c "import proxy_server; print(proxy_server.self_test())"

# 3) .npmrc 读写自检（临时文件隔离，不碰真实配置）
"D:\Program Files\Python313\python.exe" tests\test_npm_manager.py

# 4) GUI 冒烟（真实窗口：切标签页 + 测速 + 正常退出，退出码必须为 0）
"D:\Program Files\Python313\python.exe" tests\smoke_gui.py

# 5) 主题自检（对比度 + 切换 / 持久化 / 跟随系统）
"D:\Program Files\Python313\python.exe" tests\test_theme.py

# 6) 真实安装端到端（需联网，隔离临时缓存目录）
"D:\Program Files\Python313\python.exe" tests\verify_proxy_install.py

# 7) 打包后的 exe 自检（结果见 %TEMP%\npm-accelerator-selftest.txt，退出码 0 为通过）
dist\NPM加速器.exe --selftest
```

## Git 提交规范

`feat: / fix: / chore: / docs:` 前缀，中文描述。

<!-- TRELLIS:START -->
# Trellis Instructions

These instructions are for AI assistants working in this project.

This project is managed by Trellis. The working knowledge you need lives under `.trellis/`:

- `.trellis/workflow.md` — development phases, when to create tasks, skill routing
- `.trellis/spec/` — package- and layer-scoped coding guidelines (read before writing code in a given layer)
- `.trellis/workspace/` — per-developer journals and session traces
- `.trellis/tasks/` — active and archived tasks (PRDs, research, jsonl context)

If a Trellis command is available on your platform (e.g. `/trellis:finish-work`, `/trellis:continue`), prefer it over manual steps. Not every platform exposes every command.

If you're using Codex or another agent-capable tool, additional project-scoped helpers may live in:
- `.agents/skills/` — reusable Trellis skills
- `.codex/agents/` — optional custom subagents

Managed by Trellis. Edits outside this block are preserved; edits inside may be overwritten by a future `trellis update`.

<!-- TRELLIS:END -->
