#!/usr/bin/python3
"""Reconnect trusted audio devices that were connected immediately before sleep."""
import time

DEVICE = 'org.bluez.Device1'
ADAPTER = 'org.bluez.Adapter1'
AUDIO_SINK = '0000110b-0000-1000-8000-00805f9b34fb'


def audio_devices(objects, connected=False):
    found = {}
    for path, interfaces in objects.items():
        device = interfaces.get(DEVICE, {})
        adapter = objects.get(device.get('Adapter'), {}).get(ADAPTER, {})
        if not (device.get('Paired') and device.get('Trusted')) or device.get('Blocked'):
            continue
        if AUDIO_SINK not in [str(u).lower() for u in device.get('UUIDs', [])]:
            continue
        if connected and not device.get('Connected'):
            continue
        if not device.get('Address') or not adapter.get('Address'):
            continue
        key = (str(adapter['Address']), str(device['Address']))
        found[key] = (str(path), bool(device.get('Connected')), bool(adapter.get('Powered')))
    return found


class Recovery:
    def __init__(self):
        self.pending = {}
        self.deadline = 0
        self.generation = 0

    def suspend(self, objects):
        self.generation += 1
        self.pending = {key: 0 for key in audio_devices(objects, connected=True)}
        self.deadline = 0

    def resume(self, now):
        self.deadline = now + 45

    def candidates(self, objects, now):
        if not self.deadline or now >= self.deadline:
            self.pending.clear()
            return []
        current = audio_devices(objects)
        result = []
        for key, attempts in list(self.pending.items()):
            if key not in current:
                continue  # Adapter may still be reappearing after reset.
            path, connected, powered = current[key]
            if connected:
                del self.pending[key]
            elif powered and attempts < 3:
                self.pending[key] += 1
                result.append((key, path))
        return result


def main():
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    state = Recovery()
    objects = {}
    timer = None
    in_flight = set()

    def log(message):
        print(message, flush=True)

    def refresh():
        nonlocal objects
        try:
            manager = dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager')
            objects = manager.GetManagedObjects(timeout=3)
        except dbus.DBusException:
            objects = {}

    def changed(interface, changes, invalidated, path=None):
        props = objects.setdefault(path, {}).setdefault(interface, {})
        props.update(changes)
        for key in invalidated:
            props.pop(key, None)

    def added(path, interfaces):
        objects.setdefault(path, {}).update(interfaces)

    def removed(path, interfaces):
        for interface in interfaces:
            objects.get(path, {}).pop(interface, None)

    def finish(key, generation, error=None):
        if generation != state.generation:
            return
        in_flight.discard(key)
        if error:
            log('Audio reconnect attempt failed: ' + error.get_dbus_name())
        else:
            log('Audio connection request completed.')

    def tick():
        nonlocal timer
        refresh()
        # Do not overlap requests, including when an adapter briefly disappears.
        if not in_flight:
            for key, path in state.candidates(objects, time.monotonic()):
                generation = state.generation
                in_flight.add(key)
                log('Requesting previous audio connection, attempt ' + str(state.pending[key]) + '/3.')
                try:
                    device = dbus.Interface(bus.get_object('org.bluez', path), DEVICE)
                    device.ConnectProfile(AUDIO_SINK,
                        reply_handler=lambda k=key, g=generation: finish(k, g),
                        error_handler=lambda e, k=key, g=generation: finish(k, g, e), timeout=8)
                except dbus.DBusException as error:
                    finish(key, generation, error)
        if time.monotonic() >= state.deadline or not state.pending:
            log('Audio resume recovery finished; remaining: ' + str(len(state.pending)))
            state.pending.clear()
            timer = None
            return False
        return True

    def sleep_signal(sleeping):
        nonlocal timer
        if timer:
            GLib.source_remove(timer)
            timer = None
        if sleeping:
            # The signal cache is captured before BlueZ disconnects for suspend.
            state.suspend(objects)
            in_flight.clear()
            log('Audio devices connected before sleep: ' + str(len(state.pending)))
        else:
            state.resume(time.monotonic())
            if state.pending:
                timer = GLib.timeout_add_seconds(5, tick)
                log('Waiting for Bluetooth to return before reconnecting audio.')

    bus.add_signal_receiver(changed, signal_name='PropertiesChanged',
        dbus_interface='org.freedesktop.DBus.Properties', bus_name='org.bluez', path_keyword='path')
    bus.add_signal_receiver(added, signal_name='InterfacesAdded',
        dbus_interface='org.freedesktop.DBus.ObjectManager', bus_name='org.bluez')
    bus.add_signal_receiver(removed, signal_name='InterfacesRemoved',
        dbus_interface='org.freedesktop.DBus.ObjectManager', bus_name='org.bluez')
    bus.add_signal_receiver(sleep_signal, signal_name='PrepareForSleep',
        dbus_interface='org.freedesktop.login1.Manager', bus_name='org.freedesktop.login1')
    refresh()
    log('Audio resume listener ready. No connection changes at startup.')
    GLib.MainLoop().run()


if __name__ == '__main__':
    main()
