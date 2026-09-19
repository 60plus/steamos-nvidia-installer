# NV12 chroma export descriptor

Experimental receiver correction for nvidia-vaapi-driver revision
`a03711106b5e297a64a704c876aeb776cbce957b` from
https://github.com/elFarto/nvidia-vaapi-driver (MIT).

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
