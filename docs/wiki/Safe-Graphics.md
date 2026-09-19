[Manual home](README.md)

# Safe Graphics

Safe Graphics is an optional recovery session for Game Mode display problems.
It is off by default. It is included only in images built with the recovery
helper. If the command is missing, use a newer installer image.

Keep your working installer USB. Safe Graphics must be selected manually; it is
not an automatic rollback or a boot-menu entry.

## Enable

Connect one monitor directly to the NVIDIA card. From Desktop Mode, a text console
or SSH, run as `deck`, without sudo:

```bash
steamos-nvidia-safe-graphics check
steamos-nvidia-safe-graphics on
```

`check` reads the advertised display modes and validates the session script without
changing settings. Save your game, then reboot or restart Game Mode. The command
does not interrupt the running session. A matching Enable Safe Graphics entry is
available in Desktop Mode's application menu.

Recovery requests a supported progressive mode near 60 Hz: 1920x1080 first,
then 1280x720, 1024x768, 800x600 or 640x480. It refuses to guess if no candidate
is advertised or more than one screen is connected. The monitor's information
screen is the final check of the actual resolution and refresh rate.

The recovery session disables HDR and VRR advertising after Valve's environment
setup, removes the usual output preference and forces composition. The actual
HDR/VRR state should be checked in the monitor's information screen. This is
a troubleshooting option, not a fix for every black or green screen.

## Return to normal

```bash
steamos-nvidia-safe-graphics off
```

Save your work and restart Game Mode or reboot. With recovery off, the launcher
executes Valve's unchanged session. The helper does not rewrite Steam preferences
or remove your display profiles. Check those settings afterward because Steam
itself can save settings while using the recovery session.

## Check status or recover from an error

```bash
steamos-nvidia-safe-graphics status
```

This reports the selection for the next Game Mode start, not the live signal.
If the recovery script rejects an unsupported session or display, use `off` from
a text console or SSH and restart. The selection is a per-user file at
`~/.config/steamos-nvidia/safe-graphics`; removing that file also selects normal
startup. It remains across reboots until turned off.

If no console or SSH is available, boot the saved USB to back up files and reach
the recovery desktop. See [Updates and recovery](Updates-and-recovery.md).
