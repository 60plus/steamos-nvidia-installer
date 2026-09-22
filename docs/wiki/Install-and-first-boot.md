[Manual home](README.md)

# Install and first boot

First [build the USB image](Build-the-USB-image.md#complete-build) using installer
release **0.1.4** and write the resulting `.img` to USB. Release source archives
and the signed updater bundle are not bootable images.

## Boot and install

1. Connect one monitor to the NVIDIA card and attach a wired keyboard.
2. Select the USB's UEFI entry in the motherboard boot menu. Disable Secure Boot if required.
3. Wait for the recovery desktop and open the appropriate installer shortcut.
4. The installer checks the NVIDIA GPU and running driver before showing destination disks. Check the displayed GPU, driver, and target disk's model and capacity before confirming.
5. When installation finishes, choose **Proceed** to shut down, or **Cancel** to stay on the recovery desktop.
6. After shutdown, remove only the installer USB and boot from the installed disk.

**Install SteamOS (NVIDIA) to Disk** erases the chosen disk.
**Upgrade SteamOS (NVIDIA) - keeps games & data** reinstalls the OS on a recognized
SteamOS layout while retaining the data partition. Back up important files first.

## Install on an external USB disk

Use two separate devices: the installer USB and the external target disk.
Connect both before opening the installer. External disks are marked
**USB (external)** in the list. Check the model and capacity, then choose the
external disk. A fresh installation erases it completely.

After shutdown, remove only the installer USB. Leave the installed external disk
connected and choose its UEFI entry in the computer's boot menu. The bootloader
is placed on that disk, including the standard removable-media boot path; it
selects SteamOS from the same disk. Do not unplug the disk while SteamOS is
running or suspended. Shut down before disconnecting it.

Use a USB SSD for a full installation. The minimum layout fits on a 16 GB device,
but games need additional space. This is a USB installation, not a promise
of portability between computers. Each PC must meet the graphics and UEFI
requirements. Booting the same USB installation on multiple PCs has been reported
working, but compatibility with every computer is not established.

The installer excludes its source disk and disks with mounted partitions or
active swap. If your target is missing, unmount its partitions first. This
installer supports 512-byte logical sectors. Reinstalling while keeping home
requires the standard eight-partition SteamOS layout and enough room in both
root partitions. A changed or incomplete layout stops installation before writes.

## Finish Steam setup

Follow the network and account setup screens. The first start may require an OS
update. Leave the machine powered while the NVIDIA driver is rebuilt; this adds
time to the update. If setup reports a download error, see
[Updates and recovery](Updates-and-recovery.md) before repeatedly retrying.

After setup, check Game Mode, Desktop Mode and a game you know. Confirm audio
output, resolution and controller input, then restart once.

## Display settings

Open **Steam → Settings → Display**. New display profiles start with HDR off.
You can turn HDR on manually; the choice is kept across Steam restarts. A newly
connected screen may need a Steam restart before the default is applied.

Check the monitor's own information screen for the actual resolution and refresh
rate. If Steam shows the wrong Native resolution, turn **Automatically Set
Resolution** off and select the correct mode. Image scaling is a separate setting.
For green output over HDMI, leave HDR off or use DisplayPort. See
[Troubleshooting](Troubleshooting.md).

## Xbox Bluetooth controller

Pair the controller through Bluetooth settings, then open Steam's controller
settings to test buttons, sticks, triggers and rumble. Check reconnect after
turning the controller off and on. Test Share inside a game with screenshots enabled.

Built-in SteamOS controller drivers are the default. If you deliberately built
with xpadneo, `modinfo hid_xpadneo` checks whether its module is installed.
Without xpadneo, a module-not-found response to that command is expected.

## Account and diagnostics

The local account is `deck`. Set a password in a terminal with `passwd`; there is
no shared SSH password supplied by this manual. SSH access requires an enabled
SSH service and your own password or authorized key.

The installer can perform its installation task without asking for a password.
Other administrative commands require your account password. To start SSH for
the current session after setting your password:

```bash
sudo systemctl start sshd
```

To stop it when finished:

```bash
sudo systemctl stop sshd
```

To collect a report, run as the desktop user:

```bash
steamos-nvidia-diagnostics > ~/steamos-nvidia-report.txt
```
