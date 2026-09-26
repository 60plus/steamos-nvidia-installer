# Receiver patches

Experimental receiver corrections for nvidia-vaapi-driver revision
`a03711106b5e297a64a704c876aeb776cbce957b` from
https://github.com/elFarto/nvidia-vaapi-driver (MIT). That revision is upstream's
tip as of 2026-09-26, so neither patch is a backport of an upstream commit.

## 0001-nv12-chroma-descriptor.patch

The direct backend reports NV12 surfaces but describes their chroma layer as
DRM_FORMAT_RG88. Steam's receiving client maps that layout to SDL_PIXELFORMAT_NV21,
which reverses U/V and produces incorrect colors. This patch changes the exported
chroma-layer descriptor to DRM_FORMAT_GR88 for NV12 only. It leaves the pixel data,
allocation, offsets, pitches and modifiers unchanged. Both single-object and
per-plane descriptor paths receive the same correction; only the single-object
path has been tested on hardware. Other pixel formats are unchanged.

A live test on RTX 5060 with NVIDIA 610.57.04 changed the receiving SDL texture
from NV21 to NV12; the tester confirmed correct colors. This does not establish
compatibility with all VAAPI consumers, GPUs or HDR streams.

The tested receiver also used single-buffer export (`NVD_SINGLE_BUFFER=1`) and
an SDR display-detection workaround. The patch alone does not solve those separate
requirements. The optional `--remote-play-dir` artifact integrates this patch into the image
and A/B repair hooks. The receiver environment helper leaves Steam client files
untouched. Fresh-image acceptance is separate from the live decoder test.

## 0002-release-unresolved-surfaces.patch

A surface stays marked as resolving after the thread that would clear the mark is
gone, and the wait for it has no timeout, so the calling thread blocks for good.
`resolveSurfaces` leaves on `ctx->exiting` without draining its queue,
`nvBeginPicture` marks a surface that `nvEndPicture` may never queue, and a
VideoProc context marks its target with no resolve thread running at all. Because
a surface outlives the context that produced it, the next `vaExportSurfaceHandle`
or `vaSyncSurface` on such a surface never returns.

Measured on 2026-09-26 on RTX 5060 with NVIDIA 610.57.04. A 2560x1440 Remote Play
session ran over a wireless link whose PCIe root port logged 5820 correctable
errors, and the client held four CUDA event handler threads, consistent with the
decoder having been recreated several times during the session. When the session
ended, `streaming_client`'s main thread sat in `pthread_cond_wait` under
`nvExportSurfaceHandle` at no CPU, ignored SIGTERM and only went away on SIGKILL.
Gamescope kept the client's last black frame on screen for as long as the window
existed, which reads to the user as the machine hanging after leaving a game.

The patch makes `resolveSurfaces` release every surface it abandons, so an
ordinary teardown wakes all waiters at once, and bounds both waits in
`waitSurfaceResolved` against one shared deadline of two seconds. On expiry it
logs which surface it gave up on and continues. The paths no drain can reach
therefore degrade to one stale frame instead of a process that cannot exit.

This trades a frame that may be stale for a process that stays killable. It has
not been measured on hardware yet, and it does not address why the link reset the
decoder more than once in one session.
