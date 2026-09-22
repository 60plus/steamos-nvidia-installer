[Manual home](README.md)

# Build on Windows

Build inside an x86_64 Linux virtual machine while keeping Windows as your main
system. No NVIDIA GPU passthrough is needed for compilation. Test the output on
real hardware.

The project's own Windows builds ran in an Arch Linux guest under QEMU with
hardware acceleration. Any hypervisor works as long as the guest runs an ordinary
Linux distribution kernel. The VirtualBox route below was then followed on
Windows 11 and produced a complete, verified image. Read
[Validation](#validation) for what that test covered and what it did not.

## Why a virtual machine, not WSL or Docker

WSL2 and Docker Desktop containers share one kernel supplied by Microsoft, and
that kernel is built without `CONFIG_UNICODE`. SteamOS keeps its home partition on
ext4 with the casefold feature, and the builder mounts that partition from the
recovery image, so the attempt ends with:

```text
EXT4-fs (loop2): Filesystem with casefold feature cannot be mounted without CONFIG_UNICODE
```

Making the container privileged does not help, because the limit is in the shared
kernel and not in the container. Checked on the WSL2 kernel
6.6.87.2-microsoft-standard-WSL2, which does provide loop devices, OverlayFS and
Btrfs, but not case-insensitive ext4. A guest running a distribution kernel, such
as Arch Linux, has that option and works.

The flag is set on Valve's image but no directory on that partition uses it, so
the obstacle can in principle be removed by clearing it. This page does not do
that, and the builder is not tested that way here.

This rules out building inside WSL2, not using WSL2 at all. WSL2 supports nested
virtualization, so it can host a virtual machine whose own kernel does have the
missing option. The project's builds were produced that way.

## Hardware acceleration

Give the guest hardware acceleration. Which accelerator you get depends on what
else runs on the machine. VirtualBox uses its own only while no Windows hypervisor
is active; with WSL2, Docker Desktop, Hyper-V or Memory Integrity enabled it runs
on the Windows Hypervisor Platform instead, which is slower. QEMU can use that
platform directly. The work is CPU bound: compiling the driver, the overlay and
the other artifacts takes a long time, and QEMU's software emulation is far slower
again. No GPU passthrough is needed, so the NVIDIA card stays with Windows.

If acceleration fails to start, find out why instead of falling back to software
emulation. Check that virtualization is enabled in firmware, and for a nested
setup that the outer hypervisor exposes it. A build that quietly ran without
acceleration looks like a hung build.

## Set up Linux

1. Enable CPU virtualization in firmware if disabled.
2. Install [VirtualBox for Windows](https://www.virtualbox.org/wiki/Downloads).
3. Download the x86_64 ISO from [Arch Linux](https://archlinux.org/download/) and
   verify it following the instructions on that page.
4. Create a Linux/Arch Linux 64-bit VM with 4 CPU cores, 8 GB RAM, NAT networking
   and a new dynamically allocated 100 GB virtual disk. Windows also needs enough
   real free space for that disk to grow. Allow more space for optional artifacts.
5. Attach the ISO and boot. Run `archinstall`, select the empty virtual disk,
   ext4, the Linux kernel and a minimal profile. Create your own sudo-capable user
   and configure networking. Attach no physical disks. The
   [Arch installation guide](https://wiki.archlinux.org/title/Installation_guide)
   describes manual installation if preferred.
6. Shut down, detach the ISO and start the installed guest.

Install build dependencies inside Linux:

```bash
sudo pacman -Syu --needed git python btrfs-progs rsync curl kmod zstd binutils util-linux tar gzip xz openssh
sudo systemctl enable --now sshd
mkdir -p "$HOME/steamos-build/input" "$HOME/steamos-build/work"
```

Keep the repository, input copy, work directory and artifacts on the Linux disk.
Use Windows shared folders only to transfer files, not as a chroot or build cache.

## Transfer the recovery image

In VirtualBox NAT port forwarding add TCP host IP `127.0.0.1`, host port `2222`,
guest port `22`, leaving guest IP blank. Choose another unused host port if needed.
This exposes the guest SSH service only on the local Windows host.

Download and unpack the image through
[Valve's recovery instructions](https://help.steampowered.com/en/faqs/view/65B4-2AA3-5F37-4227#install).
The project does not distribute Valve's image.

In PowerShell, replace `builder` with your Linux username and the local paths with yours:

```powershell
Get-Command ssh, scp
ssh -p 2222 builder@127.0.0.1
```

If missing, install the Windows OpenSSH Client optional feature. Compare the first
connection fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` in
the VM console. Exit the SSH session, then transfer the unpacked image:

```powershell
Get-FileHash -Algorithm SHA256 'C:/Users/YOUR_USER/Downloads/recovery.img'
scp -P 2222 'C:/Users/YOUR_USER/Downloads/recovery.img' builder@127.0.0.1:steamos-build/input/recovery.img
```

Compare with `sha256sum ~/steamos-build/input/recovery.img` inside Linux.

## Check and build

Download the source archive for [release 0.1.4](https://github.com/60plus/steamos-nvidia-installer/releases/tag/v0.1.4), or clone tag `v0.1.4`, inside Linux. Keep the full
checkout. From its root run:

```bash
sudo bash tools/check-build-host.sh "$HOME/steamos-build/input/recovery.img" "$HOME/steamos-build/work"
```

The check validates tools and free space, then creates a temporary sparse image
to test loop devices, Btrfs and OverlayFS in a private mount namespace. It removes
its own probe after detaching. It does not modify the input image. A cleanup
failure leaves the directory in place and prints its path.

For the base installer:

```bash
set -o pipefail
sudo bash ./steamos-nvidia-installer.sh \
  --workdir "$HOME/steamos-build/work" \
  "$HOME/steamos-build/input/recovery.img" \
  2>&1 | tee "$HOME/steamos-build/build.log"
```

The base command does not include all release extras. Use
[Complete build](Build-the-USB-image.md#complete-build) to prepare a disposable
SteamOS root and build/include the overlay, Remote Play fixes and updater. The
Arch VM is the host, not a substitute for that SteamOS build root. Preparing that
root remains a separate prerequisite described in the complete build guide.
The host probe does not validate the SteamOS version or artifact compatibility.

Keep Windows and the guest awake. Compilation can produce little output for a
while; inspect the log before assuming a freeze.

## Start the build from PowerShell

For a complete build, choose a new work-directory name that does not exist yet.
After copying the image and cloning the repository in the guest, you can use the
PowerShell helper from a checkout on Windows. It runs the same host probe and
Linux builder over SSH. It does not create a VM, upload files or install a Windows
driver. Enter the guest account password when SSH or sudo asks for it.

```powershell
./tools/build-on-windows.ps1 -Guest builder@127.0.0.1 -Port 2222 `
  -Repository /home/builder/steamos-nvidia-installer `
  -Image /home/builder/steamos-build/input/recovery.img `
  -WorkDirectory /home/builder/steamos-build/complete-01
```

The default runs the complete builder and prepares its SteamOS environment and
artifacts automatically. Allow at least 50 GiB free inside the VM. Use
`-BuilderArguments` with complete-builder options such as `--keep-cuda` or
`--driver`. Add `-BaseOnly` for the older base-only command, with an existing work
directory and options accepted by the main installer. The helper propagates a failed check or build;
it does not change PowerShell execution policy or bypass SSH host verification.

## Retrieve the output

Only after a successful build, compute its checksum inside Linux:

```bash
sha256sum "$HOME/steamos-build/input/recovery-nvidia-usbinstall.img"
```

In Windows PowerShell:

```powershell
scp -P 2222 builder@127.0.0.1:steamos-build/input/recovery-nvidia-usbinstall.img 'C:/Users/YOUR_USER/Downloads/recovery-nvidia-usbinstall.img'
Get-FileHash -Algorithm SHA256 'C:/Users/YOUR_USER/Downloads/recovery-nvidia-usbinstall.img'
```

Compare both hashes before writing USB from Windows. Do not flash an incomplete
output from a failed build. The VM needs no access to your physical USB disk.

## Troubleshooting

- Missing tool: install the packages above inside the installed Linux guest.
- Loop, namespace or mount denied: run with sudo inside a full Linux VM. WSL and
  Docker Desktop cannot run this build as documented here; see
  [Why a virtual machine, not WSL or Docker](#why-a-virtual-machine-not-wsl-or-docker).
- Shared filesystem rejected: copy the input and work directory to the guest's
  ext4, Btrfs or XFS disk. Do not build on 9p, vboxsf, SMB or a Windows mount.
- Low space: check `df -h` inside Linux and Windows free space. Enlarging the
  virtual disk does not automatically grow the guest partition/filesystem.
- Failed build: preserve its log and cache. Do not bypass signature verification
  or recursively delete directories containing mounts.
- SSH refused: check the guest, sshd and the NAT port rule.
- The build stops during the package database sync with `Could not resolve host:
  steamdeck-packages.steamos.cloud`, even though the guest itself resolves names:
  the builder copies the host's `/etc/resolv.conf` into the build chroot, and on a
  guest that resolves through systemd-resolved that file can still be the stock one
  with no `nameserver` line. Point it at the stub resolver and run the build again:

  ```bash
  sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
  ```

  Then start the build again with a work directory that does not exist yet. The
  complete builder refuses to reuse the one the failed run left behind, and that
  directory holds the log worth keeping.

## Validation

These steps were run on Windows 11 with VirtualBox 7.1.10. A new guest was created
to the specification above: Arch Linux 64-bit, 4 CPU cores, 8 GB RAM, NAT with host
port 2222 forwarded to guest port 22, and a 100 GB dynamically allocated disk. The
guest checked out release 0.1.4 from GitHub, and `tools/build-on-windows.ps1` was
started from PowerShell on Windows exactly as shown above. The host check passed,
the complete builder compiled all four artifacts and wrote the image and its
checksum, and no loop device or mount was left behind.

The output was then verified read only: its checksum, the NVIDIA driver and modules
for the image kernel, addon integrity, the 32-bit and 64-bit NVIDIA libraries, a
byte comparison of the four compiled artifacts against the build artifacts, and the
updater configuration. It recorded installer version 0.1.4 and the release commit.

What this test did not cover. The Windows host was in daily use rather than freshly
installed, and VirtualBox was already present. That host also runs WSL2, so
VirtualBox used the Windows hypervisor backend instead of its own, which is slower;
a host without WSL2 normally runs faster than this test did. The guest was
installed non-interactively instead of working through `archinstall` by hand, it
downloaded the recovery archive itself instead of receiving it from Windows over
scp, and it was reached with an SSH key rather than the password prompts described
above. The image was checked inside the guest, so the retrieval step on this page,
copying it back to Windows and comparing hashes there, was not exercised either.
The image was not written to USB or installed on hardware.

For a Bazzite host, see [Build on Bazzite](Build-on-Bazzite.md).
