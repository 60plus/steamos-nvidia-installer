#!/usr/bin/env python3
"""Exercise the actual patched AVIF pixel loop on asymmetric RGB test data.

Usage: python3 tools/test-gamescope-capture.py /path/to/patched/gamescope/source
Requires a C++ compiler. Does not require a display or an NVIDIA GPU.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

source = (Path(sys.argv[1]) / 'src/steamcompmgr.cpp').read_text()
start = source.index('const uint32_t uRedShift =')
end = source.index('assert( HAVE_AVIF );', start)
loop = source[start:end]
# Compile the pixel conversion directly from the backported source, including
# row-pitch addressing. Test both layouts, alpha bits and padded rows.
program = r'''
#include <cstdint>
#include <vector>
#include <cassert>
constexpr int VK_FORMAT_A2R10G10B10_UNORM_PACK32=0;
constexpr int VK_FORMAT_A2B10G10R10_UNORM_PACK32=1;
struct Texture { int layout; int format(){return layout;} unsigned rowPitch(){return 32;} };
int main(){
 for(int fmt: {0,1}) {
  Texture tex{fmt}; auto *pScreenshotTexture=&tex;
  constexpr uint32_t g_nOutputWidth=3, g_nOutputHeight=2, kCompCnt=3;
  std::vector<uint32_t> pixels(16,0xdeadbeef);
  auto *mappedData=reinterpret_cast<uint8_t*>(pixels.data());
  std::vector<uint16_t> imageData(g_nOutputWidth*g_nOutputHeight*kCompCnt);
  const uint16_t expected[6][3]={{1023,0,0},{0,1023,0},{0,0,1023},{17,511,999},{0,0,0},{1023,1023,1023}};
  for(unsigned i=0;i<6;i++) {
   auto *v=expected[i];
   pixels[(i/3)*8+i%3]=0xc0000000u | (fmt==0 ? (uint32_t(v[0])<<20)|v[2] : (uint32_t(v[2])<<20)|v[0]) | (uint32_t(v[1])<<10);
  }
''' + loop + r'''
  for(unsigned i=0;i<6;i++) for(unsigned c=0;c<3;c++) assert(imageData[i*3+c]==expected[i][c]);
 }
}
'''
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root / 'test.cpp').write_text(program)
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-Wall', '-Wextra', '-Werror', str(root / 'test.cpp'), '-o', str(root / 'test')], check=True)
    subprocess.run([str(root / 'test')], check=True)
print('PASS: XRGB/XBGR color decoding, RGB primaries, mixed channels, alpha and padded rows')