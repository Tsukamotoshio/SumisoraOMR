# 批量乐谱(PDF/JPG/PNG) -> 简谱 PDF 转换工具
# Batch sheet music (PDF/JPG/PNG) to numbered notation (jianpu) PDF converter.
# Pipeline: input image/PDF -> Audiveris (OMR) -> MusicXML -> music21 -> MIDI -> jianpu-ly -> LilyPond -> jianpu PDF
#
# Copyright (c) 2026 Tsukamotoshio. All rights reserved.
# SPDX-License-Identifier: MIT
# See LICENSE file for full license text.
#
# 本文件是薄包装器（thin wrapper）。所有实现均位于 core/ 目录。
# This file is a thin wrapper; all logic lives in the core/ package.

from core.pipeline import main

if __name__ == '__main__':
    main()