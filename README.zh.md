# SumisoraOMR

> 将五线谱（PDF / PNG / JPG）批量转换为简谱 PDF，内置简谱编辑器与移调功能。

[![许可证: AGPL-3.0](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![平台: Windows](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()

**[English →](README.md)**

---

## 功能简介

- 批量读取 `Input` 文件夹中的五线谱文件，结果输出到 `Output`
- 已转换过的文件自动跳过（基于文件哈希去重）
- 可选同时生成 MIDI 文件
- 支持中文、日文等非 ASCII 文件名
- 可选超分辨率预处理，提升低质量扫描件的识别率

---

## 引擎说明

### OMR（光学音乐识别）

| 引擎 | 适合场景 | 说明 |
|------|---------|------|
| **Audiveris** | 数字 PDF | 识别由记谱软件（MuseScore、Sibelius、Finale 等）导出的 PDF，速度快、准确率高，无需 GPU。 |
| **Homr** | 扫描件与照片 | AI 驱动的引擎，处理印刷乐谱的 PNG/JPG 图像，能应对噪点与真实拍摄的各种干扰；GPU 加速（CUDA / DirectML）。 |

> 自动模式下：PDF 文件使用 **Audiveris**，图片文件（PNG/JPG）使用 **Homr**，无需手动切换。

### 超分辨率（可选预处理）

| 引擎 | 说明 |
|------|------|
| **Real-ESRGAN** | 默认超分引擎，更高质量超分，支持 anime 优化模型，Vulkan GPU 加速 |
| **waifu2x-ncnn-vulkan** | 备选超分引擎，Vulkan GPU 加速 |

---

## 使用方法

### 转换（主流程）

1. 启动程序：双击桌面快捷方式 **SumisoraOMR**，或直接运行 `SumisoraOMR.exe`。
2. 在左侧文件列表中点击「**添加文件**」（支持多选）或「**添加文件夹**」按钮，
   导入五线谱文件（`.pdf`、`.png`、`.jpg`）；所选文件会自动复制到 `Input`
   文件夹并显示在列表中。
3. 在文件侧边栏中勾选要转换的文件。
4. 选择 OMR 引擎（或保持**自动选择**），点击**开始转换**。
5. 在弹出对话框中确认选项（是否生成 MIDI、是否跳过重复文件），点击
   **开始转换**。
6. 转换后的简谱 PDF 保存在 `Output` 文件夹中。

### 简谱预览

转换完成后，切换到**简谱预览**标签页，浏览所有已生成的简谱 PDF。勾选文件后
点击侧边栏标题栏中的导出按钮，即可批量复制到指定目录。点击**编辑**按钮可打开
当前预览的文件进行简谱校对。

### 五线谱预览

切换到**五线谱预览**标签页，浏览识别后重新排版的五线谱 PDF，支持 MIDI 试听。
勾选文件后可批量导出到指定目录。点击**移调**按钮进入移调子页面。

### 简谱编辑器

编辑器用于查阅和手动校对 `.jianpu.txt` 源文件。左侧显示原始乐谱图像供参考，
右侧为可编辑的简谱文本。修改完成后点击**重新生成 PDF** 即可重建输出文件。

### 移调功能

在五线谱预览页内点击**移调**进入移调子页面，读取 `xml-scores/` 中的 MusicXML
文件并渲染为不同调性。支持三种模式：

- **按音程** — 选择具体音程（纯四度、大三度等）及方向。
- **按调** — 直接指定目标调号，软件自动计算偏移量。
- **全音移调** — 按音阶度数在调内移动。

任意参数变化时自动触发预览。结果可导出为五线谱 PDF。

---

## 源码运行

**前置条件**

- Python 3.10+
- JDK 17+（仅 PDF 识别时需要，供 Audiveris 使用；识别图片时不需要）
- 以下运行时目录需与仓库根目录并列放置：

  | 目录 | 用途 |
  |------|------|
  | `omr_engine/audiveris/` | Audiveris OMR 引擎源码，用于 PDF 输入 |
  | `lilypond-2.24.4/` | LilyPond 排版引擎 |
  | `jdk/` | Audiveris 使用的 Java 运行时 |
  | `omr_engine/homr/` | Homr 深度学习 OMR 引擎，用于 PNG/JPG 输入 |
  | `waifu2x-ncnn-vulkan/` *（可选）* | waifu2x 超分可执行文件 |
  | `realesrgan-runtime/` *（可选）* | Real-ESRGAN 可执行文件及模型 |

**安装依赖**

```bash
pip install -r requirements.txt
```

**运行**

```bash
python app.py
```

开发模式（热重载）：

```bash
flet run app.py
```

---

## 目录结构

```
Input/                   # 放入原始五线谱文件
Output/                  # 生成的简谱 PDF / MIDI
editor-workspace/        # 供手动校对的 .jianpu.txt 中间文件
xml-scores/              # MusicXML 存档（移调功能使用）
logs/                    # 运行日志
THIRD_PARTY_NOTICES.md   # 第三方组件许可证说明
```

---

## 已知缺陷与局限

- **识别准确率受乐谱质量影响**：扫描模糊或排版复杂的乐谱可能出现错音、漏音。
- **多声部支持有限**：多声部或和弦密集的乐谱可能只保留主旋律。
- **不支持歌词输出**：仅输出音符，不含歌词。
- **处理速度较慢**：单张乐谱的识别可能需要数分钟，多页 PDF 耗时更长。
- **边缘情况**：少数非常规拍号或中途变调的乐谱可能不准确。

---

## 署名说明

本项目的**整合、脚本编写、功能开发与打包工作**由 **Tsukamotoshio** 完成。

如对外分发，请保留本署名与 `THIRD_PARTY_NOTICES.md`，以区分：

- **整合、开发与打包**：Tsukamotoshio
- **第三方组件版权与许可证**：仍归其各自原作者所有（详见 `THIRD_PARTY_NOTICES.md`）

---

## 许可证

本项目采用 **GNU Affero 通用公共许可证第三版（AGPL-3.0）** 授权，详见 [`LICENSE`](LICENSE) 文件。

本工具包含第三方组件（Audiveris、Homr、LilyPond、music21、waifu2x-ncnn-vulkan、Real-ESRGAN 等），其各自版权与许可证信息请参见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
