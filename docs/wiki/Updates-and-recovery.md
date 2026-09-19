[Manual home](README.md)

# Updates and recovery

## Update from Steam

Use Steam's normal system update interface. Keep at least 9 GiB free on `/home`
for repair and leave the PC powered until it finishes. Check space with:

```bash
df -h / /home /tmp
```

The updater prepares the other SteamOS slot, then rebuilds the pinned NVIDIA
driver and optional xpadneo for its kernel. Driver compilation adds time. Reboot
when the update finishes, then check your display, audio, controller and a game.

OS updates keep the currently pinned driver version. A Steam client update is
separate from a SteamOS update; check the OS version in system settings.

## Update stops near the end

A download error at the end of setup can come from driver repair, even when the
network download succeeded. Read the repair log:

```bash
sudo tail -n 80 /var/log/steamos-nvidia-repatch.log
```

For a support request, save the full log instead of photographing scrolling text:

```bash
sudo cat /var/log/steamos-nvidia-repatch.log > ~/steamos-nvidia-repatch.txt
```

Check the first failure: insufficient space, an unavailable package, signature
failure or a compiler error need different fixes. Follow
[Troubleshooting](Troubleshooting.md). Do not manually create completion markers
or mark a partially repaired boot slot valid.

## If the installed system cannot start

Keep the working installer USB. Boot it to reach the recovery desktop and copy
logs or back up files from the installed disk. If you have a usable text console,
try `Ctrl+Alt+F4`, log in as `deck` and collect diagnostics.

The Upgrade shortcut can reinstall the OS while retaining the data partition on
a recognized SteamOS layout. It is not a substitute for a backup. Fresh Install
erases the selected disk.

The repair checks the staged slot before enabling it. Automatic recovery from
power loss or a failed graphical boot is not guaranteed; the optional [Safe Graphics](Safe-Graphics.md) session must be selected manually
and does not provide automatic recovery.

## After an interrupted update

If power was lost during an update and the previous system still boots, check
the installed OS version and save the update logs. Let any active update finish
before starting another attempt through Steam's update interface.

The new slot is selected only after repair succeeds. Do not manually mark an
incomplete slot valid. If retrying fails, save the system journal as well as the
repair log, because the failure can happen before NVIDIA repair starts:

```bash
sudo journalctl -b --no-pager > ~/steamos-update-journal.txt
```

Check log timestamps against the failed attempt. An older successful repair log
does not confirm that the latest update completed. Review logs before sharing.

## Change the driver version

On the installed system, open **Change NVIDIA Driver** from Desktop Mode's
desktop shortcut or application menu. Choose a driver version or return to the
previous system. The installer USB is not a supported target.

The version list has three columns:

| Column | Meaning |
|---|---|
| Version | The exact driver package version to install. |
| GPU families (NVIDIA) | GeForce families listed in NVIDIA's support information. |
| Your GPU | A green indicator means the release lists all detected NVIDIA graphics cards. |

Versions with incompatible GPUs or unavailable support information are hidden.
The hardware match does not guarantee compatibility with every kernel or display
feature. GTX 10xx and older cards are not supported by this installer.

The manager checks the ten newest complete package sets each time you open the
version list. New drivers appear once their packages and GPU support information
are available. Choose and confirm a version to install it; the manager does not
install new releases automatically.

The operation opens a terminal for your normal sudo password and live output. No password is stored. Downloads and driver compilation can leave the window looking paused; wait for completion instead of starting another operation.

Preparation runs as a system service and continues if the terminal closes. Reopen a terminal and use the status command below if needed. A successful operation offers Restart or Later. The installer USB is not a supported target for this manager.


If `steamos-nvidia-driver` is installed, it can prepare a different NVIDIA
version without recreating the installer USB. It requires the normal self-healing
update mode, a recognized A/B installation and a read-only root filesystem.

Check the exact package version before starting:

```bash
steamos-nvidia-driver plan 610.57.04-1
```

The version above is an example, not a request to downgrade or reinstall it.
Choose the full package version you intend to test. The command prints the pinned
package URLs without changing the system. Support libraries retain their existing
pins; unsupported dependencies cause installation to stop.

Leave space on `/home` for a root partition copy plus 12 GiB, normally about
17 GiB in total. Finish pending OS updates and reboot first. Do not start another
update or turn off the PC during preparation.

Start the operation as a system service so it can continue if the terminal closes:

```bash
sudo systemd-run --unit=steamos-driver-change --collect /usr/bin/steamos-nvidia-driver install 610.57.04-1
sudo journalctl -fu steamos-driver-change
```

Preparation overwrites the inactive OS slot, not your currently running slot.
It copies the same SteamOS release with a new filesystem identifier, migrates
settings and builds the requested driver. Games on `/home` remain in place.
The previous inactive installation is replaced by this operation.

Check the result before rebooting:

```bash
sudo steamos-nvidia-driver status
```

Only a `ready` result means the new slot was prepared and selected. After reboot,
check graphics, a game, audio and suspend/resume. Future OS updates will rebuild
the newly selected driver version.

To return to the previous slot:

```bash
sudo steamos-nvidia-driver rollback
```

Reboot after the command succeeds. Before the first reboot, the same command
cancels selection of the prepared slot. After booting it, the tool validates the
previous system and selects it. A later update may replace that slot; the tool
refuses a stale rollback record. This is not automatic recovery from a graphical
boot failure. Use a working console or SSH if Game Mode cannot be displayed.

On failure, save the service journal and `/var/log/steamos-nvidia-repatch.log`.
A failed repair leaves the target disabled. Do not manually mark it valid.

Older images without this command can still be rebuilt with `--driver SPEC`.
Regular OS updates do not automatically choose a newer NVIDIA driver.

If the image was built with `--hold-updates`, Steam intentionally reports the
system as up to date. If it was built with `--no-hold-updates`, stock OS updates
remove the added NVIDIA driver. The normal build mode includes repair.

## Beta and Preview testing

A build made with `--experimental-beta` selects beta; `--experimental-preview`
selects Preview. These are different channels. Check the
OS version and update channel after first setup. Leave the PC powered while
NVIDIA is compiled, even if the progress estimate stops changing near the end.

Use a separate test disk and keep the stable installer. Main is not selected
by either option. An unfamiliar update format is rejected
before our repair hook is installed; do not bypass that check by manually
marking the new slot valid.
