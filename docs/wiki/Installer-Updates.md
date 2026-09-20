[Manual home](README.md)

# Update installer tools

**SteamOS NVIDIA Installer Update** updates this project's tools and fixes on an
installed system. It is separate from Steam's OS update and Change NVIDIA Driver.
The selected NVIDIA driver is retained. Changes to image building or disk layout
can still require a new installer; this tool applies the integration files
included in a compatible release package.

The shortcut is included only in builds configured with a signed release source.
If it is missing, updating SteamOS will not add it. Use an installer that includes
the updater or a bootstrap procedure supplied by the maintainer.

## Install an update

1. Finish any pending SteamOS update and reboot first.
2. Open **SteamOS NVIDIA Installer Update** in Desktop Mode.
3. Choose **Check for updates**. Check the source, current version, available
   version and description of changes.
4. Choose **Continue** and enter your normal SteamOS password when requested.
5. Leave the PC powered while it prepares the other OS slot. Copying and checking the system
   can leave the window looking paused for several minutes.
6. After success, choose **Restart** or **Later**. The new version takes effect
   after restarting. Check a game, audio, your controller and sleep/resume.

Preparation normally needs about 17 GiB free on /home. It replaces the inactive
OS slot, including any earlier system stored there. The running slot is retained.
An update does not require writing another USB installer.

Public images use this repository's GitHub Releases and the bundled public
verification key. The updater accepts signed release packages from that source. Stable
sources reject prereleases. An unavailable server does not cause the updater
to switch to another source.

The updater only delivers components included in its signed package. It can
preserve existing Remote Play artifacts, but it does not currently add new
Gamescope, receiver or NVENC binaries to systems that lack them. Use a prepared
image containing those components when they are needed.

## Updating from an older installer

Release packages contain the current set of installer tools, so you can update
from 0.1.0 directly to 0.1.2 without installing 0.1.1 first. Version 0.1.2 includes
the notification workaround. The signed package supports SteamOS 3.8.16; finish
updating SteamOS first if the installed OS is older.

Changes to the complete image builder are available in the source archive. They
do not require rebuilding a working installation just to receive runtime fixes.

## Return to the previous system

Open the tool and choose **Return to previous system**, then restart after it
confirms success. Driver changes and installer updates share the same A/B slots:
the previous system means the one before the most recent completed transaction.
A later OS update can replace that slot, in which case rollback is refused.

This restores the previous system slot. It does not undo changes to games, saves
or other shared home data. Recovery is manual, not automatic after a failed boot.
If Game Mode cannot be displayed, use a text console or SSH:

```bash
sudo steamos-nvidia-installer-update status
sudo steamos-nvidia-installer-update rollback
```

## If preparation fails

Keep the error and read the transaction status. Do not manually mark the target
slot valid. If the terminal closes, preparation can continue as a system service:

```bash
sudo steamos-nvidia-installer-update status
sudo journalctl -u steamos-installer-update --no-pager > ~/installer-update-log.txt
```

A signature or checksum failure stops installation. Check the configured source
and contact the maintainer rather than disabling verification. An unsupported
SteamOS version requires a compatible release.

A changed release after confirmation requires a new check. A pending OS update,
selected slot or another update operation must finish before starting this one.
After an interrupted preparation, reboot into the working system and check status
before retrying. Keep the installer USB available for recovery.

The diagnostic report separates the original image build from the currently
installed integration version. Include both with an update failure report.
