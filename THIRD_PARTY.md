# Third-party components

This project is based on [28allday/steamos-nvidia-installer](https://github.com/28allday/steamos-nvidia-installer).
Its original copyright and MIT license are preserved in LICENSE.

The optional corrected performance overlay is built from
[MangoHud](https://github.com/flightlessmango/MangoHud), revision
`33c2c7ddbb72c15e19a42163d75424d5804f8ec8`. The patches in
`patches/mangohud` include upstream commit
`4e69793b9b77a394b8f7842a78de235eaf3859df`, with its authorship retained,
and local changes hiding unsupported NVIDIA sensor fields and displaying
detected hardware names in the detailed Steam overlay.

The artifact includes the upstream license as `MangoHud-LICENSE` and provenance
in `mangoapp-build.json`. Distribute those files with the binary. MangoHud and
its dependencies retain their respective licenses; the installer license does
not replace them. The build script fetches the pinned source and its submodules.

The experimental capture correction uses [Gamescope](https://github.com/ValveSoftware/gamescope)
3.16.23.4, revision `2b79e07b3da1723c7e5c5f44f18de36c6cb78b9e`.
`patches/gamescope` backports Matthew Schwartz's upstream commit
`ff6b924fd0634a51d0fb3755c56c01dca1daadc1`, retaining the author and source link.
The backport adapts the screenshot context and dimension variable names to the
stable source. Gamescope retains its MIT license; its dependencies retain their
own licenses. Distribute the artifact's license files and source metadata with it.

The sampled-texture correction adapts Matthew Schwartz's
[Gamescope PR 2164](https://github.com/ValveSoftware/gamescope/pull/2164)
to the stable shared capture pool. It adds the Vulkan sampled usage required by
the RGB-to-NV12 conversion shader.

The optional Remote Play receiver artifact uses
[nvidia-vaapi-driver](https://github.com/elFarto/nvidia-vaapi-driver), revision
`a03711106b5e297a64a704c876aeb776cbce957b`, under the MIT license. Its COPYING file
is distributed as `licenses/nvidia-vaapi-driver-LICENSE`. The local patch changes
NV12 chroma export descriptors. `tools/build-remote-play.sh` records source and
patch hashes with the artifact. No Steam executable or SDL binary is redistributed.

The optional NVENC encoding bridge uses the MIT-licensed
[efortin/nvidia-vaapi-driver encoding branch](https://github.com/efortin/nvidia-vaapi-driver/tree/feat/nvenc-support),
revision `3a58095f1833c997fd4f0a73ce3fa0300cdc20fc`. Its COPYING license is
included in the artifact. The build adjusts only the i386 pkg-config search paths
for Arch/SteamOS and includes that cross-compilation file with the provenance.
The 32-bit encoding driver and 64-bit helper remain separate from the receiver
artifact. This upstream feature is experimental (draft pull request 427).
