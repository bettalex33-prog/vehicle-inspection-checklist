# Standalone macOS third-party notices

The standalone builder includes the following upstream texts in this directory.
Build tools are listed separately because their presence in the build
environment does not establish that their complete distributions ship in the app.

| Component | Terms and bundled text |
| --- | --- |
| CPython 3.15.0 and standard-library components | PSF License 2 and incorporated notices in `CPython/LICENSE.txt` and `CPython/license.html` from the framework installer. |
| Tcl 9.0.4 and Tk 9.0.4 | Tcl/Tk license terms, provided separately in `Tcl/license.terms` and `Tk/license.terms`. |
| OpenSSL | Apache 2.0 and attribution in the installed Python incorporated-software notices, `CPython/license.html`. |
| Zstandard 1.5.7 | BSD 3-Clause option. The library notice is reproduced below. |
| PyInstaller 6.22.3 | GPL-2.0-or-later with its distribution exception, in the build-wheel notices. |
| altgraph 0.17.5 and macholib 1.16.4 | MIT, in the build-wheel notices. |
| packaging 26.3 | Apache-2.0 or BSD-2-Clause, with both texts in the build-wheel notices. |
| pyinstaller-hooks-contrib 2026.10 | Per-file terms, in the build-wheel notices. |
| setuptools 84.2.1 | MIT and applicable vendored terms, in the build-wheel notices. |

The Python.org runtime contains upstream build provenance strings, including
`/Users/nad`. The builder reports these separately from active Mach-O paths
and Python code filenames. The standalone bundle rewrites native load commands
for relocation. PyInstaller applies an ad-hoc signature for local ARM64 execution.

## Zstandard BSD license

Copyright (c) Meta Platforms, Inc. and affiliates. All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name Facebook, nor Meta, nor the names of its contributors may
   be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
