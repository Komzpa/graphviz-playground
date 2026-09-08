/// @file
/// @brief AA-lib device for raster ASCII output.

#include "config.h"

#include <aalib.h>
#include <assert.h>
#include <gvc/gvplugin.h>
#include <gvc/gvplugin_device.h>
#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <util/gv_math.h>

/// convert an RGB color to grayscale
static int rgb_to_grayscale(unsigned red, unsigned green, unsigned blue) {

  /// use “perceptual” scaling,
  /// https://en.wikipedia.org/wiki/Grayscale#Colorimetric_(perceptual_luminance-preserving)_conversion_to_grayscale

  const double r_linear = red / 255.0;
  const double g_linear = green / 255.0;
  const double b_linear = blue / 255.0;

  const double y_linear =
      0.2126 * r_linear + 0.7152 * g_linear + 0.0722 * b_linear;
  return (int)(y_linear * 255.999);
}

/// does the given range only contain space characters?
static bool is_space(const unsigned char *base, size_t size) {
  assert(base != NULL || size == 0);
  // essentially the inverse of memchr, `memcchr(base, ' ', size)`
  for (size_t i = 0; i < size; ++i) {
    if (base[i] != ' ') {
      return false;
    }
  }
  return true;
}

static void process(GVJ_t *job) {
  assert(job != NULL);

  assert(job->width <= INT_MAX);
  const int width = (int)job->width;
  assert(job->height <= INT_MAX);
  const int height = (int)job->height;

  aa_hardwareparams params = aa_defparams;
  params.width = width;
  params.height = height;
  aa_context *ctx = aa_init(&mem_d, &params, NULL);
  if (ctx == NULL) {
    agerrorf("failed to initialized AA-lib\n");
    return;
  }

  const unsigned char *const data = job->imagedata;
  for (unsigned y = 0; y < job->height; ++y) {
    for (unsigned x = 0; x < job->width; ++x) {
      const unsigned offset =
          y * job->width * BYTES_PER_PIXEL + x * BYTES_PER_PIXEL;
      const unsigned red = data[offset + 2];
      const unsigned green = data[offset + 1];
      const unsigned blue = data[offset];
      aa_putpixel(ctx, (int)x, (int)y, rgb_to_grayscale(red, green, blue));
    }
  }

  aa_fastrender(ctx, 0, 0, width, height);
  aa_flush(ctx);

  const unsigned char *const text = aa_text(ctx);
  const size_t size = job->height * job->width;
  for (size_t y = 0; y < job->height; ++y) {
    if (is_space(&text[y * job->width], size - y * job->width)) {
      break;
    }
    for (size_t x = 0; x < job->width; ++x) {
      if (is_space(&text[y * job->width + x], job->width - x)) {
        break;
      }
      printf("%c", (char)text[y * job->width + x]);
    }
    printf("\n");
  }

  aa_close(ctx);
}

static gvdevice_engine_t engine = {
    .format = process,
};

static gvdevice_features_t device_features = {
    .default_dpi = {96, 96},
};

gvplugin_installed_t gvdevice_ascii_cairo_types[] = {
    {1, "ascii:cairo", 0, &engine, &device_features},
    {0},
};
