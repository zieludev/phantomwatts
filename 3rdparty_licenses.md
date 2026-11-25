# Third-party components

Components this repository depends on or refers to. None of their code is copied into the
repository.

| source | name | license type | license link |
| --- | --- | --- | --- |
| https://github.com/google/bumble | bumble (Bluetooth stack, the one runtime dependency) | Apache-2.0 | https://github.com/google/bumble/blob/main/LICENSE |
| https://github.com/dlech/windows-vhci-driver | dlech/windows-vhci-driver: its `winvhci` Python package (tests, optional at run time), its Windows driver, its install action and its bridge script (Windows workflow only) | MIT | https://github.com/dlech/windows-vhci-driver/blob/master/LICENSE |
| https://github.com/pytest-dev/pytest | pytest (tests) | MIT | https://github.com/pytest-dev/pytest/blob/main/LICENSE |
| https://github.com/zacharyedwardbull/pycycling | pycycling (tests, as an independent parser) | MIT | https://github.com/zacharyedwardbull/pycycling/blob/master/LICENSE |
| https://github.com/hbldh/bleak | bleak (Windows tests only) | MIT | https://github.com/hbldh/bleak/blob/develop/LICENSE |
| https://github.com/astral-sh/ruff | ruff (lint and format) | MIT | https://github.com/astral-sh/ruff/blob/main/LICENSE |
| https://github.com/DetachHead/basedpyright | basedpyright (type check) | MIT | https://github.com/DetachHead/basedpyright/blob/main/LICENSE.txt |
| https://github.com/astral-sh/uv | uv_build (build backend) | MIT OR Apache-2.0 | https://github.com/astral-sh/uv/blob/main/LICENSE-MIT |
| https://github.com/actions/checkout | actions/checkout (CI only) | MIT | https://github.com/actions/checkout/blob/main/LICENSE |
| https://github.com/astral-sh/setup-uv | astral-sh/setup-uv (CI only) | MIT | https://github.com/astral-sh/setup-uv/blob/main/LICENSE |

Their own dependencies are pinned in `uv.lock`. Going by the metadata each package publishes,
every one of them is under a permissive licence: MIT, MIT-0, Apache-2.0, a BSD licence or
PSF-2.0.

Two dependencies that Bumble declares are left out on purpose, with `exclude-dependencies` in
`pyproject.toml`: `libusb1` (LGPL-2.1-or-later) and `libusb-package` (Apache-2.0, but it bundles
libusb, which is LGPL-2.1-or-later). They are not in `uv.lock` and not in any environment `uv`
builds from it. Bumble imports them only for its USB transports, which this project never
opens. Installing phantomwatts into another project resolves Bumble's own metadata and does
install them, unless that project excludes them the same way.

mypy is not the type checker here because it depends on `pathspec`, which is MPL-2.0.

The Bluetooth SIG specifications this was implemented from are not part of the repository.
