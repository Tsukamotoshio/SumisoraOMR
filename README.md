# OMR-to-Jianpu Conversion Tool / 简谱转换工具

**Author / 作者：Tsukamotoshio**

A tool to batch-convert Western staff notation PDFs into Jianpu (numbered musical notation) PDFs, with optional MIDI output.

将五线谱 PDF 批量转换为简谱 PDF，并可按需同时生成 MIDI 文件。

---

## Features / 功能简介

- Batch-reads sheet music files from the `Input` folder / 批量读取 `Input` 文件夹中的五线谱乐谱文件
- Automatically recognizes and converts to Jianpu PDF, then opens the `Output` folder / 自动识别并转换为简谱 PDF，转换完毕后自动打开 `Output` 文件夹
- Optional MIDI file generation / 可选同时生成 MIDI 文件
- Already-converted files are skipped automatically / 已转换过的文件自动跳过，避免重复转换
- Compatible with Chinese, Japanese, and other non-ASCII filenames / 支持中文、日文等文件名场景的兼容处理

### Supported Input Formats / 支持的输入格式

| Format | Description |
|--------|-------------|
| `.pdf` | Staff notation PDF (recommended, multi-page supported) / 五线谱 PDF（推荐，支持多页）|
| `.png` | Sheet music image / 五线谱图片 |
| `.jpg` / `.jpeg` | Sheet music image / 五线谱图片 |

---

## Usage / 使用方法

1. Place the staff notation files (PDF / PNG / JPG) into the `Input` folder / 将待转换的五线谱文件（PDF / PNG / JPG）放入 `Input` 文件夹
2. Launch the program: double-click the **简谱转换工具** desktop shortcut, or run `ConvertTool.exe` in the installation directory / 运行程序：双击桌面快捷方式**简谱转换工具**，或直接运行安装目录下的 `ConvertTool.exe`
3. Follow the prompts to confirm conversion and optional MIDI output / 按提示确认是否开始转换、是否生成 MIDI
4. Results are saved to the `Output` folder / 转换结果会保存到 `Output` 文件夹

---

## Directory Structure / 目录说明

- `Input/` — Place source sheet music files here / 放入原始五线谱文件（PDF / PNG / JPG）
- `Output/` — Converted Jianpu PDFs and MIDIs are saved here / 保存生成的简谱 PDF / MIDI
- `logs/` — Runtime logs / 自动记录运行日志
- `THIRD_PARTY_NOTICES.md` — Third-party component licenses / 第三方组件许可证说明

---

## Known Limitations / 已知缺陷与局限

- **Recognition accuracy depends on score quality** — Audiveris uses OCR and machine learning; blurry scans or complex layouts may produce wrong or missing notes. / **识别准确率受乐谱质量影响**：扫描模糊或排版复杂的乐谱可能出现错音、漏音
- **Limited polyphony support** — Scores with many voices or chords may only retain the main melody. / **多声部支持有限**：多声部乐谱转换结果可能只保留主旋律
- **No lyrics output** — Only notes are exported; lyrics are not included. / **不支持歌词输出**：仅输出音符，不含歌词
- **Slow processing** — Audiveris startup and recognition take time; multi-page PDFs may take several minutes. / **处理速度较慢**：多页 PDF 可能需要数分钟
- **Edge cases in key/time signatures** — Uncommon time signatures or key changes may yield inaccurate results. / **调号 / 拍号边缘情况**：少数非常规调号或变拍子乐谱可能不准确

---

## Attribution / 署名说明

The **integration, scripting, feature adjustments, and packaging** of this project were done by **Tsukamotoshio**.

本项目的**整合、脚本编写、功能调整与打包工作**由 **Tsukamotoshio** 完成。

If redistributing, please retain this attribution and `THIRD_PARTY_NOTICES.md` to distinguish:
如果对外分发，建议保留本署名与 `THIRD_PARTY_NOTICES.md`，以区分：
- **Integration, development & packaging** / 整合、开发、打包工作：Tsukamotoshio
- **Third-party component copyrights & licenses** / 第三方组件版权与许可证：remain with their respective owners / 仍归其各自原作者所有

---

## License / 许可证

This project is licensed under the **MIT License** — see the `LICENSE` file for details.

本项目采用 **MIT License** 授权，详见 `LICENSE` 文件。

This tool bundles third-party components (Audiveris, LilyPond, music21, etc.).
Their respective copyrights and licenses are listed in `THIRD_PARTY_NOTICES.md`.

本工具包含第三方组件（如 Audiveris、LilyPond、music21 等），其各自的版权与许可证信息请参见 `THIRD_PARTY_NOTICES.md`。

---

## Development Notes / 开发说明

This project was developed using **Vibe Coding**, assisted by **GitHub Copilot**.

本项目采用 **Vibe Coding** 方式开发，由 **GitHub Copilot** 辅助完成代码编写与调试。

---

## Changelog / 更新日志

### v0.1.1
- Auto-open `Output` folder after conversion / 转换完成后自动打开 `Output` 文件夹
- Show "completed but no new files" when all inputs were already converted / 转换全部失败或无实际新增文件时提示"已完成，但无新增文件"
- Added support for PNG / JPG / JPEG input formats / 新增支持 PNG / JPG / JPEG 格式输入
- Fixed: "Music engraving by LilyPond" watermark text appearing at end of output PDF / 修复：输出简谱 PDF 末尾出现水印文字的问题
- Expanded known limitations section / 完善已知缺陷与局限说明

---

## Version / 版本

Current version / 当前版本：`0.1.1`
