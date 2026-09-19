[Manual home](README.md)

# Diagnostics

## Save a report

Run this in Desktop Mode as the normal desktop user:

```bash
steamos-nvidia-diagnostics > ~/steamos-nvidia-report.txt
```

The command reads system information and saves it locally. It does not change
settings or upload the report. Running it as the desktop user preserves useful
session and audio information. Missing tools or inaccessible logs are listed.

For an update failure, also save:

```bash
sudo cat /var/log/steamos-nvidia-repatch.log > ~/steamos-nvidia-repatch.txt
```

Review logs before sharing them. Remove unrelated private content, but keep the
hardware, version and error details needed to understand the problem.

## Find the installer version

The diagnostic report includes `Installer version` under Original image build.
You can read it directly with:

```bash
grep '^Installer version:' /usr/lib/steamos-nvidia/build-info.txt
```

The Installed integration version section shows tools updated after installation,
where the desktop updater is included. Original image build remains unchanged.

This identifies the installer code used to create the image, not the current
SteamOS or NVIDIA version. An OS update does not turn it into a newer installer
release. When building from source, the repository's `VERSION` file contains
the version; a `-dev` suffix means a development build, not a published release.

Older images may not have this field. Report the image filename and Source commit
from the same file instead. Do not infer an installer version from SteamOS.

## Choose the right report

Use a bug report for a failure, a feature request for an improvement, or a
hardware compatibility report to share results from your PC. Keep separate
problems in separate reports. Mark untested features as Not tested rather than
assuming they work. A hardware report should include both successes and limits.

## What to include with a support request

- CPU, GPU and monitor or TV model.
- HDMI or DisplayPort, adapters, resolution, refresh rate, HDR and VRR state.
- SteamOS version and the image filename used to install it.
- Whether the problem happens from USB, after installation or after an update.
- Steps to reproduce and the exact error.
- Whether a text console works and whether restarting changes anything.
- For controllers: model, Bluetooth or USB connection and which buttons fail.

Attach the report as a file rather than sending many photos of scrolling output.

## Useful checks

```bash
uname -r
nvidia-smi
wpctl status
bluetoothctl list
df -h / /home /tmp
```

`nvidia-smi` checks communication with the GPU; it does not prove that a game or
HDR works. `wpctl status` lists audio devices and routes. `bluetoothctl list`
lists Bluetooth adapters.

The report includes original build information and the versions installed during
update repair. It also lists saved HDR profiles; check the monitor's information
screen separately for the signal actually being displayed.


## Update and addon checks

The report includes the booted slot, root filesystem source, installed NVIDIA
package, module version on disk and loaded module version. Differences can help
identify a pending restart or an incomplete driver repair.

Addon checks compare the shipped HDR initializer, Safe Graphics, Bluetooth audio
helper and service files with their recorded checksums. They also check the
required command and activation links. A failed check identifies an installation
problem; passing checks do not prove that a display or audio device works.
User HDR choices and the optional Safe Graphics setting are not overwritten.

The completion marker records a successful slot repair. Its presence alone does
not prove that the current boot or an application is healthy. Effective user
service definitions are included to help spot local overrides.


## NVIDIA library compatibility

Builds and update repairs ask the target system's dynamic loaders to resolve
NVIDIA OpenGL/EGL dependencies for both 64-bit and 32-bit applications, plus the
64-bit management library. Missing libraries and unavailable symbol versions,
including glibc version requirements, stop completion with the loader's error.
The updater checks the repaired slot before marking it ready.

The last result is saved in `/usr/lib/steamos-nvidia/userspace-check.txt` and
included in diagnostics. Its timestamp describes the build or repair, not a new
live test. It does not exercise GPU rendering, Proton or game-specific libraries.
