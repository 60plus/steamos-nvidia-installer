[Manual home](README.md)

# Before you start

## Target PC

- NVIDIA graphics supported by the selected open kernel driver. The project targets RTX desktops; the open modules require Turing or newer. GTX 16xx also falls within this architecture range.
- **GeForce GTX 10xx and older cards are not supported by this installer.** Choosing an older version in the driver manager does not add support for them. See [NVIDIA's GPU support documentation](https://github.com/NVIDIA/open-gpu-kernel-modules#compatible-gpus).
- UEFI boot with Secure Boot disabled.
- A USB drive of at least 16 GB and an installation disk you can erase.
- A wired keyboard and one monitor connected directly to the NVIDIA card for setup.
- Internet access for Steam setup and OS updates.

Intel and AMD desktop CPUs can be used. GPU generation alone does not guarantee
compatibility: the selected driver must support the exact GPU. On hybrid laptops,
the internal screen may be connected to the integrated GPU.

## Build machine

Use Linux with root access, preferably an Arch-based system. You need Git,
Python 3, pacman, losetup, btrfs-progs, rsync, curl, kmod, zstd and binutils
(including readelf), plus a decompressor for the recovery archive.

The builder checks for at least 20,000 MiB free in the output location before
starting. Allow additional space for the downloaded input image, a build cache
on another filesystem, and any separately compiled artifacts. Repeated builds
need more space. On Windows, use a Linux VM with working loop devices
and mounts. Docker or WSL alone should not be assumed to provide these.

## Before erasing a disk

Back up your saves and other files. Confirm the destination by model and capacity,
not just a device name such as `/dev/sda`. Disconnect unrelated disks if practical.
Keep the USB installer after installation.

HDR starts off for new display profiles. Enable it later in Steam's display
settings if the connection supports it. HDMI and DisplayPort can behave differently
on the same screen. An optional [Safe Graphics](Safe-Graphics.md) session is available in new builds,
and must be selected manually. Automatic recovery from a failed boot is not provided.
