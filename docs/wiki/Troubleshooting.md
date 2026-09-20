[Manual home](README.md)

# Troubleshooting

Change one setting at a time and keep the original value. Capture a report before applying a workaround so the failure can be compared with the result.

## Installer stops at the GPU check

The installer checks the NVIDIA driver before offering a destination disk. All
NVIDIA graphics cards must be detected by the driver included in the image, and
the running driver must match its installed package. This check works offline.

GTX 10xx and older are outside the supported range. For a GTX 16xx or RTX card,
check `nvidia-smi` in the installer desktop. If it fails, save the boot errors
and check that the image uses a driver supporting your card. If the driver was
changed during the live session, restart before trying again. A successful check
confirms driver initialization on this PC, not every game or display feature.

## Black screen

First distinguish a USB boot failure from an installed-system failure. Record whether the firmware logo appears, whether a text console works and which port connects the display.

Try one display connected directly to the NVIDIA card. Remove adapters for the first comparison. Try `Ctrl+Alt+F4` to reach a text console and log in as `deck`. If a console is available, check `nvidia-smi`, `uname -r` and the boot errors. A successful driver query does not rule out a session or display problem.

The repository describes `steamos-session-select plasma` as a way to switch to Desktop Mode from a usable console. It changes the selected session, so record that change. New builds include an optional [Safe Graphics](Safe-Graphics.md) session.

## HDR and HDMI

**Try DisplayPort if HDMI gives you display problems.** Connect the monitor
directly to the NVIDIA card, without an adapter. This is a useful first check for
a green or blank screen, flickering, or problems enabling HDR and VRR.

HDR and VRR support depends on the GPU, driver, display and connection.
Check each feature separately. A working DisplayPort connection does not mean
the same settings will work over HDMI.

If HDMI produces a green or blank image with HDR enabled, switch to a working
connection such as DisplayPort and turn HDR off in **Steam > Settings > Display**.
Before reconnecting HDMI, select a lower refresh rate on the working connection.
Start with 60 Hz and test higher rates separately. HDR may work at a lower rate
even when a higher rate works only with HDR off. No single maximum applies to
every GPU, cable and display. Reconnect HDMI and check the monitor's signal information. HDMI and DisplayPort
can behave differently on the same display.

New display profiles start with HDR off; an existing manual ON choice is retained.
Restart Steam after connecting a new monitor if its default has not been applied.
The setting does not force a resolution, refresh rate or scaling value.

If Steam's Native label shows 1080p on a 1440p screen, turn **Automatically Set
Resolution** off and select the correct mode. Check the monitor's own information
screen to distinguish the physical output from Steam's maximum game resolution.

## Flicker, missing refresh rate or TV problems

Start with a standard refresh rate and temporarily disable VRR and HDR through the available display settings. Record whether the problem affects Desktop Mode, Game Mode or both.

Test another cable and port separately. Record HDMI versus DisplayPort, monitor or TV model, resolution, refresh rate and scaling. Do not apply several compositor environment variables at once.

## Black border around Game Mode notifications

Steam notifications can have an opaque black background in Game Mode while
Desktop Mode notifications look normal. This has been observed on both HDMI
and DisplayPort, including with HDR off. The background disappears with the
notification.

There is no confirmed fix in this installer. Safe Graphics has not removed this
symptom. Include the NVIDIA driver and Gamescope versions in a support report,
and say whether the background remains after the notification closes.

## Red and blue are swapped in Remote Play or screenshots

Compare the local game image with the receiving device or saved image. If the
local image is correct but red and blue are swapped in the capture, record the
Gamescope and NVIDIA driver versions, the capture method, and the screenshot
format. Use a scene containing distinct red, green and blue areas.

