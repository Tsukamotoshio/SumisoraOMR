# Third-Party Notices

This file provides license and attribution notices for third-party components distributed with this product, where such notices are required by the applicable licenses. It does not attempt to document every development or build dependency.

## Audiveris
- Version: `5.10.2`
- License: `AGPL-3.0`
- Upstream: <https://github.com/Audiveris/audiveris>
- Official site: <https://audiveris.github.io/audiveris/>

## LilyPond
- Version: `2.24.4`
- License: GPL-2.0
- Upstream: <https://lilypond.org/>

## music21
- Version: `9.9.1`
- License: BSD-3-Clause
- Upstream: <https://github.com/cuthbertLab/music21>
- Documentation: <https://www.music21.org/music21docs/>

## Pillow
- Version: `12.2.0`
- License: HPND (Historical Permission Notice and Disclaimer)
- Upstream: <https://github.com/python-pillow/Pillow>
- Documentation: <https://pillow.readthedocs.io/>
- Note: Used for sheet music image preprocessing (noise reduction, deskewing,
  sharpening, brightness/contrast enhancement) before OMR recognition.

## ReportLab
- Version: `4.4.10`
- License: BSD-3-Clause
- Official site: <https://www.reportlab.com/>

## OpenCV
- Version: `4.8.0`
- License: Apache-2.0
- Upstream: <https://github.com/opencv/opencv>
- Note: Used for advanced image preprocessing and line/staff detection in the OMER pipeline.

## ONNX Runtime (DirectML)
- Version: `1.20.0`
- License: MIT
- Upstream: <https://github.com/microsoft/onnxruntime>
- Note: Windows DirectML backend for GPU-accelerated inference used by the Homr engine.

## Homr
- Version: `0.1.0` (fork)
- License: AGPL-3.0
- Upstream (original): <https://github.com/liebharc/homr>
- Fork used by this project: <https://github.com/Tsukamotoshio/homr>
- Modifications: DirectML GPU inference support, ORT thread limits, explicit resource teardown, and safe XML output preservation on post-write exceptions.
- Note: End-to-end optical music recognition engine integrated as a local repository under `omr_engine/homr`.
- Upstream foundations: Homr builds upon two prior works whose models and segmentation code are embedded in the homr package:
  - **oemer** (MIT) — segmentation models for staff lines, note heads, and bar lines.
    Copyright © BreezeWhite. <https://github.com/BreezeWhite/oemer>
  - **Polyphonic-TrOMR** (MIT) — transformer model for end-to-end symbol sequence recognition.
    Copyright © NetEase. <https://github.com/NetEase/Polyphonic-TrOMR>

## scikit-learn
- License: BSD-3-Clause
- Upstream: <https://github.com/scikit-learn/scikit-learn>
- Note: A transitive dependency of `homr` for runtime inference.

## SciPy
- License: BSD-3-Clause
- Upstream: <https://github.com/scipy/scipy>
- Note: A transitive dependency of `homr` for numerical computation and matrix operations.

## NumPy
- License: BSD-3-Clause
- Upstream: <https://github.com/numpy/numpy>
- Note: Core numerical array library required by `homr` for image tensor operations and model inference.

## tqdm
- License: MPL-2.0 AND MIT
- Upstream: <https://github.com/tqdm/tqdm>
- Note: Progress-bar library; a transitive dependency of `homr`. Only `homr` internals use it directly; no tqdm source is modified by this project, so MPL-2.0 file-level copyleft is not triggered.

## jianpu-ly
- Version: `1.866`
- License: Apache-2.0
- Home: <https://ssb22.user.srcf.net/mwrhome/jianpu-ly.html>
- Upstream: <https://github.com/ssb22/jianpu-ly>

## waifu2x-ncnn-vulkan
- Version: `20250915`
- License: MIT
- Copyright: © 2019 nihui
- Upstream: <https://github.com/nihui/waifu2x-ncnn-vulkan>
- Note: Bundled as a GPU-accelerated (Vulkan) super-resolution pre-processor
  for low-resolution sheet music images. Invoked as an external subprocess;
  not linked or modified. License text is included in `waifu2x-runtime/LICENSE`.

## Eclipse Temurin JDK
- Version: `25.0.2+10` (Temurin-25.0.2+10)
- Implementor: Eclipse Adoptium
- License: GPLv2 with Classpath Exception
- Official site: <https://adoptium.net/>
- Source: <https://github.com/adoptium/temurin25-binaries>
- Note: Bundled as the Java runtime required by Audiveris.

## Flet
- Version: `0.84`
- License: Apache-2.0
- Upstream: <https://github.com/flet-dev/flet>
- Documentation: <https://flet.dev/>
- Note: Used as the GUI framework for the desktop application.

## PyMuPDF (fitz)
- Version: `>=1.24.0`
- License: AGPL-3.0 (free tier) / commercial
- Upstream: <https://github.com/pymupdf/PyMuPDF>
- Note: Used for PDF page rendering and preview in the packaged application. Bundled into the distributed executable when shipping PDF input support.
---

## Important note

If you redistribute this package, applicable open-source license obligations still apply.

### Audiveris (AGPL-3.0)

This tool invokes Audiveris as an **external subprocess**; it does not embed,
link against, or modify Audiveris source code. Only the Audiveris binary itself
is subject to AGPL-3.0. If you have not modified Audiveris, your redistribution
obligations are:

1. Keep Audiveris’ original copyright and license notices intact.
2. Provide recipients with the upstream source link: <https://github.com/Audiveris/audiveris>
3. If you have modified Audiveris itself, you must make those modifications
   available under AGPL-3.0.

### Eclipse Temurin JDK (GPLv2 with Classpath Exception)

The Classpath Exception means that applications running on this JDK are **not**
required to be GPL-licensed. You must retain the JDK’s copyright and license
notices, and provide the source link: <https://github.com/adoptium/temurin25-binaries>

### General obligations

- Do not remove or obscure any copyright or license notices from any component.
- Retain this `THIRD_PARTY_NOTICES.md` file in all redistributed copies.

> This notice is practical guidance, not legal advice. If the package will be
> sold or delivered externally, consult a qualified lawyer for a final compliance
> decision.
