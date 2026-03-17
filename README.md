# 小宇宙播客下载器

一个 PyQt6 GUI 工具，用于从小宇宙 (xiaoyuzhoufm.com) 下载播客音频并进行处理（裁剪、变速、调音）。

## 功能特性

- **网页解析**：从播客 Episode 页面读取 og 标签，获取音频 URL、标题、简介等元数据
- **流式下载**：进度显示的后台下载，支持取消
- **时长探测**：自动用 FFprobe 获取音频原始时长（秒表示）
- **音频处理**（FFmpeg 实现）：
  - 🎬 **裁剪**：自定义起始和结束时间（HH:mm:ss 格式）
  - 🎚️ **变速不变调**：0.5x 至 3.0x（0.1 递增），支持任意倍数自动链式处理
  - 🔊 **调整音量**：0.1x 至 3.0x（0.1 递增）
- **灵活输出**：指定目录、自定义文件名（留空则用节目标题）
- **单一 exe 文件**：用 PyInstaller 打包，无需 Python 环境

## 系统要求

- **Windows 10/11**
- **FFmpeg**：需在系统 PATH 中安装（[官方下载](https://ffmpeg.org/download.html)）
  - 验证：终端运行 `ffmpeg -version` 和 `ffprobe -version`

## 安装和运行

### 方式一：运行 exe（推荐）

下载 `dist/小宇宙播客下载器.exe`，双击运行，无需其他安装。

**首次运行前**：确保已安装 FFmpeg。

### 方式二：运行源码（需要 Python）

```bash
# 安装依赖
pip install -r requirements.txt

# 运行
python main.py
```

## 项目结构

```
broadcast-processor/
├── main.py              # 入口，启动 PyQt6 应用
├── gui.py               # 主窗口 UI（PyQt6 控件）
├── extractor.py         # 网页解析，提取 og:audio/title 等
├── worker.py            # QThread 后台线程
│                        #  - DownloadProcessWorker：流式下载 + FFmpeg 处理
│                        #  - ExtractWorker：网页解析 + FFprobe 时长探测
├── requirements.txt     # Python 依赖
├── .gitignore          # Git 忽略配置
└── dist/
    └── 小宇宙播客下载器.exe  # 打包好的 exe（97 MB）
```

## 使用示例

1. **解析 Episode**：粘贴小宇宙播客链接（如 `https://www.xiaoyuzhoufm.com/episode/...`），点"解析"
   - 自动获取标题、时长、音频 URL
   - 时长自动填入"结束"时间

2. **设置参数**：
   - 开始/结束：HH:mm:ss，自动填入原始时长可直接下载
   - 倍速：0.5-3.0×（例 1.5x 快放）
   - 音量：0.1-3.0×（例 1.2x 增强音量）
   - 文件名：留空则用节目标题

3. **下载处理**：选择输出目录，点"下载并处理"
   - 进度条显示下载和 FFmpeg 处理进度
   - 完成后提示保存位置，可直接打开文件夹

## 技术栈

| 模块 | 技术 |
|------|------|
| UI | PyQt6 |
| 网页解析 | requests + BeautifulSoup |
| 音频处理 | FFmpeg（atempo、volume、libmp3lame） |
| 打包 | PyInstaller |
| 多线程 | QThread（避免 UI 阻塞） |

## 开发说明

### 修改源码后重新打包

```bash
python -m PyInstaller --onefile --windowed --collect-all PyQt6 --name 小宇宙播客下载器 main.py
```

生成的 exe 在 `dist/` 目录。

### FFmpeg 滤镜链

- **变速**：自动将超出 0.5-2.0 范围的倍速分解为多个 atempo 滤镜
  例：`3.0x` → `atempo=2.0,atempo=1.5`
- **音量** + **变速**：按需链接，未使用的滤镜不添加（减少不必要的重编码）

### 时间格式

- GUI 所有时间输入/显示都是 `HH:mm:ss`，内部转秒后传给 FFmpeg
- 若 FFprobe 探测失败，时长显示为 0，用户可手动输入结束时间

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| "找不到 FFmpeg" | PATH 中无 ffmpeg.exe | 装 FFmpeg 并添加到 PATH |
| 解析失败 | 无网络或链接无效 | 检查网络、确认是小宇宙 episode 页面 |
| 下载卡住 | 网络慢或链接超时 | 重试，检查网络连接 |
| 输出文件损坏 | FFmpeg 参数有误 | 查看错误信息，确认参数范围 |

## 许可证

MIT License

## 作者

Claude Code

---

**提示**：本工具仅供学习和个人使用。请遵守小宇宙平台服务条款，勿大量商业化下载。
