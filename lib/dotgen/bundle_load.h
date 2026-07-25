/// @file
/// @brief Flow-conserving load carried by a dot bundle segment

#pragma once

#include <stdint.h>

struct Agedge_s;
struct Agraph_s;

typedef uint64_t dot_bundle_id_t;

/// Logical and visual load carried by one segment of a concentrated bundle.
typedef struct {
  dot_bundle_id_t bundle_id;
  dot_bundle_id_t membership_id;
  uint64_t logical_count;
  uint64_t cross_pressure;
  uint64_t position_weight;
  uint64_t visual_lane_count;
  /// Step 1 records abstract lane-width quanta, not physical pen width.
  uint64_t bundle_width;
} dot_bundle_load_t;

typedef enum {
  /// Attach an original edge without adding load already carried by the route.
  DOT_BUNDLE_ALIAS,
  /// Add a rendered-equivalent member without creating another visual lane.
  DOT_BUNDLE_COALESCE,
  /// Add a separately drawable member and its lane-width quantum.
  DOT_BUNDLE_ACCUMULATE,
  /// Add a separately drawable member while preserving legacy route weights.
  DOT_BUNDLE_SHARE_ROUTE,
} dot_bundle_merge_t;

dot_bundle_load_t dot_bundle_load_get(const struct Agedge_s *edge);
void dot_bundle_load_init_original(struct Agedge_s *edge);
void dot_bundle_load_init_virtual(struct Agedge_s *edge,
                                  const struct Agedge_s *original);
void dot_bundle_load_merge(struct Agedge_s *carrier,
                           const struct Agedge_s *member,
                           dot_bundle_merge_t merge);
void dot_bundle_load_adjust_legacy_count(struct Agedge_s *edge, int adjustment);
void dot_bundle_load_set_legacy_count(struct Agedge_s *edge, int count);
void dot_bundle_load_set_legacy_xpenalty(struct Agedge_s *edge, int xpenalty);
void dot_bundle_load_set_legacy_position(struct Agedge_s *edge, int position);
void dot_bundle_load_set_position_scale(struct Agedge_s *edge, uint64_t scale);
void dot_bundle_load_project_legacy(struct Agedge_s *edge);
void dot_bundle_load_dump(struct Agraph_s *graph, const char *phase);
