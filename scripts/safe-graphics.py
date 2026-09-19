#!/usr/bin/python3
"""Select a reversible graphics recovery session without editing Steam preferences."""
import ctypes as C
import os
from pathlib import Path
import re
import sys
import tempfile

STOCK = Path('/usr/lib/steamos/gamescope-session')
MODE = Path.home() / '.config/steamos-nvidia/safe-graphics'
VARIABLES = ('STEAM_GAMESCOPE_HDR_SUPPORTED', 'STEAM_GAMESCOPE_VRR_SUPPORTED',
             'STEAM_GAMESCOPE_COLOR_MANAGED', 'STEAM_GAMESCOPE_VIRTUAL_WHITE',
             'STEAM_GAMESCOPE_FORCE_HDR_DEFAULT',
             'STEAM_GAMESCOPE_FORCE_OUTPUT_TO_HDR10PQ_DEFAULT')


class ModeInfo(C.Structure):
    _fields_ = [('clock', C.c_uint32), ('hdisplay', C.c_uint16),
                ('hsync_start', C.c_uint16), ('hsync_end', C.c_uint16),
                ('htotal', C.c_uint16), ('hskew', C.c_uint16),
                ('vdisplay', C.c_uint16), ('vsync_start', C.c_uint16),
                ('vsync_end', C.c_uint16), ('vtotal', C.c_uint16),
                ('vscan', C.c_uint16), ('vrefresh', C.c_uint32),
                ('flags', C.c_uint32), ('type', C.c_uint32),
                ('name', C.c_char * 32)]


class Resources(C.Structure):
    _fields_ = [('count_fbs', C.c_int), ('fbs', C.POINTER(C.c_uint32)),
                ('count_crtcs', C.c_int), ('crtcs', C.POINTER(C.c_uint32)),
                ('count_connectors', C.c_int), ('connectors', C.POINTER(C.c_uint32)),
                ('count_encoders', C.c_int), ('encoders', C.POINTER(C.c_uint32)),
                ('min_width', C.c_uint32), ('max_width', C.c_uint32),
                ('min_height', C.c_uint32), ('max_height', C.c_uint32)]


class Connector(C.Structure):
    _fields_ = [('connector_id', C.c_uint32), ('encoder_id', C.c_uint32),
                ('connector_type', C.c_uint32), ('connector_type_id', C.c_uint32),
                ('connection', C.c_int), ('mmWidth', C.c_uint32),
                ('mmHeight', C.c_uint32), ('subpixel', C.c_int),
                ('count_modes', C.c_int), ('modes', C.POINTER(ModeInfo)),
                ('count_props', C.c_int), ('props', C.POINTER(C.c_uint32)),
                ('prop_values', C.POINTER(C.c_uint64)),
                ('count_encoders', C.c_int), ('encoders', C.POINTER(C.c_uint32))]


