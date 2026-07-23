/// @file
/// @brief Flow-conserving dot bundle load implementation

#include "config.h"

#include <common/edgeattr.h>
#include <common/render.h>
#include <dotgen/bundle_load.h>
#include <dotgen/dot.h>
#include <inttypes.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static_assert(sizeof(dot_bundle_load_t) ==
                  sizeof(((Agedgeinfo_t *)0)->bundle_load),
              "Agedgeinfo_t bundle storage must match dot_bundle_load_t");

enum {
  LEGACY_COUNT_BIAS,
  LEGACY_XPENALTY_BIAS,
  LEGACY_POSITION_BIAS,
};

dot_bundle_load_t dot_bundle_load_get(const edge_t *edge) {
  dot_bundle_load_t load;
  const Agedgeinfo_t *const info = (const Agedgeinfo_t *)AGDATA(edge);
  memcpy(&load, info->bundle_load, sizeof(load));
  return load;
}

static void set_edge_load(edge_t *edge, const dot_bundle_load_t *load) {
  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  memcpy(info->bundle_load, load, sizeof(*load));
}

static uint64_t mix_id(uint64_t value) {
  value ^= value >> 30;
  value *= UINT64_C(0xbf58476d1ce4e5b9);
  value ^= value >> 27;
  value *= UINT64_C(0x94d049bb133111eb);
  value ^= value >> 31;
  return value == 0 ? UINT64_MAX : value;
}

static uint64_t append_text(uint64_t hash, const char *text) {
  static const uint64_t FNV_PRIME = UINT64_C(1099511628211);
  for (const unsigned char *cursor = (const unsigned char *)text; *cursor;
       cursor++) {
    hash ^= *cursor;
    hash *= FNV_PRIME;
  }
  hash ^= UINT8_MAX;
  return hash * FNV_PRIME;
}

static uint64_t append_attr(uint64_t hash, edge_t *edge, const char *name) {
  const char *const value = agget(edge, (char *)name);
  hash = append_text(hash, name);
  return append_text(hash, value == NULL ? "" : value);
}

static uint64_t append_endpoint_attr(uint64_t hash, edge_t *edge,
                                     const char *tail_name,
                                     const char *head_name, bool reverse) {
  hash = append_attr(hash, edge, reverse ? head_name : tail_name);
  return append_attr(hash, edge, reverse ? tail_name : head_name);
}

static uint64_t original_member_id(edge_t *edge) {
  const char *const tail_name = agnameof(agtail(edge));
  const char *const head_name = agnameof(aghead(edge));
  const bool reverse = strcmp(tail_name, head_name) > 0;
  uint64_t hash = UINT64_C(14695981039346656037);
  hash = append_text(hash, reverse ? head_name : tail_name);
  hash = append_text(hash, reverse ? tail_name : head_name);
  hash = append_attr(hash, edge, "color");
  hash = append_attr(hash, edge, "colorscheme");
  hash = append_attr(hash, edge, "dir");
  hash = append_attr(hash, edge, "fillcolor");
  hash = append_attr(hash, edge, "fontcolor");
  hash = append_attr(hash, edge, "label");
  hash = append_attr(hash, edge, "penwidth");
  hash = append_attr(hash, edge, "style");
  hash = append_attr(hash, edge, "xlabel");
  hash = append_endpoint_attr(hash, edge, "arrowtail", "arrowhead", reverse);
  hash = append_endpoint_attr(hash, edge, "arrowsize", "arrowsize", reverse);
  hash = append_endpoint_attr(hash, edge, "headlabel", "taillabel", reverse);
  hash = append_endpoint_attr(hash, edge, "headport", "tailport", reverse);
  return mix_id(hash);
}

static uint64_t saturated_add(uint64_t left, uint64_t right) {
  return UINT64_MAX - left < right ? UINT64_MAX : left + right;
}

static uint64_t load_with_bias(uint64_t load, int bias) {
  if (bias >= 0) {
    return saturated_add(load, (uint64_t)bias);
  }
  const uint64_t subtract = (uint64_t)(-(int64_t)bias);
  return subtract > load ? 0 : load - subtract;
}

