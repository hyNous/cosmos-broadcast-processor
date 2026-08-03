# 小宇宙播客处理器

在 Windows 上用 Chrome 或 Edge，从[小宇宙](https://www.xiaoyuzhoufm.com/)单集页面下载并处理音频（裁剪、倍速、音量），输出 MP3。本项目提供浏览器扩展 + 本地辅助程序；成功后输出目录**只保留你选择位置的 MP3**，不保留任务历史或运行日志。

项目主页：[hyNous/cosmos-broadcast-processor](https://github.com/hyNous/cosmos-broadcast-processor)

> 仅用于你有权保存的公开内容。请遵守小宇宙服务条款、节目版权和所在地法律，不要批量抓取或传播受限内容。

---

## 普通用户下载（Windows）

1. 打开最新发布页：
   **[https://github.com/hyNous/cosmos-broadcast-processor/releases/latest](https://github.com/hyNous/cosmos-broadcast-processor/releases/latest)**
2. 在 **Assets** 中下载：
   **`cosmos-broadcast-processor-windows-v1.0.1.zip`**
3. **不要**下载 GitHub 自动生成的 “Source code (zip/tar.gz)”——那是源码，不是安装包。

将 ZIP **完整解压**到任意文件夹（例如桌面），保持文件夹结构不要只解压其中几个文件。

---

## 零基础安装（Windows 10/11）

适用浏览器：**Google Chrome** 或 **Microsoft Edge**。不需要管理员权限。

### 步骤 1：安装 FFmpeg（处理音频必需）

在 PowerShell 中执行（推荐，自动加入 PATH）：

```powershell
winget install --id Gyan.FFmpeg --exact --accept-source-agreements --accept-package-agreements
```

安装后**新开**一个 PowerShell 窗口，确认两条命令都能输出版本：

```powershell
ffmpeg -version
ffprobe -version
```

### 步骤 2：安装本地辅助程序

1. 打开刚才解压出来的文件夹（应能看到 `安装本地程序.cmd` 和 `extension`、`native_host` 等）。
2. **双击** `安装本地程序.cmd`。
3. 若 Windows / SmartScreen 提示“未知发布者”或拦截未签名程序：选择**更多信息 → 仍要运行**（本发布包 EXE 未做代码签名，属正常现象）。
4. 窗口显示成功后，按任意键关闭；记下退出码应为 `0`。

### 步骤 3：加载浏览器扩展

1. Chrome 地址栏打开 `chrome://extensions`，或 Edge 打开 `edge://extensions`。
2. 打开右上角 **开发者模式**。
3. 点击 **加载已解压的扩展程序**，选择 **`%LOCALAPPDATA%\CosmosBroadcastProcessor\extension`**（可在文件夹选择窗口地址栏粘贴此路径）。
4. 扩展详情中显示的 ID 应为：`hjccjnbenicffglhjkjgoecbfdjfmafh`。

安装脚本已将扩展复制到稳定目录。安装完成后可以删除下载的 ZIP 和解压目录；不要删除上述 `%LOCALAPPDATA%` 稳定目录。

如需把运行目录放到其他磁盘（例如 E 盘），在完整解压目录中执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\native_host\install-host.ps1 -InstallDir "E:\脚本工具\小宇宙播客\CosmosBroadcastProcessor"
```

安装脚本会记住这个目录；以后直接双击安装入口也会继续使用 E 盘。浏览器扩展应加载该目录下的 `extension` 文件夹。

### 步骤 4：完全退出并重启浏览器

关闭所有 Chrome / Edge 窗口后重新打开，让新注册的本地程序生效。

### 步骤 5：第一次下载

1. 打开任意小宇宙**单集**页，地址形如：
   `https://www.xiaoyuzhoufm.com/episode/...`
   （必须是 `/episode/` 单集，不是播客主页。）
2. 点击浏览器工具栏中的扩展图标，侧边栏会解析当前页面。
3. 按需设置裁剪时间、倍速、音量、输出目录和文件名，点击**下载并处理**。
4. 会弹出一个**仅针对当前任务**的本地控制台窗口，显示四阶段进度。
5. 成功后：你选择的输出目录里**只留下 MP3**；任务状态与临时文件会被自动删除，终端短暂展示结果后自动关闭。

---

## 升级 / 卸载

### 升级

1. **先等待所有正在进行的下载任务结束**（升级安装会清理旧任务状态文件，不会删你的 MP3）。
2. 下载新版本统一 ZIP 并完整解压。
3. 再双击一次 `安装本地程序.cmd`。
4. 在扩展管理页对已加载扩展点**重新加载**。若从 `v1.0.0` 升级，或旧解压目录已经删除：先移除旧扩展，再从 `%LOCALAPPDATA%\CosmosBroadcastProcessor\extension` 加载一次。
5. 完全退出并重启浏览器。

### 卸载

1. 双击解压目录中的 `卸载本地程序.cmd`（会注销当前用户的 Native Messaging 注册项并清理受限任务状态文件；**不删除**你已下载的 MP3）。
2. 在 `chrome://extensions` 或 `edge://extensions` 中移除本扩展。

---

## 最常见问题排查

| 现象 | 处理 |
|---|---|
| 侧边栏提示未安装 / 无法连接本地程序 | 确认扩展 ID 为 `hjccjnbenicffglhjkjgoecbfdjfmafh`；重新双击 `安装本地程序.cmd`；**完全退出并重启**浏览器 |
| 提示找不到 FFmpeg / ffprobe | 用上面的 `winget` 安装；新开终端执行 `ffmpeg -version`；确认 PATH 后重启浏览器 |
| 双击安装被 SmartScreen 拦截 | 选「更多信息 → 仍要运行」；发布包未代码签名 |
| 扩展加载失败或找不到文件 | 重新运行 `安装本地程序.cmd`，加载 `%LOCALAPPDATA%\CosmosBroadcastProcessor\extension` |
| 扩展图标在当前页不可用 | 必须打开 HTTPS 的 `/episode/` 单集页，不是 `/podcast/` 主页 |
| 关闭侧栏后再开看不到旧任务 | **预期行为**：一次性任务，浏览器不保存任务 ID；后台若仍在跑会自行完成并清理 |
| 任务结束后终端窗口自动关了 | **预期行为**：终态短暂展示后清理状态并退出 |
| 查询已结束任务显示 removed | **预期行为**：状态已删除，不可回溯 |

包内还有离线说明：解压后的 `快速开始.txt`。

---

## 普通用户与开发者

| 身份 | 你需要什么 |
|---|---|
| **普通用户** | 只需下载统一 ZIP、按上方步骤安装 FFmpeg / 本地程序 / 扩展，即可使用。**不必**克隆仓库、安装 Python 或运行测试。 |
| **开发者** | 使用源码、构建 EXE、跑单元测试与打包脚本；见下文「从源码开发」「测试」与「项目结构」。 |

以下内容面向进阶使用与二次开发；普通用户可忽略。

---

## 功能一览

- 只在 `https://www.xiaoyuzhoufm.com/episode/...` 单集页面启用并自动解析
- 自定义开始、结束时间（`HH:MM:SS`）；结束为 `00:00:00` 表示处理到结尾
- 0.5–3.0 倍速且不变调、0.1–3.0 音量
- 自选输出目录和文件名；留空时使用单集标题
- 输出 MP3 192 kbps；遇到同名文件自动使用 `_1`、`_2` 后缀
- 侧边栏显示当前会话中的下载/处理进度，支持取消
- **不持久化任务 ID**：关闭侧边栏后不会从存储恢复旧任务；后台 worker 与已打开的终端继续运行
- **单任务本地窗口**：每任务独立控制台、四阶段进度、终态短暂展示后自动退出
- **一次性生命周期**：成功只留 MP3；失败/取消不留业务产物；无运行日志、无历史清单

浏览器提交任务后，会为该任务打开独立的 Windows 控制台窗口。关闭网页或侧边栏**不会**中断后台 worker；关闭任务窗口只关掉监控界面，不会取消正在下载的 worker。

---

## 统一安装包内容说明

`cosmos-broadcast-processor-windows-v1.0.1.zip` 解压后结构：

```text
cosmos-broadcast-processor-windows-v1.0.1/
├─ 快速开始.txt
├─ 安装本地程序.cmd
├─ 卸载本地程序.cmd
├─ extension/          ← 安装脚本会把这 6 个文件复制到稳定目录
└─ native_host/
   ├─ cosmos-native-host.exe
   ├─ cosmos-task-center.exe
   ├─ install-host.ps1
   └─ uninstall-host.ps1
```

- `安装本地程序.cmd` / `卸载本地程序.cmd`：双击入口，内部调用 `native_host` 下的 PowerShell 脚本；支持路径含空格；结束后暂停便于阅读，并保留真实退出码。
- 安装脚本把两个 EXE 和固定 6 个扩展文件复制到 `%LOCALAPPDATA%\CosmosBroadcastProcessor`，只写当前用户的 Native Messaging 注册项；浏览器加载其中的 `extension` 子目录。
- **升级前请先等待任务结束。** 安装/卸载只会清理 `jobs` 目录下由 32 位小写 hex `job_id` 派生的状态文件（`<id>.json` / `<id>.cancel` / `<id>.json.tmp`），**不会**删除其它同扩展名文件、MP3 或你的输出目录，也**不会**递归删除整个应用根目录。
- 不会安装 Windows 服务、开机启动项，不会监听网络端口。

从源码仓库开发时，也可直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\native_host\install-host.ps1
powershell -ExecutionPolicy Bypass -File .\native_host\uninstall-host.ps1
```

（会回退使用 `dist\native-host\` 中的 EXE。）

### 单任务本地窗口（终端）

| 问题 | 说明 |
|---|---|
| 何时自动打开？ | 每次 `start` 成功后，host 为**该 job_id** 非阻塞唤起一个新控制台 |
| 并发多个任务？ | 每个任务各自一个窗口；不同任务互不抢占 |
| 同一任务再打开？ | 同 job 单实例（按 job_id 的命名互斥）；第二次唤起会快速退出 |
| 窗口里有什么？ | 仅当前任务：标题/URL、总进度、四阶段流程、消息、参数、结果或错误 |
| 四阶段是什么？ | 页面解析 → 音频下载 → 音频处理（FFmpeg）→ 结果写入；总体进度单调 0–100 |
| 切换/关闭网页会怎样？ | 不影响。worker 后台继续；完成后会清理该任务状态 |
| 关掉终端会取消任务吗？ | **不会**。只关闭监控界面；queued/running 的 worker 继续跑并自行清理 |
| 关掉后又想看进度？ | 仅当侧边栏**仍保持打开**且任务尚未结束时，可点 **「打开本地任务中心」**（内存中的 `job_id`） |
| 任务结束后？ | 短暂展示终态（约 2–3 秒展示宽限期属于任务收尾），状态删除后窗口**自动退出** |
| 会保留历史吗？ | **不会**。无任务列表、无日志、浏览器不持久化 job ID |
| 会开机自启吗？ | **不会**。没有服务、托盘或开机启动项 |

窗口键盘命令（始终针对当前窗口任务）：

| 命令 | 作用 |
|---|---|
| `r` | 立即刷新 |
| `c` | 取消当前任务（写入 `.cancel`） |
| `o` | 打开当前任务输出目录 |
| `h` | 帮助 |
| `q` | 退出本窗口（**不**取消后台 worker；worker 仍会自行清理） |

进度说明：下载在已知 Content-Length 时显示 0–100%；未知长度时显示已下载 MB。FFmpeg 通过官方 `-progress` 输出估算阶段百分比；无法可靠计算时长时显示“进行中”，不伪造连续百分比。

---

## 架构与安全边界

```text
小宇宙 episode 页面
        │ 当前标签 URL
        ▼
Chrome / Edge MV3 侧边栏（仅内存 job_id）
        │ Native Messaging（固定扩展 ID）
        ▼
按需启动的 native host ── 创建任务状态文件
        │
        ├── 独立 worker ── 下载 ── FFmpeg ── 终态 ── 宽限期 ── 清理状态
        └── 每任务独立控制台（只读该 job JSON；状态消失后自动退出）
```

- 扩展只申请小宇宙站点权限、标签页、侧边栏、本地存储和 Native Messaging 权限。
- `chrome.storage.local` **仅**保存 `outputDir` 等普通偏好，不写入 job_id、标题、URL、输出路径或进度。
- host 严格校验消息命令、字段、数据类型、数值范围和任务 ID；`show_task_center` 白名单为 `command` + `job_id`，且拒绝已清理的 job。
- `status` 在任务文件不存在时返回稳定的 `status: "removed"`，不抛错、不恢复历史。
- 页面仅允许 HTTPS 小宇宙 episode URL；音频仅允许可信的 `*.xyzcdn.net` HTTPS 地址。
- 任务运行时状态在 `%LOCALAPPDATA%\CosmosBroadcastProcessor\jobs\<job_id>.json`；终态后由 worker 删除（展示宽限期约 2.5 秒，属任务收尾）。
- 清理路径仅由经过校验的 32 位 hex `job_id` 派生，不会扫描删除其他并发任务。
- 任务窗口启动失败**不会**回滚或中断已经创建的下载任务。
- 运行时不写 `.log`、执行历史、索引、数据库或遥测；Native Messaging 的 stdin/stdout 专用于浏览器协议。

---

## 从源码开发和构建

桌面版（原 PyQt6，仍保留）：

```powershell
python -m pip install -r requirements.txt
python main.py
```

浏览器辅助程序：

```powershell
python -m pip install -r native_host\requirements.txt
.\native_host\build-host.ps1
```

构建结果位于：

- `dist\native-host\cosmos-native-host.exe`
- `dist\native-host\cosmos-task-center.exe`

生成面向普通用户的统一安装包与校验文件：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-release.ps1
```

输出：

- `dist\cosmos-broadcast-processor-windows-v1.0.1.zip`
- `dist\SHA256SUMS.txt`（仅列出上述统一 ZIP 的 SHA-256）

扩展无需编译；`manifest.json` 内置公钥以保持未打包扩展 ID 固定为 `hjccjnbenicffglhjkjgoecbfdjfmafh`。

开发态可直接运行单任务窗口（`--job-id` 必需）：

```powershell
python native_host\task_center.py --job-id <32位hex>
python native_host\task_center.py --smoke --job-id <32位hex> --jobs-dir <隔离目录>
```

自动化/CI 可设置：

- `COSMOS_SUPPRESS_TASK_CENTER=1`：host 在 `start` 时不弹出真实控制台
- `COSMOS_TERMINAL_GRACE_SECONDS=0`：worker 终态后立即清理（默认约 2.5 秒）
- `COSMOS_TASK_CENTER_STARTUP_WAIT`：任务中心等待 job 文件出现的秒数（默认 20）

---

## 测试

```powershell
python -m unittest discover -s tests -v
python -m compileall native_host tests
node tests\test_extension_state.js
node --check extension\job-state.js
node --check extension\service-worker.js
node --check extension\sidepanel.js
powershell -ExecutionPolicy Bypass -File .\scripts\package-release.ps1
```

README 链接审计（开发机）：

```powershell
python C:\Users\27312\.codex\skills\generate-standard-readme\scripts\audit_readme.py README.md --repo-root . --require-link https://github.com/hyNous/cosmos-broadcast-processor --require-link https://github.com/hyNous/cosmos-broadcast-processor/releases/latest
```

---

## 项目结构

```text
extension/                      Chrome/Edge Manifest V3 扩展
native_host/host.py             Native Messaging 协议与命令处理
native_host/processor.py        URL 校验、下载、任务、FFmpeg 与一次性清理
native_host/task_center.py      独立控制台任务中心（终态后自动退出）
native_host/*-host.ps1          构建、当前用户注册和卸载脚本
release/                        统一安装包内的 CMD 与快速开始源文件
scripts/package-release.ps1     生成 v1.0.1 统一 ZIP 与 SHA256SUMS
tests/                          host、任务中心、扩展与发布包测试
main.py gui.py worker.py        原 PyQt6 桌面版（保留）
```

---

## 已知限制

- 依赖小宇宙页面与 CDN 结构；站点改版、下架或访问策略变化可能导致解析失败。
- 发布包中的 EXE 未做代码签名，Windows SmartScreen 或浏览器安全提示可能拦截首次运行。
- Native Host 仅按消息按需启动，不会常驻服务或监听端口；真正工作由独立 worker 完成。
- 任务窗口为本地控制台监控（每任务一个），不是托盘/服务，也不会开机启动。
- 任务状态为一次性：终态后删除；卸载/安装仅清理 `jobs` 状态文件，不删除用户 MP3。
- 浏览器内侧边栏外观与真实注册流程需在本机手动加载扩展并运行安装脚本验证；自动化测试覆盖协议、安全校验、一次性清理、单任务窗口逻辑、阶段进度、发布包清单和端到端处理链路。

---

## 许可证

[MIT License](LICENSE)
