/*
 * Legacy xdot_stats declaration copied from the public header before text
 * rotation support. Keep this independent from xdot.h so this is a real ABI
 * canary: if statXDot writes beyond the old public struct, canary is changed.
 */

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

typedef struct xdot_s xdot;

typedef struct {
  size_t cnt;
  size_t n_ellipse;
  size_t n_polygon;
  size_t n_polygon_pts;
  size_t n_polyline;
  size_t n_polyline_pts;
  size_t n_bezier;
  size_t n_bezier_pts;
  size_t n_text;
  size_t n_font;
  size_t n_style;
  size_t n_color;
  size_t n_image;
  size_t n_gradcolor;
  size_t n_fontchar;
} xdot_stats;

extern xdot *parseXDot(char *);
extern void freeXDot(xdot *);
extern int statXDot(xdot *, xdot_stats *);

int main(void) {
  char input[] = "R 45 T 1 2 0 10 4 -text";
  xdot *parsed = parseXDot(input);
  if (parsed == NULL) {
    return EXIT_FAILURE;
  }

  struct {
    xdot_stats stats;
    uint64_t canary;
  } result = {.canary = UINT64_C(0x6a09e667f3bcc909)};

  const int rc = statXDot(parsed, &result.stats);
  freeXDot(parsed);
  return rc != 0 || result.stats.n_text != 1 ||
                 result.canary != UINT64_C(0x6a09e667f3bcc909)
             ? EXIT_FAILURE
             : EXIT_SUCCESS;
}