static int adjusted_bias(int bias, int adjustment) {
  if (adjustment > 0 && bias > INT_MAX - adjustment) {
    return INT_MAX;
  }
  if (adjustment < 0 && bias < INT_MIN - adjustment) {
    return INT_MIN;
  }
  return bias + adjustment;
}

static int bounded_legacy_count(int count) {
  return MIN(MAX(count, 0), SHRT_MAX);
}

static void set_legacy_count_bias(Agedgeinfo_t *info, uint64_t logical_count,
                                  int count) {
  const int bounded_count = bounded_legacy_count(count);
  if (logical_count > (uint64_t)INT_MAX) {
    info->bundle_legacy_bias[LEGACY_COUNT_BIAS] =
        bounded_count == 0 ? INT_MIN : 0;
  } else {
    info->bundle_legacy_bias[LEGACY_COUNT_BIAS] =
        bounded_count - (int)logical_count;
  }
}

static void set_legacy_position_bias(Agedgeinfo_t *info,
                                     const dot_bundle_load_t *load,
                                     int position) {
  const uint64_t scale =
      info->bundle_position_scale == 0 ? 1 : info->bundle_position_scale;
  const uint64_t scaled = load->position_weight > UINT64_MAX / scale
                              ? UINT64_MAX
                              : load->position_weight * scale;
  info->bundle_legacy_bias[LEGACY_POSITION_BIAS] =
      position - (int)MIN(scaled, (uint64_t)INT_MAX);
}

static uint64_t projected_position(const dot_bundle_load_t *load,
                                   const Agedgeinfo_t *info) {
  const uint64_t scale =
      info->bundle_position_scale == 0 ? 1 : info->bundle_position_scale;
  const uint64_t scaled = load->position_weight > UINT64_MAX / scale
                              ? UINT64_MAX
                              : load->position_weight * scale;
  return load_with_bias(scaled, info->bundle_legacy_bias[LEGACY_POSITION_BIAS]);
}

static int scaled_bias(int bias, uint64_t old_scale, uint64_t new_scale) {
  if (bias == 0 || old_scale == new_scale) {
    return bias;
  }
  const int64_t scaled = (int64_t)bias * (int64_t)new_scale / (int64_t)old_scale;
  return (int)MIN(MAX(scaled, (int64_t)INT_MIN), (int64_t)INT_MAX);
}

static short projected_count(const dot_bundle_load_t *load,
                             const Agedgeinfo_t *info) {
  const uint64_t count = load_with_bias(
      load->logical_count, info->bundle_legacy_bias[LEGACY_COUNT_BIAS]);
  return (short)MIN(count, (uint64_t)SHRT_MAX);
}

static void refresh_bundle_id(dot_bundle_load_t *load) {
  uint64_t identity = load->membership_id;
  identity ^= mix_id(load->logical_count);
  identity ^= mix_id(load->visual_lane_count) << 1;
  identity ^= mix_id(load->bundle_width) << 7;
  load->bundle_id = mix_id(identity);
}

void dot_bundle_load_init_original(edge_t *edge) {
  const uint64_t member_id = original_member_id(edge);
  dot_bundle_load_t load = {
      .membership_id = member_id,
      .logical_count = (uint64_t)MAX(ED_count(edge), 0),
      .cross_pressure = (uint64_t)MAX(ED_xpenalty(edge), 0),
      .position_weight = (uint64_t)MAX(ED_weight(edge), 0),
      .visual_lane_count = 1,
      .bundle_width = 1,
  };
  refresh_bundle_id(&load);
  set_edge_load(edge, &load);
  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  info->bundle_position_scale = 1;
  info->bundle_capture_legacy_bias = false;
  memset(info->bundle_legacy_bias, 0, sizeof(info->bundle_legacy_bias));
  dot_bundle_load_project_legacy(edge);
}