An image built with all components in the [build guide](Build-the-USB-image.md#complete-build) includes both Remote Play color corrections. There is
nothing extra to enable: Gamescope corrects capture when SteamOS sends video,
and the private NVIDIA decoder corrects colors when SteamOS receives video.
Both directions have passed hardware tests. This does not establish correct
colors for every screenshot format; report screenshot failures separately.

When building an image yourself, include the artifacts with `--gamescope-dir`
and `--remote-play-dir`. These are build options, not switches that users of the
resulting image need to set. The SteamOS update repair hook preserves the included
artifacts. Installer Update does not add their binaries to older installations
that lack them. See [How it works](How-it-works.md#capture-color-correction).
A green HDMI display or a frozen session is a different symptom.

## No HDMI or DisplayPort audio

Check the selected output and mute state in the desktop audio settings. Run `wpctl status` as the desktop user and record whether the display audio device appears. Test before and after sleep and after reconnecting the cable.

A display working does not prove its audio output is selected.

## Controller pairs but input fails

Start with the built-in SteamOS driver. Check buttons, Share and rumble in Steam and in a game before adding another driver. If you explicitly included xpadneo, check
`modinfo hid_xpadneo`. Record the Bluetooth adapter and compare with USB.

Record the controller model and firmware. If firmware is old, check for an update through Xbox Accessories on Windows, then pair again. Old firmware is one possible cause, not a diagnosis based on pairing alone.

The xpadneo build includes the original wrapper's global Bluetooth profile:
ControllerMode=dual, JustWorksRepairing=confirm, LE intervals 7/9 with latency 0,
UserspaceHID=true, ClassicBondedOnly=false and LEAutoSecurity=false.
Existing files are backed up as /etc/bluetooth/main.conf.before-xpadneo and
/etc/bluetooth/input.conf.before-xpadneo before modification. Review these
alongside the current files when comparing Bluetooth behavior. Update repairs
apply the same profile in the new slot. A --no-xpadneo build leaves Bluetooth
configuration alone.

## Package or signature failure

Save the exact package name, URL and error. Check the build host's time, free space and server availability. A signature error is not the same as a missing package or a network timeout.

Do not automatically add `--skip-sigcheck`. The repair path deliberately stops on signature failures. Fix the underlying trust or package availability problem before retrying.

## Pinned package download fails

Temporary network and server failures are retried a limited number of times.
The report distinguishes an HTTP 404/410 from a transport or server failure.
For the pinned NVIDIA packages and egl-wayland2, the downloader can try the
same filename on the Arch archive or its package mirror. It never selects a
different version to complete a repair. Older versions may exist only in the
archive, so a mirror fallback is not guaranteed to succeed.

Downloads are staged in a temporary file and only replace the destination after
a successful, nonempty transfer. Package installation still applies the existing
signature and dependency checks. An unavailable archive index is reported as
unknown availability, not proof that a requested driver version does not exist.

If both sources fail, retain the error and retry later. A failed update repair
does not mark the target slot ready. Do not substitute another kernel's headers
or disable signature checks to resolve a network failure.

## Kernel module build failure

Record the image checksum, target kernel and selected NVIDIA or xpadneo version. Save the compiler output, especially the first actual error. Matching headers and a driver compatible with that kernel are required.

Do not copy a module from another kernel or manually create the completion marker.

## Not enough space

Check both the build workspace and the mounted image. During OS repair, also check `/home`. Having free space on the host does not mean the image's root partition has enough room.

`--trim-cuda` can reduce the driver payload if CUDA, OpenCL and OptiX are not needed. The script does not enlarge partitions. The build checks actual free space after copying and flushing the compressed payload, with a 256 MiB reserve. A copy or space-check failure means the output image is incomplete and must not be used.

If OS repair reaches its final space check, unused Btrfs metadata allocation can
leave little room for files even on a large SSD. The repair helper can compact a
limited number of metadata block groups in the inactive slot, then checks the
same 256 MiB reserve again. It does not delete files, resize partitions or reduce
metadata redundancy. If the reserve is still unavailable, the update stays blocked.
Save the repair log rather than removing the space check or enabling the slot manually.

## Steam login or library disappears

Record whether this happened after a reboot, OS update or USB reinstallation. Check that the intended disk booted. Preserve logs before reinstalling.

Use an image built from the current script if an older installer repeatedly clears Steam data. Updating the installer cannot recover already deleted files. Restore those from a backup.

## A game fails but the desktop works

Test a second game and record the Proton version. Compare a 64-bit game with one using a 32-bit component. If available, `vulkaninfo --summary` provides another graphics check; it is not included in the diagnostic script and may not be installed.

Temporarily test without overlays and record whether MangoHud changes the result.

## Sleep or wake fails

Save a report before sleep and another after wake if the machine remains usable. Record whether the failure concerns video, audio, network or controller reconnect. Note whether the NVIDIA power services are enabled; that alone does not prove suspend support.

If a hard restart was necessary, previous-boot logs may help where persistent journaling is available. Sleep behavior depends on the hardware and driver.

## USB keyboard or controller cannot wake the PC

Check the motherboard's BIOS/UEFI settings before changing Linux configuration. USB wake may be disabled even when sleep and the case power button work normally.

On MSI boards, look under **Settings > Advanced > Wake Up Event Setup > Resume By USB Device** and set it to **Enabled**. Menu names vary by board and firmware. See [MSI's USB power and wake guide](https://us.msi.com/support/technical_details/MB_BIOS_Sleep_Hibernate).

Save the setting, boot SteamOS, suspend and test a wired USB keyboard first. Test pressing a button on an already connected controller separately from connecting a USB device during sleep. These actions may have different hardware support.

Bluetooth controller wake is a separate check. Working USB keyboard wake does not establish Bluetooth wake support; the adapter, controller and their wake settings also matter. The headphone reconnect helper runs after resume and does not wake the PC.

## Bluetooth headphones stay disconnected after wake

The installer includes an audio reconnect helper. It remembers paired, trusted
Bluetooth headphones or speakers connected just before sleep. After wake it waits
for the adapter and makes up to three connection attempts within 45 seconds.
Devices disconnected before sleep and game controllers are not included.
It does not change pairing, restart Bluetooth or connect devices at login.

Keep the headphones powered on and allow a few seconds after wake. If audio does
not return, check the output selected in SteamOS and try connecting manually.
Read the helper log from a terminal in your user session:

```bash
journalctl --user -b -u steamos-nvidia-bluetooth-resume.service --no-pager -n 40
```

To disable the helper for your account:

```bash
systemctl --user mask --now steamos-nvidia-bluetooth-resume.service
```

To restore it:

```bash
systemctl --user unmask steamos-nvidia-bluetooth-resume.service
systemctl --user start steamos-nvidia-bluetooth-resume.service
```

## GPU readings are zero in the performance overlay

The GPU load percentage can work while temperature, clock speed, power and VRAM
remain at zero. Some MangoApp versions keep the sensor selection from startup
when you change the overlay detail level. This does not by itself indicate a
faulty driver. Compare the readings with `nvidia-smi`.

An image built with all components in the [build guide](Build-the-USB-image.md#complete-build) includes the corrected MangoApp. Custom builds include
it with `--mangoapp-dir`. It refreshes the NVIDIA sensor selection
continuously. Voltage and junction temperature are hidden for NVIDIA because
this backend does not provide those readings. Ordinary GPU temperature remains
available. Levels 3 and 4 show each detected model above its GPU or CPU readings.
The GPU capacity is rounded to whole GB for a compact product label; live VRAM
usage is shown separately. Other unsupported sensors, such as CPU power or RAM temperature, may
still be unavailable on a particular computer.

On a build without the correction, select the detailed overlay first, then run
this command from a terminal in your own user session:

```bash
systemctl --user restart gamescope-mangoapp.service
```

This restarts only the performance overlay. Changing its detail level can trigger
the problem again on an uncorrected build. To check which executable is running:

```bash
systemctl --user show gamescope-mangoapp.service -p ExecStart
```

The corrected build uses `/usr/lib/steamos-nvidia/mangoapp`. The original
`/usr/bin/mangoapp` remains installed. Report the executable path, SteamOS and
NVIDIA versions when submitting an overlay issue.

## First setup reports an update download error near completion

An error such as `Unable to download the required update (2)` can also mean that
the final installation step failed after the OS was downloaded and written.
Save the logs before reinstalling or retrying repeatedly:

```bash
sudo journalctl -b -u rauc -u atomupd --no-pager -n 120
sudo tail -n 80 /var/log/steamos-nvidia-repatch.log
```

If the NVIDIA repair log does not exist, the failure may have happened before
that step. Messages about a missing `/efi/SteamOS/partsets/self`, an empty booted
slot or a missing other EFI device identify a boot-partition visibility problem.
They do not indicate that another NVIDIA driver or a larger disk is needed.
Report the exact log and installer version. Do not manually activate the failed
slot; the repair and validation steps must finish first.


If the repair log reports `gamescope/status.txt: FAILED` followed by
`Addon validation failed`, a generated selection status was incorrectly included
in the shipped-file checksum manifest. Downloading again cannot correct this.
Use a corrected installer or have the integration helper and manifest repaired
before retrying. The Gamescope binary must remain covered by checksum validation;
do not disable addon checks or manually activate the failed slot.


## Remote Play client codec setting

On the receiving device, open Steam's **Settings > Remote Play > Advanced Client
Options**, enable **HEVC Video**, then disconnect and reconnect the stream. For
the reverse direction, check the same option on the other receiving device.

The complete installer image has passed a tester's Remote Play check in both
directions with HEVC enabled manually. This setting is not enabled automatically
by the installer. The result does not establish that HEVC is required for every
client or that H.264 cannot work. If HEVC does not resolve the problem, collect
the streaming logs and follow the display and encoder checks below.

## Remote Play connects but shows black video

Check the host's `~/.local/share/Steam/logs/streaming_log.txt` and Game Mode
journal. Record the capture method and encoder. A working local game does not
prove that capture works. Black video can occur with stock Gamescope as well.
An image built with all components in the [build guide](Build-the-USB-image.md#complete-build) includes sampled-image usage for the RGB-to-NV12
conversion alongside the color-layout correction. Outgoing Remote Play has passed
hardware testing with these fixes. If black video returns, collect the logs above.

`NVENC - No CUDA support` alone does not prove that libcuda is missing. Verify
library loading and initialization before adding packages. Steam can fall back
to software encoding while a separate capture problem still produces black video.

## Streamed colors are wrong but the receiver menu is correct

Include the direction of the stream in your report. When another PC sends video
to SteamOS, correct local menu colors with incorrect video colors can indicate a
decoded-frame format problem. Record the codec, hardware-decoding setting and
whether red and blue are reversed. Check the receiving client's
`/tmp/streaming_client.log` and the sending PC's `streaming_log.txt`.

A Gamescope capture patch addresses the sending side; it is not a general fix for
this receiving-side symptom. An image built with all components in the [build guide](Build-the-USB-image.md#complete-build) includes the NVIDIA
decoder correction for the NV12 chroma descriptor. For custom builds, include it
with `--remote-play-dir`; Installer Update does not add it to older systems.
Do not change monitor color calibration to compensate.

## Remote Play uses x264 instead of NVENC on RTX 50

Check whether the host log already says `hardware_enabled=true`. If it then reports
`NVENC - No CUDA support`, turning hardware encoding on again will not resolve the
initialization failure. The Linux Steam host can run its encoder in a 32-bit process.
NVIDIA does not support 32-bit CUDA applications on RTX 50 and newer architectures:
[32-bit CUDA support policy](https://nvidia.custhelp.com/app/answers/detail/a_id/5615).

On the tested RTX 5060, both CUDA libraries load, but 32-bit `cuInit` returns
`CUDA_ERROR_NO_DEVICE` while 64-bit initialization succeeds and detects one GPU.
Installing more copies of the same library does not fix this architecture limit.
This does not mean the GPU lacks NVENC. The Steam host needs a compatible encoding
path; software x264 remains the working fallback in this configuration.

This limitation is separate from capture colors and the 64-bit Remote Play receiver.
Correct receiving colors do not establish hardware encoding on the sending side.

An experimental [VAAPI encoding bridge](https://github.com/elFarto/nvidia-vaapi-driver/pull/427)
can pass frames from a 32-bit Steam host to a 64-bit NVENC helper. In this path,
Steam reports VAAPI HEVC even though the GPU performs NVENC encoding. Confirm both
the helper's encoding log and GPU encoder activity rather than relying on the
Steam label alone. An image built with all components in the [build guide](Build-the-USB-image.md#complete-build) includes the bridge; custom builds
include it with `--nvenc-dir`. Installer Update does not add it to older installations.
Fresh installation followed by the SteamOS 3.8.14 to 3.8.16 update has preserved
its files and automatic startup. HEVC SDR encoding at 1440p has worked on RTX 5060,
including after reboot and after suspend/resume. These results do not establish
compatibility with every GPU or future OS update. The receiver decoder remains separate.

For builds containing the bridge, check the user service with:

```sh
systemctl --user status steamos-nvidia-nvenc.service
journalctl --user -u steamos-nvidia-nvenc.service -b
```

A successful stream should name VAAPI HEVC or H264 in Steam's streaming log, while
the helper reports encoded frames. Early `NVENC - No CUDA support` messages can
still appear before Steam selects the working VAAPI bridge. Do not diagnose the
whole session from those earlier attempts alone.

To temporarily disable the helper for diagnosis, disconnect Remote Play, then run:

```sh
systemctl --user mask --runtime --now steamos-nvidia-nvenc.service
```

Reconnect and check which fallback Steam selects. Restore it with:

```sh
systemctl --user unmask --runtime steamos-nvidia-nvenc.service
systemctl --user start steamos-nvidia-nvenc.service
```

The runtime mask disappears at reboot. These commands apply to the integrated
service, not to earlier manual experiments. The receiver decoder is separate.
