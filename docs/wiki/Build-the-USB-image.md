[Manual home](README.md)

# Build the USB image

## Get the repository

Copy the HTTPS clone URL from this repository's Code menu. Replace
`REPOSITORY_URL` below with that address and run these commands on Linux:

```bash
git clone --branch main REPOSITORY_URL
cd steamos-nvidia-installer
```

Keep the full checkout. The main script needs the files in `lib` and `scripts`.

## Download the recovery image

Get the image through [Valve's recovery instructions](https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227#install).
Unpack the archive to obtain an `.img` file. Use the recovery/repair image, not
an image of an already installed system. Replace the example paths below with yours.

## Build

The basic command below builds NVIDIA support and the standard installer helpers.
It does not compile or add the corrected overlay, Remote Play artifacts or desktop
updater automatically. You can include these components; to build
the complete configuration, build the artifacts described below and supply all
relevant options together.

```bash
sudo ./steamos-nvidia-installer.sh /path/to/recovery.img
```

The script creates a copy ending in `-nvidia-usbinstall.img`. It keeps the original
image unchanged and prints the output path when finished. Do not flash an output
from a failed or interrupted build.

The default resolves the current Arch NVIDIA driver. To select a specific build:

```bash
sudo ./steamos-nvidia-installer.sh --driver 610.57.04-1 --trim-cuda /path/to/recovery.img
```

Choose a driver compatible with both your GPU and the image kernel. `--trim-cuda`
removes CUDA, OpenCL and OptiX libraries; omit it if your applications need them.

## Options

| Option | Purpose |
|---|---|
| `--driver SPEC` | Select `latest`, a version prefix or an exact package version. |
| `--installer-update-source FILE` | Include the desktop updater with a maintainer-supplied source and public signing key. |
| `--mangoapp-dir DIR` | Include the corrected performance overlay artifact. |
| `--gamescope-dir DIR` | Include the stable Gamescope capture correction. |
| `--remote-play-dir DIR` | Include the SDR receiver and decoder color correction. |
| `--nvenc-dir DIR` | Include the 32-bit to 64-bit hardware-encoding bridge. |
| `--workdir DIR` | Choose a reusable build cache. |
| `--trim-cuda` | Reduce the driver payload by removing compute libraries. |
| `--xpadneo` | Add the optional Xbox Bluetooth driver. |
| `--no-xpadneo` | Use built-in controller support, the default. |
| `--xpadneo-version TAG` | Select an exact xpadneo tag; default `v0.10.4`. |
| `--experimental-preview` | Select Valve's Preview channel for a separate test installation. Self-healing mode only; cannot be combined with beta. |
| `--experimental-beta` | Select Valve's beta channel for first setup and OS updates. Experimental, self-healing mode only. |
| `--hold-updates` | Freeze OS updates; Steam reports the system as up to date. |
| `--no-hold-updates` | Use stock updates, which remove the added NVIDIA driver. |
| `--no-installer` | Produce a patched bootable image without desktop installation shortcuts. |
| `--skip-sigcheck` | Disable package signature checks during building. Avoid for normal use. |

The normal update mode repairs NVIDIA after OS updates. No extra flag is needed.
The older `build-xpadneo.sh` wrapper explicitly enables xpadneo and forwards other
options to the main script. Start without xpadneo unless your controller needs it.
When enabled, xpadneo also changes system-wide Bluetooth settings.

For package signature failures, check the clock, keyring and failing package
before considering a bypass. See [Troubleshooting](Troubleshooting.md).

## Include the desktop updater

Use the included public source configuration with
`--installer-update-source config/github-stable.json`. This enables
[SteamOS NVIDIA Installer Update](Installer-Updates.md) in the installed system.
It requires the normal self-healing update mode. Without this option, the desktop
updater is not included. Do not replace the source or signing key with one from
an untrusted download.

## Write the USB

Use an image-writing tool to write the entire output image to the USB device.
Copying the file onto a formatted USB drive is not enough. Verify the destination
by capacity and model; writing the image erases the USB.

On Linux, list devices with:

```bash
lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS
```

Wait for writing and verification to finish before removing the drive. You can
record the image checksum with `sha256sum /path/to/output.img`.

Continue with [Install and first boot](Install-and-first-boot.md).

## Including the corrected performance overlay

Maintainers can build the pinned MangoApp artifact with:

```bash
sudo bash tools/build-mangoapp.sh /path/to/disposable-steamos-root /path/to/new-mangoapp-output
```

Use a disposable SteamOS build root with `/proc`, `/dev` and DNS prepared. The
script installs build dependencies there. Do not pass an installed system or the
root filesystem of a running PC. The output directory must not already exist.
Use the same SteamOS library baseline as the target image.

Add `--mangoapp-dir /path/to/new-mangoapp-output` to the image build command.
The release packer accepts the same option for an integration update. Include all
three output files together: `mangoapp`, `mangoapp-build.json` and
`MangoHud-LICENSE`. See [How it works](How-it-works.md#performance-overlay).

## Experimental beta and Preview images

Use `--experimental-beta` or `--experimental-preview` only for a separate test
installation. Choose one. The recovery image's original OS remains the
installation base; the selected channel supplies subsequent OS updates,
including first setup. These options do not copy individual experimental
packages into the older system. Valve's server chooses the downloaded version,
which can change after the image is built.

Keep a working stable installer and use a separate test disk. Neither option
enables main or establishes compatibility with every future experimental build.

## Building the capture correction

An image built with all components in the [build guide](Build-the-USB-image.md#complete-build) already includes the capture, receiver and NVENC
artifacts described below. These sections explain how to reproduce that build.
The options are optional for custom builders; users of that image do not
need to build or enable the fixes themselves.
Use a disposable stable SteamOS build root with `/proc`, `/dev`, `/sys` and DNS
available. The tool reinstalls build dependencies there because recovery images
remove development headers while retaining their package database entries. Never use `/`
or the installed system you are testing as the build root.

```bash
sudo bash tools/build-gamescope.sh /path/to/disposable-steamos-root /path/to/new-gamescope-output
```

The output directory must not exist. The tool stages files under `root/` and
records provenance in `gamescope-build.json`. It does not install them into the
running system. Keep the license files with the artifact. Compilation and pixel
conversion checks do not replace a physical Remote Play and screenshot test.

To include the artifact in a stable installer, add `--gamescope-dir /path/to/new-gamescope-output`
to the normal image build command. It cannot be combined with beta or Preview.
The recovery desktop keeps its original Gamescope. The private capture build is
activated after updating to SteamOS 3.8.16 with Gamescope 3.16.23.4-1.

## Building Remote Play receiver support

Build the decoder and receiver environment helper in a disposable stable SteamOS
3.8 build root, then pass the artifact directory to the installer:

```sh
sudo tools/build-remote-play.sh /path/to/build-root /path/to/remote-play-artifact
sudo ./steamos-nvidia-installer.sh --remote-play-dir /path/to/remote-play-artifact /path/to/recovery.img
```

The artifact includes source revision, patch hashes, binary hashes and the decoder
license. It adds SDR receiving support without replacing files inside Steam's
user installation. Combine it with `--gamescope-dir` to include the separate
sending-side capture correction. Test both directions after installation and
again after the first SteamOS update. The receiver policy uses SDR; HDR receiving
is not provided by this configuration.

## Building the NVENC encoding bridge

Build the pinned encoding driver and helper in the same kind of disposable stable
SteamOS root used for the receiver build:

```sh
sudo bash tools/build-nvenc.sh /path/to/build-root /path/to/nvenc-artifact
sudo ./steamos-nvidia-installer.sh --nvenc-dir /path/to/nvenc-artifact /path/to/recovery.img
```

The builder records the source revision and file hashes and includes the upstream
license. The bridge is optional. Combine it with the capture and receiver artifacts
when testing Remote Play in both directions. It installs a 32-bit VAAPI encoding
driver and a 64-bit user service. It does not replace the private receiver decoder.
Test streaming, reconnect, reboot, suspend and the first OS update before distributing
an image. Hardware tests of a separately installed prototype do not validate a new image.

## Complete build

Release downloads do not contain Valve's recovery image or a complete SteamOS
image. Obtain the recovery image directly from Valve using the link above.
The commands below combine all of this project's integration components.

First prepare a disposable, writable SteamOS 3.8 build root on a Linux filesystem.
It must contain the system's `/etc`, `/usr` and package database, have working
package repositories and DNS, and have `/proc`, `/dev` and `/sys` mounted inside it.
Use a copy for building, never your running installation or your only recovery image.
Do not substitute an ordinary Arch root: the resulting binaries need the target
SteamOS library versions. Creating this build root is a separate prerequisite;
the artifact scripts check it but do not create it.

From the repository root, replace the two absolute paths below. Keep each output
directory new; artifact builders refuse to overwrite existing outputs.

```bash
build_root=/absolute/path/to/disposable-steamos-root
artifacts=/absolute/path/to/new-artifacts
mkdir -p "$artifacts"
sudo bash tools/build-mangoapp.sh "$build_root" "$artifacts/mangoapp"
sudo bash tools/build-gamescope.sh "$build_root" "$artifacts/gamescope"
sudo bash tools/build-remote-play.sh "$build_root" "$artifacts/remote-play"
sudo bash tools/build-nvenc.sh "$build_root" "$artifacts/nvenc"

sudo ./steamos-nvidia-installer.sh \
  --driver 610.57.04-1 --trim-cuda \
  --mangoapp-dir "$artifacts/mangoapp" \
  --gamescope-dir "$artifacts/gamescope" \
  --remote-play-dir "$artifacts/remote-play" \
  --nvenc-dir "$artifacts/nvenc" \
  --installer-update-source config/github-stable.json \
  /absolute/path/to/recovery.img
```

The pinned configuration uses a SteamOS 3.8.14 recovery image and the normal
stable update path to 3.8.16. It does not import a beta kernel. Check the image's
reported version before building; a different recovery or target OS version
requires fresh compatibility testing. Omit `--trim-cuda` if you need compute libraries.

Keep the artifact metadata and license files together with each artifact. After a
successful build, calculate the output checksum, write the image to USB and test
installation, the first OS update, suspend/resume, the overlay and both Remote
Play directions. Successful compilation alone does not verify those functions.