void dot_bundle_load_init_virtual(edge_t *edge, const edge_t *original) {
  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  if (original == NULL) {
    const dot_bundle_load_t empty = {0};
    set_edge_load(edge, &empty);
    info->bundle_position_scale = 1;
    info->bundle_capture_legacy_bias = true;
    memset(info->bundle_legacy_bias, 0, sizeof(info->bundle_legacy_bias));
    return;
  }
  const dot_bundle_load_t load = dot_bundle_load_get(original);
  set_edge_load(edge, &load);
  const Agedgeinfo_t *const original_info =
      (const Agedgeinfo_t *)AGDATA(original);
  const unsigned char original_scale = original_info->bundle_position_scale;
  info->bundle_position_scale = original_scale == 0 ? 1 : original_scale;
  info->bundle_capture_legacy_bias = original_info->bundle_capture_legacy_bias;
  memcpy(info->bundle_legacy_bias, original_info->bundle_legacy_bias,
         sizeof(info->bundle_legacy_bias));
  dot_bundle_load_project_legacy(edge);
}

void dot_bundle_load_merge(edge_t *carrier, const edge_t *member,
                           dot_bundle_merge_t merge) {
  if (merge == DOT_BUNDLE_ALIAS) {
    return;
  }

  dot_bundle_load_t source = dot_bundle_load_get(member);
  if (source.logical_count == 0 || source.membership_id == 0) {
    edge_t *const legacy_member = (edge_t *)member;
    source = (dot_bundle_load_t){
        .membership_id = original_member_id(legacy_member),
        .logical_count = (uint64_t)MAX(ED_count(member), 0),
        .cross_pressure = (uint64_t)MAX(ED_xpenalty(member), 0),
        .position_weight = (uint64_t)MAX(ED_weight(member), 0),
        .visual_lane_count = 1,
        .bundle_width = 1,
    };
    refresh_bundle_id(&source);
    if (source.logical_count == 0 || source.membership_id == 0) {
      return;
    }
  }
  dot_bundle_load_t destination = dot_bundle_load_get(carrier);
  Agedgeinfo_t *const carrier_info = (Agedgeinfo_t *)AGDATA(carrier);
  const int legacy_count = MAX(ED_count(carrier), 0);
  const int legacy_xpenalty = MAX(ED_xpenalty(carrier), 0);
  const int legacy_position = MAX(ED_weight(carrier), 0);
  const bool preserve_legacy_count =
      carrier_info->bundle_legacy_bias[LEGACY_COUNT_BIAS] != 0;
  if (destination.logical_count == 0 || destination.membership_id == 0) {
    if (carrier_info->bundle_capture_legacy_bias) {
      carrier_info->bundle_legacy_bias[LEGACY_COUNT_BIAS] =
          MAX(ED_count(carrier), 0);
      carrier_info->bundle_legacy_bias[LEGACY_XPENALTY_BIAS] =
          MAX(ED_xpenalty(carrier), 0);
      carrier_info->bundle_legacy_bias[LEGACY_POSITION_BIAS] =
          MAX(ED_weight(carrier), 0);
      carrier_info->bundle_capture_legacy_bias = false;
    }
    destination = source;
  } else {
    /* IDs combine modulo 2^64; unlike loads, they are not counters. */
    destination.membership_id += source.membership_id;
    if (destination.membership_id == 0) {
      destination.membership_id = UINT64_MAX;
    }
    destination.logical_count =
        saturated_add(destination.logical_count, source.logical_count);
    destination.cross_pressure =
        saturated_add(destination.cross_pressure, source.cross_pressure);
    destination.position_weight =
        saturated_add(destination.position_weight, source.position_weight);
    if (merge == DOT_BUNDLE_COALESCE) {
      destination.visual_lane_count =
          MAX(destination.visual_lane_count, source.visual_lane_count);
      destination.bundle_width =
          MAX(destination.bundle_width, source.bundle_width);
    } else {
      destination.visual_lane_count = saturated_add(
          destination.visual_lane_count, source.visual_lane_count);
      destination.bundle_width =
          saturated_add(destination.bundle_width, source.bundle_width);
    }
  }
  refresh_bundle_id(&destination);
  set_edge_load(carrier, &destination);
  if (merge == DOT_BUNDLE_COALESCE) {
    set_legacy_count_bias(carrier_info, destination.logical_count, legacy_count);
    carrier_info->bundle_legacy_bias[LEGACY_XPENALTY_BIAS] =
        legacy_xpenalty - (int)MIN(destination.cross_pressure, (uint64_t)INT_MAX);
    set_legacy_position_bias(carrier_info, &destination, legacy_position);
  } else if (merge == DOT_BUNDLE_SHARE_ROUTE) {
    set_legacy_count_bias(carrier_info, destination.logical_count, legacy_count);
    carrier_info->bundle_legacy_bias[LEGACY_XPENALTY_BIAS] =
        legacy_xpenalty - (int)MIN(destination.cross_pressure, (uint64_t)INT_MAX);
    set_legacy_position_bias(carrier_info, &destination, legacy_position);
  } else if (preserve_legacy_count) {
    set_legacy_count_bias(carrier_info, destination.logical_count,
                          legacy_count);
  }
  dot_bundle_load_project_legacy(carrier);
}

