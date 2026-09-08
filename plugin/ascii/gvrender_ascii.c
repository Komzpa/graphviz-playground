/// @file
/// @brief Semantic ASCII line-art renderer.

#include "config.h"

#include <common/types.h>

#include <gvc/gvio.h>
#include <gvc/gvplugin_device.h>
#include <gvc/gvplugin_render.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum {
  CELL_WIDTH = 6,
  CELL_HEIGHT = 12,
};

typedef struct {
  size_t width;
  size_t height;
  char *cells;
} lineart_t;

static lineart_t *lineart(GVJ_t *job) { return job->context; }

static int cell_x(pointf p) { return (int)lround(p.x / CELL_WIDTH); }

static int cell_y(pointf p) { return (int)lround(p.y / CELL_HEIGHT); }

static bool is_line(char c) {
  return c == '-' || c == '|' || c == '/' || c == '\\' || c == '+';
}

static void put_lineart(lineart_t *ctx, int x, int y, char c) {
  if (ctx == NULL || x < 0 || y < 0 || (size_t)x >= ctx->width ||
      (size_t)y >= ctx->height) {
    return;
  }

  char *const destination = &ctx->cells[(size_t)y * ctx->width + (size_t)x];
  if (*destination == ' ' || *destination == c) {
    *destination = c;
  } else if (is_line(*destination) && is_line(c)) {
    *destination = '+';
  }
}

static void put_text(lineart_t *ctx, int x, int y, const char *text) {
  if (ctx == NULL || y < 0 || (size_t)y >= ctx->height || text == NULL) {
    return;
  }
  for (; *text != '\0'; ++text, ++x) {
    if (x < 0 || (size_t)x >= ctx->width) {
      continue;
    }
    const unsigned char c = (unsigned char)*text;
    if (c >= ' ' && c <= '~') {
      ctx->cells[(size_t)y * ctx->width + (size_t)x] = (char)c;
    } else {
      ctx->cells[(size_t)y * ctx->width + (size_t)x] = '?';
    }
  }
}

static void draw_segment(lineart_t *ctx, int x0, int y0, int x1, int y1) {
  const int dx = abs(x1 - x0);
  const int sx = x0 < x1 ? 1 : -1;
  const int dy = -abs(y1 - y0);
  const int sy = y0 < y1 ? 1 : -1;
  const char c = x0 == x1 ? '|' : y0 == y1 ? '-' : (sx == sy ? '\\' : '/');
  int error = dx + dy;

  for (;;) {
    put_lineart(ctx, x0, y0, c);
    if (x0 == x1 && y0 == y1) {
      return;
    }
    const int twice_error = 2 * error;
    if (twice_error >= dy) {
      error += dy;
      x0 += sx;
    }
    if (twice_error <= dx) {
      error += dx;
      y0 += sy;
    }
  }
}

static void draw_polyline(lineart_t *ctx, pointf *points, size_t n,
                          bool close) {
  if (ctx == NULL || points == NULL || n < 2) {
    return;
  }
  for (size_t i = 1; i < n; ++i) {
    draw_segment(ctx, cell_x(points[i - 1]), cell_y(points[i - 1]),
                 cell_x(points[i]), cell_y(points[i]));
  }
  if (close) {
    draw_segment(ctx, cell_x(points[n - 1]), cell_y(points[n - 1]),
                 cell_x(points[0]), cell_y(points[0]));
  }
}

static void lineart_begin_graph(GVJ_t *job) {
  const size_t width = (size_t)ceil(job->width / (double)CELL_WIDTH) + 1;
  const size_t height = (size_t)ceil(job->height / (double)CELL_HEIGHT) + 1;
  lineart_t *const ctx = calloc(1, sizeof(*ctx));
  if (ctx == NULL || width == 0 || height == 0 || width > SIZE_MAX / height) {
    free(ctx);
    agerrorf("unable to allocate ASCII line-art canvas\n");
    return;
  }
  ctx->cells = malloc(width * height);
  if (ctx->cells == NULL) {
    free(ctx);
    agerrorf("unable to allocate ASCII line-art canvas\n");
    return;
  }
  memset(ctx->cells, ' ', width * height);
  ctx->width = width;
  ctx->height = height;
  job->context = ctx;
}

static void lineart_end_graph(GVJ_t *job) {
  lineart_t *const ctx = lineart(job);
  if (ctx == NULL) {
    return;
  }

  size_t top = ctx->height;
  size_t bottom = 0;
  size_t left = ctx->width;
  size_t right = 0;
  for (size_t y = 0; y < ctx->height; ++y) {
    for (size_t x = 0; x < ctx->width; ++x) {
      if (ctx->cells[y * ctx->width + x] != ' ') {
        if (y < top) {
          top = y;
        }
        if (y > bottom) {
          bottom = y;
        }
        if (x < left) {
          left = x;
        }
        if (x > right) {
          right = x;
        }
      }
    }
  }

  if (top < ctx->height) {
    for (size_t y = top; y <= bottom; ++y) {
      for (size_t x = left; x <= right; ++x) {
        gvputc(job, ctx->cells[y * ctx->width + x]);
      }
      gvputc(job, '\n');
    }
  }
  free(ctx->cells);
  free(ctx);
  job->context = NULL;
}

