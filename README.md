# 小宇宙播客处理器

在 Chrome 或 Edge 打开小宇宙单集页面，通过浏览器侧边栏下载并处理音频。项目仍保留原来的 PyQt6 桌面版；新增的浏览器扩展沿用相同参数和 FFmpeg 输出规则。

项目主页：[hyNous/cosmos-broadcast-processor](https://github.com/hyNous/cosmos-broadcast-processor)

浏览器提交下载任务后，会为**该任务**自动打开一个独立的 **Windows 本地任务窗口**（控制台），只显示该 `job_id` 的实时状态与四阶段进度。并发任务可各自有窗口。关闭网页或侧边栏不会中断后台 worker；关闭任务窗口也只关掉监控界面，不会取消正在下载的 worker。

**一次性任务生命周期：** 任务运行期间保留必要状态；成功、失败或取消后会短暂展示最终结果，随后自动删除该任务的状态文件与临时文件，任务窗口自动退出。成功时最终只保留你选择目录中的 MP3；不会生成运行日志、历史清单或可回溯任务记录。

> 仅用于你有权保存的公开内容。请遵守小宇宙服务条款、节目版权和所在地法律，不要批量抓取或传播受限内容。

## 功能

- 只在 `https://www.xiaoyuzhoufm.com/episode/...` 单集页面启用并自动解析
- 自定义开始、结束时间（`HH:MM:SS`）；结束为 `00:00:00` 表示处理到结尾
- 0.5–3.0 倍速且不变调、0.1–3.0 音量
- 自选输出目录和文件名；留空时使用单集标题
- 输出 MP3 192 kbps；遇到同名文件自动使用 `_1`、`_2` 后缀
- 侧边栏显示当前会话中的下载/处理进度，支持取消
- **不持久化任务 ID**：关闭侧边栏后不会从存储恢复旧任务；后台 worker 与已打开的终端继续运行
- **单任务本地窗口**：每任务独立控制台、四阶段进度、终态短暂展示后自动退出

## Windows 安装（浏览器扩展）

### 1. 安装 FFmpeg

安装 FFmpeg，并确保在 PowerShell 中以下两条命令都能正常输出版本：

```powershell
ffmpeg -version
ffprobe -version
```

### 2. 加载扩展

1. 解压发布包中的 `cosmos-browser-extension.zip`。
2. Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`。
3. 开启“开发者模式”，选择“加载已解压的扩展程序”，选中解压后的文件夹。
4. 扩展详情中显示的 ID 应为 `hjccjnbenicffglhjkjgoecbfdjfmafh`。

`manifest.json` 内置公钥以保持未打包扩展 ID 固定。本地辅助程序只接受这个 ID 发来的 Native Messaging 连接。

### 3. 注册本地辅助程序

**升级前请先等待所有进行中的下载任务结束。** 安装脚本只会清理 `%LOCALAPPDATA%\CosmosBroadcastProcessor\jobs` 下由 32 位小写 hex `job_id` 派生的状态文件（`<id>.json` / `<id>.cancel` / `<id>.json.tmp`），**不会**删除其它同扩展名文件、MP3 或你的输出目录。

在**扁平发布包**解压目录（与 `install-host.ps1` 同级）打开 PowerShell，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-host.ps1
```

从源码仓库开发时，也可在 `native_host` 目录运行（会回退到 `dist\native-host\` 中的 EXE）：

```powershell
powershell -ExecutionPolicy Bypass -File .\native_host\install-host.ps1
```

默认同时为当前 Windows 用户注册 Chrome 和 Edge，不需要管理员权限。也可只注册一个浏览器：

```powershell
.\install-host.ps1 -Browser Chrome
.\install-host.ps1 -Browser Edge
```

脚本优先使用与 `install-host.ps1` **同目录**的 `cosmos-native-host.exe`（扁平 ZIP），找不到时再回退仓库布局 `dist\native-host\`；并要求同目录存在 `cosmos-task-center.exe`。安装时复制到 `%LOCALAPPDATA%\CosmosBroadcastProcessor`，只写入当前用户的 Native Messaging 注册项。它**不会**安装 Windows 服务、**不会**创建开机启动项、**不会**监听网络端口，也**不会**自动启动任务窗口；**不会**递归删除整个应用目录。

### 4. 使用

1. 打开一个小宇宙 `/episode/` 单集页面。
2. 点击工具栏中的扩展图标，侧边栏会自动解析当前页面。
3. 设置裁剪、倍速、音量、目录和文件名，点击“下载并处理”。
4. 成功创建任务后，会自动弹出**该任务**的本地监控控制台；侧边栏仍可查看**当前会话**中的任务。
5. 任务成功后，MP3 保留在你选择的输出目录；状态文件会被清理，终端短暂展示后自动退出。

浏览器仅在插件发出 `ping`、解析、启动任务或查询状态时启动辅助程序。每次消息响应后该进程退出；真正的下载/FFmpeg 工作由按任务创建的独立后台 worker 完成。因此关闭侧边栏或切换页面不会中断工作，浏览器完全退出后任务也会继续到完成并自行清理状态。

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

窗口键盘命令（**无需**序号或短 ID，始终针对当前窗口任务）：

| 命令 | 作用 |
|---|---|
| `r` | 立即刷新 |
| `c` | 取消当前任务（写入 `.cancel`） |
| `o` | 打开当前任务输出目录 |
| `h` | 帮助 |
| `q` | 退出本窗口（**不**取消后台 worker；worker 仍会自行清理） |

进度说明：下载在已知 Content-Length 时显示 0–100%；未知长度时显示已下载 MB 并标记进度未知。FFmpeg 通过官方 `-progress` 输出估算阶段百分比；无法可靠计算时长时显示“进行中”，不伪造连续百分比。

### 卸载

从扁平发布包目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall-host.ps1
```

从源码仓库运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\native_host\uninstall-host.ps1
```

脚本删除所选浏览器的当前用户注册项、已安装的 host / 任务中心 exe 和 host manifest，并清理 `jobs` 目录下由 32 位小写 hex `job_id` 派生的状态文件（`<id>.json` / `<id>.cancel` / `<id>.json.tmp`）。**不删除**其它同扩展名文件、已下载的 MP3、用户输出目录或整个应用根目录。随后在浏览器扩展管理页移除扩展即可。

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

## 从源码开发和构建

桌面版：

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

扩展无需编译，发布时把 `extension` 目录内容打成 zip 即可。Windows Host ZIP 必须同时包含两个 EXE、`install-host.ps1`、`uninstall-host.ps1` 和 `README.md`（扁平布局，不含 `.log` / `.handoff`）。

开发态可直接运行单任务窗口（`--job-id` 必需）：

```powershell
python native_host\task_center.py --job-id <32位hex>
python native_host\task_center.py --smoke --job-id <32位hex> --jobs-dir <隔离目录>
```

自动化/CI 可设置：

- `COSMOS_SUPPRESS_TASK_CENTER=1`：host 在 `start` 时不弹出真实控制台
- `COSMOS_TERMINAL_GRACE_SECONDS=0`：worker 终态后立即清理（默认约 2.5 秒）
- `COSMOS_TASK_CENTER_STARTUP_WAIT`：任务中心等待 job 文件出现的秒数（默认 20）

运行测试和静态检查：

```powershell
python -m unittest discover -s tests -v
python -m compileall native_host tests
node tests\test_extension_state.js
node --check extension\job-state.js
node --check extension\service-worker.js
node --check extension\sidepanel.js
```

## 项目结构

```text
extension/                      Chrome/Edge Manifest V3 扩展
native_host/host.py             Native Messaging 协议与命令处理
native_host/processor.py        URL 校验、下载、任务、FFmpeg 与一次性清理
native_host/task_center.py      独立控制台任务中心（终态后自动退出）
native_host/*-host.ps1          构建、当前用户注册和卸载脚本
tests/                          host、任务中心与扩展状态测试
main.py gui.py worker.py        原 PyQt6 桌面版（保留）
```

## 故障排查

| 现象 | 处理 |
|---|---|
| 侧边栏显示“未安装” | 确认扩展 ID 正确，重新运行 `install-host.ps1`，然后重启浏览器 |
| 显示找不到 FFmpeg | 确认 `ffmpeg` 和 `ffprobe` 已加入 PATH；注册后重启浏览器以获取新环境变量 |
| 扩展图标在当前页面不可用 | 确认地址是 HTTPS 的小宇宙 `/episode/` 单集页，而非 `/podcast/` 主页 |
| 页面解析失败 | 单集可能下架/受限，或页面结构变化；先在浏览器确认页面可公开播放 |
| 关闭侧栏后重新打开看不到旧任务 | 预期行为：一次性任务，浏览器不持久化 job ID；worker 若仍在运行会自行完成并清理 |
| 任务窗口没有弹出 | 确认已构建/安装 `cosmos-task-center.exe`；侧边栏仅在**当前会话有进行中任务**时可「打开本地任务中心」 |
| 任务结束后窗口自动关了 | 预期行为：终态短暂展示后状态清理，窗口自动退出 |
| 查询已结束任务显示 removed | 预期行为：状态已清理，不可回溯 |

## 已知限制

- 依赖小宇宙页面与 CDN 结构；站点改版、下架或访问策略变化可能导致解析失败。
- 发布包中的 EXE 未做代码签名，Windows SmartScreen 或浏览器安全提示可能拦截首次运行。
- Native Host 仅按消息按需启动，不会常驻服务或监听端口；真正工作由独立 worker 完成。
- 任务窗口为本地控制台监控（每任务一个），不是托盘/服务，也不会开机启动。
- 任务状态为一次性：终态后删除；卸载/安装仅清理 `jobs` 状态文件，不删除用户 MP3。
- 浏览器内侧边栏外观与真实注册流程需在本机手动加载扩展并运行安装脚本验证；自动化测试覆盖协议、安全校验、一次性清理、单任务窗口逻辑、阶段进度和端到端处理链路。

## 许可证

[MIT License](LICENSE)
