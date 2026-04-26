# OMR-to-Jianpu 简谱转换工具

> 将五线谱（PDF / PNG / JPG）批量转换为简谱 PDF，并可按需同时生成 MIDI 文件。

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

| 引擎 | 支持格式 | 说明 |
|------|---------|------|
| **Audiveris** | PDF, PNG, JPG | 默认引擎，Java 实现，识别稳定 |
| **Homr** *（实验性）* | PNG, JPG | 深度学习模型；Windows 下支持 CUDA / DirectML GPU 加速 |

### 超分辨率（可选预处理）

| 引擎 | 说明 |
|------|------|
| **waifu2x-ncnn-vulkan** | 默认超分引擎，Vulkan GPU 加速 |
| **Real-ESRGAN** | 更高质量超分，支持 anime 优化模型，可在界面中选择 |

---

## 使用方法

1. 将五线谱文件（`.pdf`、`.png`、`.jpg`）放入 `Input` 文件夹。
2. 启动程序：双击桌面快捷方式**简谱转换工具**，或直接运行 `ConvertTool.exe`。
3. 按提示确认开始转换，并选择是否生成 MIDI。
4. 转换结果保存在 `Output` 文件夹中。

---

## 源码运行

**前置条件**

- Python 3.10+
- JDK 17+（需在 `PATH` 中，Audiveris 依赖）
- 以下运行时目录需与仓库根目录并列放置：

  | 目录 | 用途 |
  |------|------|
  | `omr_engine/audiveris/` | Audiveris OMR 引擎源码 |
  | `lilypond-2.24.4/` | LilyPond 排版引擎 |
  | `jdk/` | Audiveris 使用的 Java 运行时 |
  | `omr_engine/homr/` *（可选）* | Homr 深度学习 OMR 引擎 |
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
- **处理速度较慢**：Audiveris 启动耗时，多页 PDF 可能需要数分钟。
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

本工具包含第三方组件（Audiveris、LilyPond、music21、waifu2x-ncnn-vulkan、Real-ESRGAN 等），其各自版权与许可证信息请参见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
