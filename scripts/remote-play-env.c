/* Receiver-only environment policy. No Steam client files are modified. */
#define _GNU_SOURCE
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

__attribute__((constructor)) static void receiver_environment(void)
{
    char executable[4096];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length <= 0 || length >= (ssize_t)sizeof(executable) - 1)
        return;
    executable[length] = '\0';
    const char *name = strrchr(executable, '/');
    if (!name || strcmp(name + 1, "streaming_client") != 0)
        return;
    const char *disabled = getenv("STEAMOS_NVIDIA_REMOTE_PLAY");
    if (disabled && strcmp(disabled, "0") == 0)
        return;
    /* Steam's receiver otherwise infers HDR from EDID even with HDR output off. */
    setenv("SDL_VIDEO_X11_XRANDR", "0", 1);
    setenv("LIBVA_DRIVERS_PATH", "/usr/lib/steamos-nvidia/remote-play/dri", 1);
    setenv("LIBVA_DRIVER_NAME", "nvidia", 1);
    setenv("NVD_SINGLE_BUFFER", "1", 1);
}
