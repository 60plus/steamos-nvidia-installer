[Manual home](README.md)

# Build on Windows

Build inside an x86_64 Linux virtual machine while keeping Windows as your main
system. No NVIDIA GPU passthrough is needed for compilation. Test the output on
real hardware.

The project has built images in an Arch Linux QEMU guest on Windows. The
VirtualBox setup below is a proposed reproducible route and still needs a full
clean-install test. Do not read it as completed VirtualBox acceptance.

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

Download the source archive for [release 0.1.1](https://github.com/60plus/steamos-nvidia-installer/releases/tag/v0.1.1), or clone tag `v0.1.1`, inside Linux. Keep the full
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
- Loop, namespace or mount denied: run with sudo inside a full Linux VM. Docker
  or WSL alone may lack the required kernel facilities.
- Shared filesystem rejected: copy the input and work directory to the guest's
  ext4, Btrfs or XFS disk. Do not build on 9p, vboxsf, SMB or a Windows mount.
- Low space: check `df -h` inside Linux and Windows free space. Enlarging the
  virtual disk does not automatically grow the guest partition/filesystem.
- Failed build: preserve its log and cache. Do not bypass signature verification
  or recursively delete directories containing mounts.
- SSH refused: check the guest, sshd and the NAT port rule.

Bazzite host support requires a separate test. This guide does not claim it.
