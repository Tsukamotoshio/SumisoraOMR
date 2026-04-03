# Third-Party Notices

This package includes or invokes the following third-party components:

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

## ReportLab
- Version: `4.4.10`
- License: BSD-3-Clause
- Official site: <https://www.reportlab.com/>

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

## PyInstaller
- Version: `6.19.0`
- License: GPLv2-or-later with Bootloader Exception
- Upstream: <https://pyinstaller.org/>
- Note: PyInstaller’s bootloader (the `.exe` wrapper) carries a special exception
  that explicitly permits packaging non-GPL applications. The resulting executable
  is therefore **not** required to be GPL-licensed.

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