void dot_bundle_load_adjust_legacy_count(edge_t *edge, int adjustment) {
  const dot_bundle_load_t load = dot_bundle_load_get(edge);
  if (load.logical_count == 0 || load.membership_id == 0) {
    ED_count(edge) =
        (short)MIN(MAX((int)ED_count(edge) + adjustment, 0), SHRT_MAX);
    return;
  }

  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  info->bundle_legacy_bias[LEGACY_COUNT_BIAS] =
      adjusted_bias(info->bundle_legacy_bias[LEGACY_COUNT_BIAS], adjustment);
  ED_count(edge) = projected_count(&load, info);
}

void dot_bundle_load_set_legacy_count(edge_t *edge, int count) {
  const dot_bundle_load_t load = dot_bundle_load_get(edge);
  const int bounded_count = bounded_legacy_count(count);
  if (load.logical_count == 0 || load.membership_id == 0) {
    ED_count(edge) = (short)bounded_count;
    return;
  }

  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  set_legacy_count_bias(info, load.logical_count, bounded_count);
  ED_count(edge) = projected_count(&load, info);
}

void dot_bundle_load_set_legacy_xpenalty(edge_t *edge, int xpenalty) {
  const dot_bundle_load_t load = dot_bundle_load_get(edge);
  const int bounded_xpenalty = MIN(MAX(xpenalty, 0), SHRT_MAX);
  if (load.logical_count == 0 || load.membership_id == 0) {
    ED_xpenalty(edge) = (short)bounded_xpenalty;
    return;
  }

  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  info->bundle_legacy_bias[LEGACY_XPENALTY_BIAS] =
      bounded_xpenalty -
      (int)MIN(load.cross_pressure, (uint64_t)INT_MAX);
  ED_xpenalty(edge) = (short)bounded_xpenalty;
}

void dot_bundle_load_set_legacy_position(edge_t *edge, int position) {
  const dot_bundle_load_t load = dot_bundle_load_get(edge);
  const int bounded_position = MIN(MAX(position, 0), INT_MAX);
  if (load.logical_count == 0 || load.membership_id == 0) {
    ED_weight(edge) = bounded_position;
    return;
  }

  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  set_legacy_position_bias(info, &load, bounded_position);
  ED_weight(edge) = (int)MIN(projected_position(&load, info), (uint64_t)INT_MAX);
}

void dot_bundle_load_set_position_scale(edge_t *edge, uint64_t scale) {
  Agedgeinfo_t *const info = (Agedgeinfo_t *)AGDATA(edge);
  const uint64_t old_scale =
      info->bundle_position_scale == 0 ? 1 : info->bundle_position_scale;
  const uint64_t bounded_scale =
      MIN(MAX(scale, UINT64_C(1)), (uint64_t)UCHAR_MAX);
  const dot_bundle_load_t load = dot_bundle_load_get(edge);
  if (load.logical_count == 0 || load.membership_id == 0) {
    const uint64_t current_weight = (uint64_t)MAX(ED_weight(edge), 0);
    ED_weight(edge) = current_weight > (uint64_t)INT_MAX / bounded_scale
                          ? INT_MAX
                          : (int)(current_weight * bounded_scale);
  }
  info->bundle_legacy_bias[LEGACY_POSITION_BIAS] = scaled_bias(
      info->bundle_legacy_bias[LEGACY_POSITION_BIAS], old_scale, bounded_scale);
  info->bundle_position_scale = (unsigned char)bounded_scale;
  dot_bundle_load_project_legacy(edge);
}