def display_modes():
    """Read kernel-advertised modes. This never requests DRM master or sets a mode."""
    drm = C.CDLL('libdrm.so.2')
    drm.drmModeGetResources.argtypes = [C.c_int]
    drm.drmModeGetResources.restype = C.POINTER(Resources)
    drm.drmModeGetConnector.argtypes = [C.c_int, C.c_uint32]
    drm.drmModeGetConnector.restype = C.POINTER(Connector)
    drm.drmModeFreeResources.argtypes = [C.POINTER(Resources)]
    drm.drmModeFreeConnector.argtypes = [C.POINTER(Connector)]
    drm.drmModeGetConnectorTypeName.argtypes = [C.c_uint32]
    drm.drmModeGetConnectorTypeName.restype = C.c_char_p
    displays = []
    for card in sorted(Path('/dev/dri').glob('card[0-9]*')):
        try:
            fd = os.open(card, os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            res = drm.drmModeGetResources(fd)
            if not res:
                continue
            try:
                for i in range(res.contents.count_connectors):
                    conn = drm.drmModeGetConnector(fd, res.contents.connectors[i])
                    if not conn:
                        continue
                    try:
                        c = conn.contents
                        if c.connection != 1:
                            continue
                        kind = drm.drmModeGetConnectorTypeName(c.connector_type)
                        if not kind:
                            continue
                        name = kind.decode('ascii') + '-' + str(c.connector_type_id)
                        modes = []
                        for j in range(c.count_modes):
                            m = c.modes[j]
                            # Interlaced and doublescan modes are not recovery candidates.
                            if not m.htotal or not m.vtotal or m.flags & ((1 << 4) | (1 << 5)):
                                continue
                            hz = m.clock * 1000 / m.htotal / m.vtotal / max(m.vscan, 1)
                            modes.append((m.hdisplay, m.vdisplay, hz))
                        displays.append((name, modes))
                    finally:
                        drm.drmModeFreeConnector(conn)
            finally:
                drm.drmModeFreeResources(res)
        finally:
            os.close(fd)
    return displays


def choose_mode(displays):
    if len(displays) != 1:
        raise ValueError('Connect exactly one monitor directly to the NVIDIA card for recovery.')
    name, modes = displays[0]
    for width, height in [(1920, 1080), (1280, 720), (1024, 768), (800, 600), (640, 480)]:
        if any(w == width and h == height and 59 <= hz <= 61 for w, h, hz in modes):
            return name, width, height
    raise ValueError('No supported recovery mode near 60 Hz. Use Desktop Mode or the recovery USB.')


def safe_session(source, output):
    marker = 'exec gamescope \\\n'
    if source.count(marker) != 1:
        raise ValueError('Unrecognized Valve session launcher; recovery was not applied.')
    # Refuse a changed upstream command instead of letting later flags override recovery.
    tail = source.split(marker, 1)[1]
    if re.search(r'(^|\s)(-[WHr]|--output-width|--output-height|--nested-refresh|--hdr\S*|--adaptive-sync|--prefer-output)(\s|=|$)', tail):
        raise ValueError('Conflicting options in Valve session; recovery needs review.')
    if source.count("-O '*',eDP-1") != 1:
        raise ValueError('Unrecognized connector selection in Valve session.')
    connector, width, height = output
    if not re.fullmatch(r'[A-Za-z0-9-]+', connector):
        raise ValueError('Invalid connector name')
    for var in VARIABLES:
        source = re.sub(r'(?m)^(\s*export ' + var + r'=)[^\n]*$', r'\g<1>0', source)
    # This is after Valve's exports and before it publishes the environment to Steam.
    boundary = '# Spawned in parallel to read values from gamescope'
    if source.count(boundary) != 1:
        raise ValueError('Unrecognized session environment handoff.')
    exports = ''.join('export ' + var + '=0\n' for var in VARIABLES)
    source = source.replace(boundary, exports + boundary)
    source = source.replace("-O '*',eDP-1", '-O ' + connector)
    source = source.replace(marker, f'exec gamescope --force-composition -W {width} -H {height} -r 60 \\\n')
    return source


def set_mode(enabled):
    if enabled:
        MODE.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=MODE.parent, prefix='.safe-graphics-')
        try:
            with os.fdopen(fd, 'w') as f:
                f.write('1\n')
            os.replace(name, MODE)
        finally:
            Path(name).unlink(missing_ok=True)
    else:
        MODE.unlink(missing_ok=True)


def main():
    command = sys.argv[1] if len(sys.argv) == 2 else ''
    if command in ('on', 'off'):
        if os.geteuid() == 0:
            raise ValueError('Run as the desktop user without sudo.')
        set_mode(command == 'on')
        print('Recovery ' + ('enabled' if command == 'on' else 'disabled') + '. Save your game, then restart Game Mode or reboot.')
    elif command == 'status':
        print('Recovery: ' + ('enabled for next Game Mode start' if MODE.exists() else 'off'))
    elif command in ('check', 'launch'):
        if command == 'launch' and not MODE.exists():
            os.execv(str(STOCK), [str(STOCK)])
        selected = choose_mode(display_modes())
        source = safe_session(STOCK.read_text(), selected)
        print(f'Recovery requests {selected[0]} {selected[1]}x{selected[2]} near 60 Hz, SDR, VRR off, composition on.', flush=True)
        if command == 'check':
            return
        runtime = Path(os.environ['XDG_RUNTIME_DIR'])
        fd, name = tempfile.mkstemp(prefix='nvidia-recovery-', suffix='.sh', dir=runtime)
        with os.fdopen(fd, 'w') as f:
            f.write(source)
        # The private runtime directory is cleared at logout or reboot.
        os.execv('/bin/bash', ['/bin/bash', name])
    else:
        raise ValueError('Usage: steamos-nvidia-safe-graphics on|off|status|check')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print('Safe Graphics: ' + str(error), file=sys.stderr)
        sys.exit(1)
