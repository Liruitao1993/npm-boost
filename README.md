<div align="center">

# ⚡ NPM 加速器

**让每一次 `npm install` 都快人一步，也让 GitHub 不再打不开**

基于 PySide6 的 Windows 桌面工具 · 国内镜像 · 本地缓存代理 · 极速参数 · **GitHub 加速** · 浅色/深色主题 · 全中文界面

**当前版本 v1.1.0** · 作者 **liruitao** · 项目代号 `npm-boost`
窗口标题会带上版本号、编译日期与作者；exe 上「右键 → 属性 → 详细信息」里也能看到。

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4?logo=windows11&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.13-3776ab?logo=python&logoColor=white)
![Qt](https://img.shields.io/badge/PySide6-6.9-41cd52?logo=qt&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)
![Size](https://img.shields.io/badge/exe-50%20MB%20%E5%8D%95%E6%96%87%E4%BB%B6-orange)

</div>

---

## ✨ 为什么需要它

| 痛点 | NPM 加速器的解法 |
|------|------------------|
| 官方源下载龟速，`npm install` 卡半天 | 一键切换国内镜像源（npmmirror / 腾讯云 / 华为云…） |
| electron、node-sass、puppeteer 装到一半超时 | 内置 7 类二进制包国内镜像，直连 npmmirror CDN |
| 每次 `install` 都重复下载相同的包 | **本地缓存代理**：同版本包第二次安装秒取本地磁盘 |
| `audit` / `fund` 安全检查拖慢收尾 | 极速参数预置：高并发 + 跳过冗余检查 |
| 不知道哪个镜像源最快 | 并发测速 6 大镜像源，自动推荐最优 |
| GitHub 打不开、`git clone` / `git pull` 卡住 | **GitHub 加速**：DoH 优选最快 IP + 本地隧道，绕开被污染的 DNS |

## 🚀 快速开始

**方式一：免安装 exe（推荐）**

```text
dist/NPM加速器.exe   ← 双击即用，无需 Python 环境
```

**方式二：源码运行**

```bash
git clone <repo-url>
cd npm加速器
# 双击 启动NPM加速器.bat
# 或
"D:\Program Files\Python313\python.exe" main.py
```

> 依赖：Python 3.13+ / PySide6 6.9+ / requests。首次使用建议先点「⚡ 一键加速」。

## 📦 功能一览

### ⚡ 加速中心

- **一键加速**：镜像源 + 极速参数 + 二进制镜像，一次写齐
- **镜像测速**：并发探测，🟢 标记最快源并给出切换建议
- **环境总览**：npm / Node 版本、当前源、缓存目录与体积一目了然
- **安全网**：任何修改前自动备份 `~/.npmrc` → `~/.npmrc.bak`，随时一键还原

### 🪞 镜像源

内置 6 个镜像源单选切换，立即生效：

`npmmirror（淘宝）` · `腾讯云` · `华为云` · `cnpm` · `npm 官方` · `Yarn 官方`

### 🗄️ 本地缓存代理（核心）

```text
npm install ──▶ 127.0.0.1:8046 ──▶ 首次：回源下载 + 写入缓存
                       │
                       └─────────▶ 再次：直接命中本地缓存 ⚡
```

- 元数据缓存 10 分钟 TTL，tarball 永久缓存
- **元数据里的 tarball 下载地址会被改写到本代理**，所以 tarball 也走本地缓存；
  否则 npm 会绕过代理直连镜像，tarball 缓存形同虚设
- 实时统计：命中 / 回源次数、命中率、累计省下的下载量、已改写 tarball 数量
- 启动代理自动把 registry 指向 `127.0.0.1`，停止时**精确还原成你启动前的源**（不会强行改成别的镜像）
- 下次启动时若发现上次异常退出遗留的代理源（那会让所有 `npm install` 全部失败），自动修复
- 缓存落盘 `~/.npm-accelerator-cache/`，SHA-256 分片存储；命中回放依赖同名 `*.meta.json`
  边车文件（记录 Content-Type / ETag）
- `~/.npmrc` 只做「按键最小改动」写入：注释、空行、键顺序、私有源鉴权行都会原样保留

### 🐙 GitHub 加速

```text
curl / git / 浏览器 ──▶ 127.0.0.1:8047 ──▶ DoH 优选出的最快 IP ──▶ GitHub
                              │
                              └─ 只转发字节：证书与 SNI 仍是 GitHub 的（无需装自签 CA）
```

- **绕开被污染的 DNS**：用阿里 / 腾讯 / 360 的 DoH 解析候选 IP，不读本机 hosts 与运营商 DNS
- **三重确认才采用**：TCP 能连 + TLS 握手成功且**证书就是该域名的** + 服务端真的回了 HTTP 响应
- **16 个域名**：github.com / api / codeload / raw / objects / 头像 / gist / GitHub Pages …
- **一键让 git 走加速**：只给 git 的 GitHub 请求配置代理（其它仓库不受影响）；设置前会把原有代理配置
  备份到 `~/.npm-boost-git-proxy.json`，随时一键还原
- **不改证书、不改 hosts、不需要管理员权限**，也不会和 Steam++ 这类同样改写 hosts 的工具抢域名
- 界面实时显示每个域名的优选 IP 与握手耗时；IP 失效时可手动重测，启动后每 10 分钟自动重测
- 某个 IP 连得上却一个字节都不回时会被自动标记，后续连接优先换用其它候选

### 📁 项目助手

选择任意含 `package.json` 的目录，直接运行 `npm install / ci / update` 或自定义命令，输出实时滚动，可随时终止。

### 🎨 主题

浅色 / 深色两套配色，默认**跟随系统**（系统切换应用主题后界面自动跟随）：

- 标题栏右侧的下拉框可随时切换「跟随系统 / 浅色 / 深色」，选择会被记住
- 颜色只定义在 `theme.py`，两套主题共用同一份 QSS 模板，组件里不写死色值
- 配套的 QPalette 让下拉箭头、菜单、对话框、禁用态等原生绘制部分也跟随主题
- 预览图：`docs/theme-preview-light.png`、`docs/theme-preview-dark.png`

### 📜 日志

全部操作留痕：配置变更、测速结果、命令输出、代理请求，一目了然。

<details>
<summary>⚙️ 「极速参数」具体写了什么？（点开查看）</summary>

```ini
# 写入 ~/.npmrc
maxsockets=15              # 并发连接数拉满
prefer-offline=true        # 优先使用本地缓存
fetch-retries=3            # 失败重试 3 次
fetch-timeout=60000        # 单请求超时 60s
audit=false                # 跳过安全审计（提速主因之一）
fund=false                 # 跳过捐赠提示检查
progress=true              # 保留进度条

# 二进制包镜像（同样写入 ~/.npmrc）
ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
SASS_BINARY_SITE=https://npmmirror.com/mirrors/node-sass/
PUPPETEER_DOWNLOAD_BASE_URL=https://npmmirror.com/mirrors/chrome-for-testing
CHROMEDRIVER_CDNURL=https://npmmirror.com/mirrors/chromedriver/
NODEJS_ORG_MIRROR=https://npmmirror.com/mirrors/node/
```

</details>

## 🏗️ 项目结构

```text
npm加速器/
├── main.py               # 程序入口
├── main_window.py        # 主窗口 · 6 个功能页
├── npm_manager.py        # .npmrc 读写 · 镜像列表 · 测速 · npm 命令
├── proxy_server.py       # 本地 HTTP 缓存代理（线程式）
├── github_boost.py       # GitHub 加速：DoH 优选 IP + 本地 CONNECT 隧道代理
├── workers.py            # QThread 后台线程
├── theme.py              # 浅色/深色调色板 + QSS 模板 + 主题管理器
├── version.py            # 版本号 / 作者 / 项目代号（版本信息唯一来源）
├── NPM加速器.spec         # PyInstaller 打包脚本（编译时写入版本与日期）
├── 启动NPM加速器.bat      # 开发态一键启动
├── AGENTS.md             # AI Agent 协作上下文
├── .trellis/             # Trellis 工作流（规范/任务/会话记忆）
├── tests/                # 回归自检脚本（.npmrc / 主题 / GUI 冒烟 / 真实安装 / GitHub 加速）
├── docs/                 # 主题预览图（浅色 / 深色）
└── dist/
    └── NPM加速器.exe     # 免安装单文件版（编译产物，不入库）
```

## 🧪 开发与测试

```bash
# 1) 编译检查
python -m py_compile *.py

# 2) 代理缓存自检（gzip mock 上游，离线可跑；覆盖长度截断与 tarball 改写回归）
python -c "import proxy_server; print(proxy_server.self_test())"

# 3) .npmrc 读写自检（用临时文件，不碰真实配置）
python tests/test_npm_manager.py

# 4) GUI 冒烟（真实窗口：切标签页 + 测速 + 正常退出，退出码必须为 0）
python tests/smoke_gui.py

# 5) 真实安装端到端（需联网，使用隔离的临时缓存目录）
python tests/verify_proxy_install.py

# 6) GitHub 加速离线回归（域名判定 / DoH 解析 / 隧道转发 / git 配置备份还原）
python tests/test_github_boost.py

# 7) GitHub 加速真实端到端（需联网：curl 与 git 经本地加速代理拉取）
python tests/verify_github_boost.py

# 8) 打包后的 exe 自检（无窗口；结果写入 %TEMP%\npm-accelerator-selftest.txt，退出码 0 为通过）
dist\NPM加速器.exe --selftest

# 重新打包 exe
python -m PyInstaller --noconfirm --clean "NPM加速器.spec"
```

## 🛠️ 更新记录

### v1.1.0 —— 新增 GitHub 加速（2026-10-16）

新增第 6 个功能页「GitHub 加速」，解决 GitHub 打不开、`git clone` / `git pull` 卡住的问题。
思路与 FastGithub 同源（绕开被污染/被改写的 DNS，直连实测最快的 IP），但实现上做了两点简化：

- **不做 TLS 中间人**：本地只开一个 CONNECT 隧道，SNI 与证书仍由客户端与 GitHub 直接协商，
  所以不需要生成、分发、信任自签 CA，也不需要让 git 关掉 `http.sslverify`
- **不改 hosts**：不需要管理员权限，也不会和 Steam++ 这类同样改写 hosts 的工具抢域名

工作流程：DoH（阿里/腾讯/360）解析候选 IP → 对每个 IP 做 TCP + TLS 握手（SNI 校验证书）
+ 发一个 HEAD 请求三重确认、按握手耗时排序 → CONNECT 时把目标 IP 换成优选 IP。
实测 16 个域名 15 个可用，`curl` 与 `git ls-remote` 经代理均成功，且全程未回退系统解析。

同时新增「上游零响应」自愈：某个优选 IP 连得上却一个字节都不回时会被标记，
后续连接优先换用其它候选（实测 `raw.githubusercontent.com` 确实存在这种 IP）。


### v1.0.0 —— 首个公开发布版（2026-10-15）

首个公开版本，包含下面三轮迭代的全部修复与功能：

- 本地缓存代理可用（元数据 + tarball 都走缓存，实测重复安装秒取）
- 浅色 / 深色双主题，默认跟随系统
- 所有操作不再闪出命令行黑窗口，调用 npm 也更快
- 版本号 / 编译日期 / 作者写进窗口标题，并写进 exe 文件属性

版本号规则：`version.py` 里的 `__version__` 是唯一来源，打包脚本每次编译都会把
「版本 + 编译日期」写进 `build_info.py` 与 `version_info.txt`（两者都不入库），发新版只改 `__version__`。

### 迭代 3 —— 操作时不再闪出命令行黑窗口

- 根因：打包成 `--windowed` 的 exe 自身**没有控制台**，而程序里所有对 `npm.cmd` / `taskkill`
  这类控制台程序的调用都没有抑制新窗口，Windows 就为它们各新建一个黑色控制台窗口。于是
  「启动时采集环境」「一键加速后刷新环境」「项目助手跑安装」「清缓存」「点停止」都会闪一下。
- 修复：所有子进程调用统一走 `npm_manager.spawn_kwargs()`（`CREATE_NO_WINDOW`）。
  顺带还更快——实测 `npm --version` 由 1.5s 降到 0.7s（省掉了创建控制台宿主 conhost 的开销）。
- 实测对照（在无控制台的进程里运行）：不抑制弹出 1 个控制台窗口，抑制后 0 个；
  `--selftest` 新增「子进程静默」自检项，以后能自动拦住这类回归。

### 迭代 2 —— 深色 / 浅色双主题，默认跟随系统

- 新增浅色主题：颜色集中在 `theme.py` 的两套调色板里，QSS 由同一份模板渲染，避免两套样式各写一遍而漏改。
- 标题栏右侧新增主题选择器（跟随系统 / 浅色 / 深色），选择写入 `QSettings` 持久保存，
  默认跟随系统。
- 跟随系统的实现：优先 Qt 的 `styleHints().colorScheme()`，Qt 报 Unknown 时回退 Windows 注册表
  `AppsUseLightTheme`；同时监听 `colorSchemeChanged` 并加低频轮询兜底，系统换主题时界面自动跟随。
- 清理组件里残留的 5 处硬编码色值（卡片底色、卡片标题、环境标签、统计数字），改为 QSS 动态属性
  选择器；并加了自动检查——组件文件里再出现 `#rrggbb` 就判定为回归。
- 同时下发配套的 QPalette，让下拉箭头、菜单、对话框、禁用态等原生绘制部分也跟随主题。
- 顺手修掉两处既存样式问题：提示文字（hint）此前因为选择器写法不对而从未生效；
  卡片内的标签会继承窗口底色，形成肉眼可见的色块。
- 新增 `tests/test_theme.py`：按 WCAG 断言两套主题的对比度（正文 ≥ 4.5、非文本 ≥ 3.0），
  并覆盖主题切换、持久化与跟随系统的行为；`tests/smoke_gui.py` 增加了渲染亮度校验
  （浅色主题平均亮度必须明显高于深色）。

### 迭代 1 —— 修复「启动后根本无法加速」与退出闪退

本地缓存代理此前几乎是不可用状态，关键修复：

- **代理转发 gzip 响应时按「压缩后长度」写 Content-Length**，客户端拿到的 JSON 被截断，
  npm 直接报 `HPE_INVALID_CONSTANT` / 解析失败 —— 这就是「一开代理就装不上包」的根因。
  现在统一解压后按真实字节数写长度。
- **tarball 根本没走代理**（元数据里的下载地址仍指向镜像），缓存省不到流量；
  现在会把地址改写指向本代理，实测第二次安装 3 次全部命中缓存。
- 缓存命中时会丢失 Content-Type / ETag，现随 `*.meta.json` 保存并回放。
- 缩略元数据与完整元数据共用一份缓存、互相污染，现按 Accept 变体分开存放。

其它已修复项：

- **退出时闪退**（进程退出码 `0xC0000409`）：后台 QThread 未收尾就退出，现在退出前会等待线程结束。
- **备份文件名写错**（实际生成 `~/.npmrc.npmrc.bak`）且每次操作都会覆盖备份，
  现在是标准的 `~/.npmrc.bak`，且只在首次操作时生成，随时能回到最初配置。
- **停止代理 / 退出时硬编码把源改回 npmmirror**，会覆盖你自己的私有源；现在精确还原。
- **新增开机自愈**：上次异常退出若把 registry 留在已停止的代理上（会让所有 `npm install` 失败），
  下次启动自动修复。
- **镜像源测速把腾讯云误判为失败**（探测根路径返回 404），改用标准探活端点 `/-/ping`。
- **「Node 版本」显示的其实是 npm 版本**，已改为真实读取 `node --version`。
- **`.npmrc` 写入改为按键最小改动**：不再整篇重排、不再丢空行，私有源鉴权行原样保留。
- **缓存目录体积扫描会卡住界面**（实测 6 秒以上），已加时间与文件数上限。
- **「停止」按钮只杀掉 npm.cmd 外壳**，会留下孤儿 node 进程；改用 `taskkill /T` 结束整棵进程树。
- **打包漏掉 TLS 证书包（certifi 的 `cacert.pem`）**：exe 里所有 HTTPS 请求失败，表现为
  「镜像测速全部失败」「本地缓存代理回源全部 502」。现在 spec 显式 `collect_data_files('certifi')`，
  启动时还会自检 CA 证书包，缺失会在徽标和日志里明确提示。
- 新增 `--selftest` 打包后自检：一条命令确认 exe 的证书包、镜像测速与代理缓存是否都可用。

## 🗺️ Roadmap

- [ ] 团队局域网共享缓存（多人共用一份代理缓存）
- [ ] pnpm / yarn 支持
- [ ] 安装包体积对比报告（加速前后可视化）
- [ ] 托盘常驻模式

## 📄 License

MIT
