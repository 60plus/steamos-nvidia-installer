[Manual home](README.md)

# Build on Bazzite

Build the installer image directly on a PC running Bazzite. No separate virtual
machine is needed: the Bazzite kernel provides the loop devices, Btrfs, OverlayFS
and case-insensitive ext4 support that the builder uses. Test the output on the
target NVIDIA PC.

Bazzite is an image-based Fedora system and does not include pacman. Choose one
method:

| Method | Extra setup | Installer release |
|---|---|---|
| [Native build](#native-build) | None | 0.1.4 or newer |
| [Arch Linux Distrobox](#arch-linux-distrobox) | A rootful Distrobox container | 0.1.3 or newer |

Both methods keep their files in your home directory. On Bazzite this is a Btrfs
filesystem without casefold, so the
[casefold workaround](Build-the-USB-image.md#build-on-ext4-with-casefold) is not
needed. Do not layer extra packages with `rpm-ostree` for this build.

## Prepare the files

Allow at least 50 GiB free for the build work, plus about 20 GiB for the recovery
archive, the unpacked recovery image and the output image. Check with `df -h ~`.
Keep the PC awake and on mains power; compilation can be quiet for several minutes.

Open a terminal and create a build folder:

```bash
mkdir -p ~/steamos-build/input
cd ~/steamos-build
```

Clone the repository into this folder as described in
[Get the repository](Build-the-USB-image.md#get-the-repository). Then download
Valve's SteamOS 3.8.14 recovery archive through
[Valve's recovery instructions](https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227#install),
save it in `~/steamos-build/input` and unpack it.

The long number in the third command is the SHA256 checksum of the unpacked
3.8.14 recovery image. Every correct copy of that file has exactly this
checksum. The project measured it during the builds described at the end of this
page, and the installer records the same value in the images it produces. The
command feeds that expected value to `sha256sum -c`, which reads your own
unpacked file and compares. It is worth the minute it takes: an incomplete
download or a damaged unpack otherwise fails much later, in the middle of a build
that takes hours.

```bash
cd ~/steamos-build/input
bunzip2 -k steamdeck-oobe-repair-20260707.10-3.8.14.img.bz2
echo "f9aa0fa2dd618febf28a5e2a583d1e2b5a8ca3f1205be965da584602ac962f40  steamdeck-oobe-repair-20260707.10-3.8.14.img" | sha256sum -c -
```

The last command must print `OK`. If it prints `FAILED`, download and unpack the
archive again. If Valve's page offers a different file name or SteamOS version,
stop: the complete builder currently accepts only the 3.8.14 recovery image, and
a different image will not match this checksum.

## Native build

Use installer release 0.1.4 or newer. Older releases stop on Bazzite with
`FAIL: Missing tool: pacman` (or `Missing host tool: pacman` from a base-only
build); use the Distrobox method for them.

From the repository folder, run the default
[complete build](Build-the-USB-image.md#complete-build):

```bash
cd ~/steamos-build/steamos-nvidia-installer
sudo bash tools/build-complete.sh ~/steamos-build/input/steamdeck-oobe-repair-20260707.10-3.8.14.img
```

The complete builder checks the host first and stops with a clear message if
something is missing. It then creates a new work directory next to the recovery
image. The builder reads package information with the pacman included in the
SteamOS recovery image, so nothing needs to be installed on Bazzite. SELinux can
stay enforcing. The options described in the complete build section, such as
`--workdir`, work the same way here.

## Arch Linux Distrobox

This method runs the same commands inside an Arch Linux container on the Bazzite
kernel. The container must be rootful, because the builder attaches loop devices
and mounts filesystems. Root inside this container is root on the PC, so use it
only for this build.

Create and enter the container. Distrobox runs Podman through sudo, so it asks for
your Bazzite password. On the first `enter` it may also ask you to choose a
password for your user inside the container; `sudo` inside the container then
uses that password:

```bash
distrobox create --root --name steamos-build --image docker.io/library/archlinux:latest
distrobox enter --root steamos-build
```

Inside the container, install the build tools and run the same default complete
build:

```bash
sudo pacman -Syu --needed git python btrfs-progs rsync curl kmod zstd binutils util-linux tar gzip xz e2fsprogs dosfstools
cd ~/steamos-build/steamos-nvidia-installer
sudo bash tools/build-complete.sh ~/steamos-build/input/steamdeck-oobe-repair-20260707.10-3.8.14.img
```

Your home directory is shared with the container, so the output appears in the
same folder on Bazzite. When you no longer need the container, leave it with
`exit` and remove it with `distrobox rm --root steamos-build`. This does not
delete the files in your home directory.

## Result

The builder writes the image and its checksum next to the input:

```text
~/steamos-build/input/steamdeck-oobe-repair-20260707.10-3.8.14-nvidia-usbinstall.img
~/steamos-build/input/steamdeck-oobe-repair-20260707.10-3.8.14-nvidia-usbinstall.img.sha256
```

Check it, then follow [Write the USB](Build-the-USB-image.md#write-the-usb):

```bash
cd ~/steamos-build/input
sha256sum -c steamdeck-oobe-repair-20260707.10-3.8.14-nvidia-usbinstall.img.sha256
```

While the build runs the file ends in `-nvidia-usbinstall.partial.img`, and it is
renamed only when the build has succeeded, so a leftover partial file is never
something you can flash. Delete it before building again. The work directory
(`~/steamos-build/input/complete-build-<date>-<time>`) keeps `build.log`, the
artifacts and the driver cache. Remove it only after the build has finished and
`losetup -a | grep steamos-build` prints nothing. The builder's mounts exist only
in its own private mount namespace, so `findmnt` does not show them.

## Troubleshooting

- `FAIL: Missing tool: pacman` or `Missing host tool: pacman`: the checkout is
  older than 0.1.4. Update it or use the Distrobox method. Do not install pacman
  on Bazzite.
- Loop device or mount permission errors in Distrobox: the container was created
  without `--root`. Create a rootful container as shown above.
- The recovery image checksum differs: download and unpack the archive again.
- The build stops with `Driver copy failed` and `build.log` contains
  `rsync: failed to open files-from file ... Permission denied`: the build was
  started as a system service, for example with `systemd-run`. SELinux then
  confines rsync. Start the build from a terminal as shown above.
- The builder refuses to start because the output already exists: move the old
  image and its `.sha256` file to another folder, then run the build again.

## Validation

Both methods were tested on Bazzite 44 (bazzite-nvidia-open stable image, kernel
7.2.4) in a virtual machine with SELinux enforcing. The builds were started from a
remote shell without a logged-in desktop session, so the interactive Distrobox
password prompts were not part of the test. Each method completed a full build
from the original SteamOS 3.8.14 recovery image. Read-only checks of both outputs
passed: checksum, NVIDIA driver and modules for the image kernel, addon integrity,
32-bit and 64-bit NVIDIA libraries, the four compiled artifacts and the updater
configuration. Both builds recorded identical package and payload file lists. The
native steps on this page were then repeated exactly as written with the 0.1.4
source, and that build passed the same checks. The same builder change was also
checked with a complete build on an Arch Linux host.

The image from that native 0.1.4 build was then installed on an NVIDIA PC. It
booted, installed, and completed the first OS update to SteamOS 3.8.16 with the
pinned driver rebuilt automatically and the recorded integration files intact.
The earlier outputs on this page were not installed. Other Bazzite editions, for
example the Deck edition, were not tested separately.
