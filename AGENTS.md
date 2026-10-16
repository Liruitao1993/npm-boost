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
main_window.py    # 主窗口，6 个标签页（加速中心/镜像源/本地缓存代理/GitHub 加速/项目助手/日志）
npm_manager.py    # .npmrc 读写、镜像源列表、测速、npm 命令执行（纯函数为主）
proxy_server.py   # 本地 HTTP 缓存代理 127.0.0.1:8046（线程式 ThreadingHTTPServer）
github_boost.py   # GitHub 加速 127.0.0.1:8047（DoH 优选 IP + CONNECT 隧道，不做 TLS 中间人）
speed_meter.py    # 速率聚合：两个代理的累计字节 → 实时 ↓/↑（滑动平均 + 空闲判定，纯后端）
tray.py           # 托盘常驻 + 速率悬浮条（图标代码绘制；QSettings 记偏好与位置）
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
- `main.py --selftest`：打包后自检（CA 证书包 + 6 家镜像测速 + 代理缓存 + GitHub 优选 IP），结果写
  `%TEMP%\npm-accelerator-selftest.txt`，退出码 0 表示通过；windowed 模式没有 stdout，别再靠 print 判断。
  其中 GitHub 优选那一项只做报告、不计入整体成败（有些网络整体封锁 GitHub，其它功能仍然正常）。
- **版本信息只有一处来源**：`version.py` 的 `__version__`（还有作者、项目代号）。编译时由
  `NPM加速器.spec` 生成两个不入库的文件：`build_info.py`（运行时读取 → 窗口标题、自检输出）
  与 `version_info.txt`（写进 exe 的 Windows 文件属性）。发新版只改 `__version__` 再重新编译，
  界面上写的日期永远是「本次编译日期」，不会和源码里的固定字符串脱节。
- **raw.githubusercontent.com 的 185.199.1xx.133 时通时断是正常的**（本机实测同一批 IP 几分钟内从
  `HTTP 301` 变成全体 TCP 超时）：`probe()` 会把它们剔掉、`--selftest` 的 GitHub 项因此只作报告，
  别把这种波动当成代码回归去查。

- **GitHub 加速不做 TLS 中间人**：本地只开 CONNECT 隧道转发字节，SNI 与证书仍由客户端与 GitHub
  直接协商。因此**不需要**自签 CA、**不需要**让 git 关 `http.sslverify`。改这里别退化成中间人实现。
- **优选 IP 必须三重确认**：TCP 能连 + TLS 握手成功（SNI=域名、校验证书）+ 服务端回了 HTTP 响应，
  三者都过才算可用。只测 TCP 会把「443 通但证书不匹配」的 IP 选进来，客户端握手必失败。
- **DoH 只用阿里/腾讯/360**：本机实测 Cloudflare（cloudflare-dns.com）与 Google（dns.google）被阻断，
  加进 `DOH_PROVIDERS` 只会白白拖慢解析。
- **别用系统 DNS 解析 GitHub 域名**：本机 hosts 通常已被 Steam++ 这类工具指向 `127.0.0.1`，
  所以 `resolve_candidates()` 走 DoH、隧道用优选 IP；`is_github_host()` 必须按「`.后缀`」严格匹配，
  否则 `notgithub.com` / `github.com.evil.com` 会被误加速。
- **上游零响应要能自愈**：`_relay()` 返回 `_RelayBytes`（带 `from_upstream`），隧道结束后
  `_judge_tunnel()` 对「一个字节都没回」的 IP 记一次失败，`_candidate_ips()` 据此把失败 IP 排后、
  连续两次失败暂时不用（全部失败过则原样返回，避免彻底不可用）。
- **git 配置只碰 GitHub 的键**（`http.https://github.com.proxy` / gist），设置前把原值备份到
  `~/.npm-boost-git-proxy.json` 且只备份一次，还原时按备份恢复原值而不是无脑删除。
  注意 `git config --global --get <key>` 才是读（写成 `get` 会被 git 当成键名报 rc=2）。
- GitHub 加速页的定时器：状态 2s 轻量刷新（只读内存，不跑子进程），优选 IP 每 10 分钟自动重测。
- **托盘常驻的三条硬约束**：① `main.py` 必须 `setQuitOnLastWindowClosed(False)`，否则收起到托盘后进程会
  退出；② 真正退出统一走 `MainWindow._cleanup_and_quit()`（显式 `app.quit()`），`closeEvent` 只负责
  「隐藏还是退出」的分叉；③ 没有系统托盘时（`QSystemTrayIcon.isSystemTrayAvailable()` 为假）强制
  「关闭即退出」，不然窗口就再也叫不回来了。
- **速率必须来自真实字节、而且必须边传边计**：`proxy_server` 用 `_copy_counting()` 与回源下载循环
  **逐块** `_count_down()`（未命中计「上游→本地」，命中计「本地→客户端」，同一次安装不重复计）；
  `github_boost._relay()` 双向计数（`_RelayBytes.from_upstream/from_client`）→ `speed_meter.RateMeter`
  1 秒采样 + 3 点滑动平均、5 秒无流量算空闲。**别改成"整段传完再累加一次"**：实测那样长下载会中途
  一直显示 0、末尾跳一个"突发均值"，用户看不到实时速率。npm 的 GET 请求体由 requests 内部生成且极小，
  不计入上传，免得报出虚高数字。
- **代理状态落盘**：启动本地代理前把用户原本的 registry 写进 `~/.npm-accelerator-state.json`
  （`npm_manager.save_proxy_state()`），崩溃/强杀后自愈时优先用它还原。只靠 `~/.npmrc.bak` 会还原成
  很久以前的源（实测踩过：用户的 npmmirror 被还原成了备份里的腾讯云）。
- **测试里的 QSettings 必须显式隔离**：`QSettings(org, app)` 默认写注册表，且 `QSettings.setDefaultFormat`
  对它**无效**（踩过坑：测试真的写进了用户注册表）。测试要 monkeypatch `tray.settings` 指向临时 ini。
- **退出与启动都要管住 git 代理键**：退出前 `_restore_git_proxy_if_ours()`；启动时
  `_repair_dangling_git_proxy()`（先用 `_port_alive(8047)` 确认没有别的实例在提供加速，否则会误改）。
  npm registry 的开机自愈也补了同样的端口判断。
- **托盘/窗口图标用 `tray.make_icon()` 代码绘制**（颜色取自 `theme.palette()`），exe 不依赖图标文件；
  悬浮条样式在 theme.py 的 QSS（`#speedBar` / `#speedDot[state]` / `#speedStatus`），组件里不要写色值。

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

# 7) GitHub 加速离线回归（域名判定 / DoH 解析 / CONNECT 隧道 / IP 失败轮换 / git 配置还原）
"D:\Program Files\Python313\python.exe" tests\test_github_boost.py

# 8) GitHub 加速真实端到端（需联网：curl 与 git 经本地加速代理拉取，并核对确实走了优选 IP）
"D:\Program Files\Python313\python.exe" tests\verify_github_boost.py

# 9) 托盘常驻与速率显示（真实托盘/悬浮条：关闭行为、默认位置、速率文案与偏好持久化）
"D:\Program Files\Python313\python.exe" tests\test_tray.py

# 10) 打包后的 exe 自检（结果见 %TEMP%\npm-accelerator-selftest.txt，退出码 0 为通过）
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