static void lineart_textspan(GVJ_t *job, pointf p, textspan_t *span) {
  const char *const text = span->str;
  const int width = (int)strlen(text);
  int x = cell_x(p) - width / 2;
  if (span->just == 'l') {
    x = cell_x(p);
  } else if (span->just == 'r') {
    x = cell_x(p) - width;
  }
  put_text(lineart(job), x, cell_y(p), text);
}

static void lineart_ellipse(GVJ_t *job, pointf *points, int filled) {
  (void)filled;
  lineart_t *const ctx = lineart(job);
  const int x0 =
      cell_x((pointf){points[0].x - (points[1].x - points[0].x), points[0].y});
  const int x1 =
      cell_x((pointf){points[0].x + (points[1].x - points[0].x), points[0].y});
  const int y0 =
      cell_y((pointf){points[0].x, points[0].y - (points[1].y - points[0].y)});
  const int y1 =
      cell_y((pointf){points[0].x, points[0].y + (points[1].y - points[0].y)});
  const int middle = (y0 + y1) / 2;

  for (int x = x0 + 1; x < x1; ++x) {
    put_lineart(ctx, x, y0, '-');
    put_lineart(ctx, x, y1, '-');
  }
  for (int y = y0 + 1; y < y1; ++y) {
    put_lineart(ctx, x0, y, y == middle ? '(' : '|');
    put_lineart(ctx, x1, y, y == middle ? ')' : '|');
  }
  put_lineart(ctx, x0, y0, '/');
  put_lineart(ctx, x1, y0, '\\');
  put_lineart(ctx, x0, y1, '\\');
  put_lineart(ctx, x1, y1, '/');
}

static void lineart_polygon(GVJ_t *job, pointf *points, size_t n, int filled) {
  lineart_t *const ctx = lineart(job);
  if (job->obj != NULL && job->obj->type == ROOTGRAPH_OBJTYPE) {
    return;
  }
  if (job->obj != NULL && job->obj->type == EDGE_OBJTYPE && filled && n >= 3) {
    size_t tip = 0;
    double greatest_distance = -1;
    for (size_t i = 0; i < n; ++i) {
      double base_x = 0;
      double base_y = 0;
      for (size_t j = 0; j < n; ++j) {
        if (j != i) {
          base_x += points[j].x;
          base_y += points[j].y;
        }
      }
      base_x /= n - 1;
      base_y /= n - 1;
      const double dx = points[i].x - base_x;
      const double dy = points[i].y - base_y;
      const double distance = dx * dx + dy * dy;
      if (distance > greatest_distance) {
        greatest_distance = distance;
        tip = i;
      }
    }

    double base_x = 0;
    double base_y = 0;
    for (size_t i = 0; i < n; ++i) {
      if (i != tip) {
        base_x += points[i].x;
        base_y += points[i].y;
      }
    }
    base_x /= n - 1;
    base_y /= n - 1;
    const double dx = points[tip].x - base_x;
    const double dy = points[tip].y - base_y;
    const char arrow =
        fabs(dx) >= fabs(dy) ? (dx >= 0 ? '>' : '<') : (dy >= 0 ? 'v' : '^');
    put_text(ctx, cell_x(points[tip]), cell_y(points[tip]), (char[]){arrow, 0});
    return;
  }
  draw_polyline(ctx, points, n, true);
}

static pointf cubic(pointf a, pointf b, pointf c, pointf d, double t) {
  const double u = 1.0 - t;
  return (pointf){u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x +
                      t * t * t * d.x,
                  u * u * u * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y +
                      t * t * t * d.y};
}

static void lineart_bezier(GVJ_t *job, pointf *points, size_t n, int filled) {
  (void)filled;
  lineart_t *const ctx = lineart(job);
  if (n < 4) {
    draw_polyline(ctx, points, n, false);
    return;
  }
  for (size_t start = 0; start + 3 < n; start += 3) {
    pointf previous = points[start];
    for (int i = 1; i <= 16; ++i) {
      const pointf next = cubic(points[start], points[start + 1],
                                points[start + 2], points[start + 3], i / 16.0);
      draw_segment(ctx, cell_x(previous), cell_y(previous), cell_x(next),
                   cell_y(next));
      previous = next;
    }
  }
}

static void lineart_polyline(GVJ_t *job, pointf *points, size_t n) {
  draw_polyline(lineart(job), points, n, false);
}

static gvrender_engine_t engine = {
    .begin_graph = lineart_begin_graph,
    .end_graph = lineart_end_graph,
    .textspan = lineart_textspan,
    .ellipse = lineart_ellipse,
    .polygon = lineart_polygon,
    .beziercurve = lineart_bezier,
    .polyline = lineart_polyline,
};

static gvrender_features_t render_features = {
    .flags = GVRENDER_Y_GOES_DOWN,
    .default_pad = 4,
    .color_type = COLOR_STRING,
};

static gvdevice_features_t device_features = {
    .default_dpi = {72, 72},
};

gvplugin_installed_t gvrender_ascii_lineart_types[] = {
    {0, "lineart", 0, &engine, &render_features},
    {0},
};

gvplugin_installed_t gvdevice_ascii_lineart_types[] = {
    {0, "ascii:lineart", 0, NULL, &device_features},
    {0},
};