void dot_bundle_load_project_legacy(edge_t *edge) {
  const dot_bundle_load_t load = dot_bundle_load_get(edge);
  if (load.logical_count == 0 || load.membership_id == 0) {
    return;
  }
  const Agedgeinfo_t *const info = (const Agedgeinfo_t *)AGDATA(edge);
  const uint64_t xpenalty = load_with_bias(
      load.cross_pressure, info->bundle_legacy_bias[LEGACY_XPENALTY_BIAS]);
  const uint64_t position = projected_position(&load, info);
  if (info->bundle_legacy_bias[LEGACY_COUNT_BIAS] == 0) {
    ED_count(edge) = projected_count(&load, info);
  }
  ED_xpenalty(edge) = (short)MIN(xpenalty, (uint64_t)SHRT_MAX);
  ED_weight(edge) = (int)MIN(position, (uint64_t)INT_MAX);
}

void dot_bundle_load_dump(graph_t *graph, const char *phase) {
  const char *const enabled = getenv("GV_BUNDLE_LOAD_DEBUG");
  if (enabled == NULL || enabled[0] == '\0' || strcmp(enabled, "0") == 0) {
    return;
  }

  for (node_t *node = GD_nlist(graph); node != NULL; node = ND_next(node)) {
    for (size_t index = 0; index < ND_out(node).size; index++) {
      edge_t *const edge = ND_out(node).list[index];
      if (edge == NULL) {
        continue;
      }
      const dot_bundle_load_t load = dot_bundle_load_get(edge);
      if (load.logical_count == 0 || load.membership_id == 0) {
        continue;
      }
      const Agedgeinfo_t *const info = (const Agedgeinfo_t *)AGDATA(edge);
      const uint64_t count = load_with_bias(
          load.logical_count, info->bundle_legacy_bias[LEGACY_COUNT_BIAS]);
      const uint64_t xpenalty = load_with_bias(
          load.cross_pressure, info->bundle_legacy_bias[LEGACY_XPENALTY_BIAS]);
      const uint64_t position = projected_position(&load, info);
      fprintf(stderr,
              "{\"schema\":\"dot-bundle-load-v1\",\"phase\":\"%s\","
              "\"segment_id\":\"%016" PRIx64 ":%d:%d\","
              "\"bundle_id\":\"0x%016" PRIx64 "\","
              "\"membership_id\":\"0x%016" PRIx64 "\","
              "\"tail_rank\":%d,\"head_rank\":%d,"
              "\"logical_count\":%" PRIu64 ","
              "\"cross_pressure\":%" PRIu64 ","
              "\"position_weight\":%" PRIu64 ","
              "\"visual_lane_count\":%" PRIu64 ","
              "\"bundle_width\":%" PRIu64 ","
              "\"legacy_count_bias\":%d,\"legacy_xpenalty_bias\":%d,"
              "\"legacy_position_bias\":%d,"
              "\"legacy_count\":%d,\"legacy_xpenalty\":%d,"
              "\"legacy_position_weight\":%d,"
              "\"count_saturated\":%s,\"xpenalty_saturated\":%s,"
              "\"position_saturated\":%s}\n",
              phase, load.bundle_id, ND_rank(agtail(edge)),
              ND_rank(aghead(edge)), load.bundle_id, load.membership_id,
              ND_rank(agtail(edge)), ND_rank(aghead(edge)), load.logical_count,
              load.cross_pressure, load.position_weight, load.visual_lane_count,
              load.bundle_width, info->bundle_legacy_bias[LEGACY_COUNT_BIAS],
              info->bundle_legacy_bias[LEGACY_XPENALTY_BIAS],
              info->bundle_legacy_bias[LEGACY_POSITION_BIAS], ED_count(edge),
              ED_xpenalty(edge), ED_weight(edge),
              count > (uint64_t)SHRT_MAX ? "true" : "false",
              xpenalty > (uint64_t)SHRT_MAX ? "true" : "false",
              position > (uint64_t)INT_MAX ? "true" : "false");
    }
  }
}
