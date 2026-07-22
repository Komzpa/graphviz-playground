/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

/*
 * set edge splines.
 */

#include "config.h"

#include <assert.h>
#include <common/boxes.h>
#include <common/edgeattr.h>
#include <common/geomprocs.h>
#include <common/globals.h>
#include <common/render.h>
#include <common/utils.h>
#include <dotgen/dot.h>
#include <float.h>
#include <math.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <util/agxbuf.h>
#include <util/alloc.h>
#include <util/gv_math.h>
#include <util/list.h>

#ifdef ORTHO
#include <ortho/ortho.h>
#endif

#define NSUB 9 /* number of subdivisions, re-aiming splines */

#define MINW 16 /* minimum width of a box in the edge path */
#define HALFMINW 8

#define MULTIEDGE_NODE_MARGIN 0.5
#define MULTIEDGE_BEZIER_FLATNESS 0.01
#define FLAT_PORT_NORMAL_ARM 36.0
#define NOMINAL_ARROW_LENGTH 10.0

#define FWDEDGE 16
#define BWDEDGE 32

#define MAINGRAPH 64
#define AUXGRAPH 128
#define GRAPHTYPEMASK 192 /* the OR of the above */
#define ENDPOINT_LABEL_GAP 4.0
#define ENDPOINT_LABEL_NODE_MIN 20.0

typedef struct {
  textlabel_t *label;
  edge_t *edge;
  bool head_p;
  bool needs_node_clearance;
} endpoint_label_t;

static bool has_grouped_flat_endpoint(edge_t *);

static void makefwdedge(edge_t *new, edge_t *old) {
  Agedgeinfo_t *const info =
      (Agedgeinfo_t *)((uintptr_t)new->base.data - offsetof(Agedgeinfo_t, hdr));
  const Agedgeinfo_t *const old_info =
      (Agedgeinfo_t *)((uintptr_t)old->base.data - offsetof(Agedgeinfo_t, hdr));
  *info = *old_info;
  *new = *old;
  new->base.data = &info->hdr;
  AGTAIL(new) = AGHEAD(old);
  AGHEAD(new) = AGTAIL(old);
  ED_tail_port(new) = ED_head_port(old);
  ED_head_port(new) = ED_tail_port(old);
  ED_edge_type(new) = VIRTUAL;
  ED_to_orig(new) = old;
}

typedef struct {
  double LeftBound;
  double RightBound;
  double Splinesep;
  double Multisep;
  boxf *Rank_box;
} spline_info_t;

static boxf label_box(const graph_t *graph, const textlabel_t *label) {
  pointf dimen = label->dimen;
  if (GD_flip(graph)) {
    SWAP(&dimen.x, &dimen.y);
  }
  return (boxf){.LL = {.x = label->pos.x - dimen.x / 2.0,
                       .y = label->pos.y - dimen.y / 2.0},
                .UR = {.x = label->pos.x + dimen.x / 2.0,
                       .y = label->pos.y + dimen.y / 2.0}};
}

static boxf node_box(const node_t *node) {
  const pointf coord = ND_coord(node);
  return (boxf){
      .LL = {.x = coord.x - ND_lw(node), .y = coord.y - ND_ht(node) / 2.0},
      .UR = {.x = coord.x + ND_rw(node), .y = coord.y + ND_ht(node) / 2.0}};
}

static bool label_boxes_overlap_or_touch(boxf a, boxf b) {
  return MAX(a.LL.x - ENDPOINT_LABEL_GAP, b.LL.x - ENDPOINT_LABEL_GAP) <=
             MIN(a.UR.x + ENDPOINT_LABEL_GAP, b.UR.x + ENDPOINT_LABEL_GAP) &&
         MAX(a.LL.y - ENDPOINT_LABEL_GAP, b.LL.y - ENDPOINT_LABEL_GAP) <=
             MIN(a.UR.y + ENDPOINT_LABEL_GAP, b.UR.y + ENDPOINT_LABEL_GAP);
}

static bool boxes_overlap(boxf a, boxf b) {
  return MAX(a.LL.x, b.LL.x) <= MIN(a.UR.x, b.UR.x) &&
         MAX(a.LL.y, b.LL.y) <= MIN(a.UR.y, b.UR.y);
}

static bool edge_spline_bounds(const edge_t *edge, boxf *bounds) {
  const splines *const edge_splines = ED_spl(edge);
  if (edge_splines == NULL || edge_splines->size == 0) {
    return false;
  }

  bool found = false;
  for (size_t spline_index = 0; spline_index < edge_splines->size;
       spline_index++) {
    const bezier *const curve = &edge_splines->list[spline_index];
    for (size_t point_index = 0; point_index < curve->size; point_index++) {
      const pointf curve_point = curve->list[point_index];
      if (!found) {
        *bounds = (boxf){.LL = curve_point, .UR = curve_point};
        found = true;
      } else {
        bounds->LL.x = MIN(bounds->LL.x, curve_point.x);
        bounds->LL.y = MIN(bounds->LL.y, curve_point.y);
        bounds->UR.x = MAX(bounds->UR.x, curve_point.x);
        bounds->UR.y = MAX(bounds->UR.y, curve_point.y);
      }
    }
  }
  return found;
}

static double graph_node_right_bound(graph_t *graph) {
  bool found = false;
  double right = 0.0;
  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    const pointf coord = ND_coord(node);
    const double node_right = coord.x + ND_rw(node);
    if (!found || node_right > right) {
      right = node_right;
      found = true;
    }
  }
  return right;
}

static void place_grouped_endpoint_label_outside_node(graph_t *graph,
                                                      edge_t *edge,
                                                      bool head_p) {
  node_t *const endpoint = head_p ? aghead(edge) : agtail(edge);
  node_t *const opposite = head_p ? agtail(edge) : aghead(edge);
  textlabel_t *const label = head_p ? ED_head_label(edge) : ED_tail_label(edge);
  pointf direction = {.x = ND_coord(endpoint).x - ND_coord(opposite).x,
                      .y = ND_coord(endpoint).y - ND_coord(opposite).y};
  const double length = hypot(direction.x, direction.y);
  if (length <= 0.0) {
    return;
  }
  direction.x /= length;
  direction.y /= length;

  pointf dimen = label->dimen;
  if (GD_flip(graph)) {
    SWAP(&dimen.x, &dimen.y);
  }
  const double node_x_extent =
      direction.x < 0.0 ? ND_lw(endpoint) : ND_rw(endpoint);
  const double node_extent = fabs(direction.x) * node_x_extent +
                             fabs(direction.y) * ND_ht(endpoint) / 2.0;
  const double label_extent =
      fabs(direction.x) * dimen.x / 2.0 + fabs(direction.y) * dimen.y / 2.0;
  const double distance = node_extent + label_extent + ENDPOINT_LABEL_GAP;

  label->pos.x = ND_coord(endpoint).x + direction.x * distance;
  label->pos.y = ND_coord(endpoint).y + direction.y * distance;
  if (fabs(direction.x) > 0.01) {
    label->pos.x += copysign(2.0 * ENDPOINT_LABEL_GAP, direction.x);
  }
}

static bool label_overlaps_any_node(graph_t *graph, const textlabel_t *label) {
  const boxf lbl_box = label_box(graph, label);
  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    if (ND_lw(node) + ND_rw(node) < ENDPOINT_LABEL_NODE_MIN ||
        ND_ht(node) < ENDPOINT_LABEL_NODE_MIN)
      continue;
    if (boxes_overlap(lbl_box, node_box(node)))
      return true;
  }
  return false;
}

static bool endpoint_label_uses_default_placement(edge_t *edge) {
  return (E_labelangle == NULL || agxget(edge, E_labelangle)[0] == '\0') &&
         (E_labeldistance == NULL || agxget(edge, E_labeldistance)[0] == '\0');
}

static void place_deduped_self_edge_label_beside_loop(graph_t *graph,
                                                      edge_t *retained,
                                                      edge_t *duplicate) {
  if (agtail(retained) != aghead(retained) ||
      agtail(duplicate) != aghead(duplicate)) {
    return;
  }

  boxf retained_bounds, duplicate_bounds;
  if (!edge_spline_bounds(retained, &retained_bounds)) {
    return;
  }
  if (edge_spline_bounds(duplicate, &duplicate_bounds)) {
    retained_bounds.LL.x = MIN(retained_bounds.LL.x, duplicate_bounds.LL.x);
    retained_bounds.LL.y = MIN(retained_bounds.LL.y, duplicate_bounds.LL.y);
    retained_bounds.UR.x = MAX(retained_bounds.UR.x, duplicate_bounds.UR.x);
    retained_bounds.UR.y = MAX(retained_bounds.UR.y, duplicate_bounds.UR.y);
  }

  textlabel_t *const label = ED_label(retained);
  const double width = GD_flip(graph) ? label->dimen.y : label->dimen.x;
  const double right_bound =
      MAX(retained_bounds.UR.x, graph_node_right_bound(graph));
  label->pos.x = right_bound + ENDPOINT_LABEL_GAP + width / 2.0;
  updateBB(graph, label);
}

static void separate_endpoint_labels(graph_t *graph, endpoint_label_t *labels,
                                     size_t label_count) {
  if (label_count == 0) {
    return;
  }

  for (size_t pass = 0; pass < label_count * 10; pass++) {
    bool moved = false;
    for (size_t i = 0; i < label_count; i++) {
      for (size_t j = i + 1; j < label_count; j++) {
        const boxf first_box = label_box(graph, labels[i].label);
        const boxf second_box = label_box(graph, labels[j].label);
        if (!label_boxes_overlap_or_touch(first_box, second_box)) {
          continue;
        }

        const int x_direction =
            labels[i].label->pos.x <= labels[j].label->pos.x ? -1 : 1;
        const int y_direction =
            labels[i].label->pos.y <= labels[j].label->pos.y ? -1 : 1;
        const double x_push =
            x_direction < 0
                ? (first_box.UR.x + ENDPOINT_LABEL_GAP - second_box.LL.x) / 2.0
                : (second_box.UR.x + ENDPOINT_LABEL_GAP - first_box.LL.x) / 2.0;
        const double y_push =
            y_direction < 0
                ? (first_box.UR.y + ENDPOINT_LABEL_GAP - second_box.LL.y) / 2.0
                : (second_box.UR.y + ENDPOINT_LABEL_GAP - first_box.LL.y) / 2.0;

        if (x_push <= y_push) {
          labels[i].label->pos.x += (double)x_direction * x_push;
          labels[j].label->pos.x -= (double)x_direction * x_push;
        } else {
          labels[i].label->pos.y += (double)y_direction * y_push;
          labels[j].label->pos.y -= (double)y_direction * y_push;
        }
        updateBB(graph, labels[i].label);
        updateBB(graph, labels[j].label);
        moved = true;
      }
    }

    for (size_t i = 0; i < label_count; i++) {
      if (!labels[i].needs_node_clearance)
        continue;

      for (node_t *node = agfstnode(graph); node != NULL;
           node = agnxtnode(graph, node)) {
        const boxf lbl_box = label_box(graph, labels[i].label);
        const boxf n_box = node_box(node);
        if (!label_boxes_overlap_or_touch(lbl_box, n_box))
          continue;

        const int x_direction =
            labels[i].label->pos.x <= ND_coord(node).x ? -1 : 1;
        const int y_direction =
            labels[i].label->pos.y <= ND_coord(node).y ? -1 : 1;
        const double anchor_gap = 2.0 * ENDPOINT_LABEL_GAP;
        const double x_push = x_direction < 0
                                  ? lbl_box.UR.x + anchor_gap - n_box.LL.x
                                  : n_box.UR.x + anchor_gap - lbl_box.LL.x;
        const double y_push = y_direction < 0
                                  ? lbl_box.UR.y + anchor_gap - n_box.LL.y
                                  : n_box.UR.y + anchor_gap - lbl_box.LL.y;

        if (x_push <= y_push) {
          labels[i].label->pos.x += (double)x_direction * x_push;
        } else {
          labels[i].label->pos.y += (double)y_direction * y_push;
        }
        updateBB(graph, labels[i].label);
        moved = true;
      }
    }
    if (!moved) {
      break;
    }
  }
}

static edge_t *getmainedge(edge_t *);
static bool concentrated_label_dedupe_match(edge_t *, edge_t *);
static bool same_self_edge_node_pair(edge_t *, edge_t *);

static void dedupe_concentrated_edge_labels(graph_t *graph) {
  if (!Concentrate) {
    return;
  }

  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    for (edge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {
      textlabel_t *const label = ED_label(edge);
      if (label == NULL || !label->set) {
        continue;
      }
      for (node_t *prior_node = agfstnode(graph); prior_node != NULL;
           prior_node = agnxtnode(graph, prior_node)) {
        for (edge_t *prior_edge = agfstout(graph, prior_node);
             prior_edge != NULL; prior_edge = agnxtout(graph, prior_edge)) {
          if (prior_edge == edge) {
            goto next_edge;
          }
          if (concentrated_label_dedupe_match(edge, prior_edge)) {
            place_deduped_self_edge_label_beside_loop(graph, prior_edge, edge);
            label->set = false;
            goto next_edge;
          }
        }
      }
    next_edge:;
    }
  }
}

typedef LIST(pointf) points_t;

typedef enum {
  ROUTE_JOIN_CONCENTRATED_PIECES,
} route_join_kind_t;

typedef struct {
  node_t *portal;
  pointf router_portal;
  pointf installed_portal;
  pointf tangent;
  boxf local_barrier;
  route_spline_metadata_t route;
  bool constrained;
} route_endpoint_metadata_t;

typedef struct {
  edge_t *edge;
  size_t spline_index;
  route_endpoint_metadata_t start;
  route_endpoint_metadata_t end;
} route_piece_metadata_t;

typedef LIST(route_piece_metadata_t) route_pieces_t;

typedef struct {
  edge_t *edge;
  route_join_kind_t kind;
  pointf joint;
  pointf left_router_portal;
  pointf right_router_portal;
  pointf left_installed_portal;
  pointf right_installed_portal;
  size_t left_spline;
  size_t left_cubic;
  size_t right_spline;
  size_t right_cubic;
  pointf t_ref;
  size_t portal_id;
  node_t *portal;
  boxf local_barriers[2];
  size_t left_piece;
  size_t right_piece;
} route_junction_t;

typedef LIST(route_junction_t) route_junctions_t;

typedef struct {
  pointf point;
  double t;
  double error;
} route_flat_point_t;

typedef LIST(route_flat_point_t) route_flat_points_t;

typedef struct {
  size_t affected_cubic;
  double affected_t;
  size_t other_edge;
  size_t other_spline;
  size_t other_cubic;
  double other_t;
} route_crossing_t;

typedef LIST(route_crossing_t) route_crossings_t;
typedef LIST(int) route_sides_t;

typedef struct {
  size_t size;
  pointf start;
  pointf end;
  uint32_t sflag;
  uint32_t eflag;
  bool suppress_sflag;
  bool suppress_eflag;
  pointf sp;
  pointf ep;
} route_bezier_invariants_t;

typedef enum {
  ROUTE_SEGMENT_DISJOINT,
  ROUTE_SEGMENT_INTERSECTION,
  ROUTE_SEGMENT_AMBIGUOUS,
} route_segment_intersection_t;

#define ROUTE_G1_RESIDUAL_TOLERANCE 1e-9

static void adjustregularpath(path *, size_t, size_t);
static Agedge_t *bot_bound(Agedge_t *, int);
static bool pathscross(Agnode_t *, Agnode_t *, Agedge_t *, Agedge_t *);
static Agraph_t *cl_bound(graph_t *, Agnode_t *, Agnode_t *);
static bool cl_vninside(Agraph_t *, Agnode_t *);
static void completeregularpath(path *, Agedge_t *, Agedge_t *, pathend_t *,
                                pathend_t *, const boxes_t *);
static int edgecmp(const void *, const void *);
static int make_flat_edge(graph_t *, const spline_info_t, path *, Agedge_t **,
                          unsigned, int);
static void make_regular_edge(graph_t *g, spline_info_t *, path *, Agedge_t **,
                              unsigned, int, route_pieces_t *,
                              route_junctions_t *);
static boxf makeregularend(boxf, int, double);
static boxf maximal_bbox(graph_t *g, const spline_info_t, Agnode_t *,
                         Agedge_t *, Agedge_t *);
static Agnode_t *neighbor(graph_t *, Agnode_t *, Agedge_t *, Agedge_t *, int);
static void place_vnlabel(Agnode_t *);
static boxf rank_box(spline_info_t *sp, Agraph_t *, int);
static void recover_slack(Agedge_t *, path *);
static void regularize_straight_bridge(points_t *, size_t *);
static void resize_vn(Agnode_t *, double, double, double);
static void setflags(Agedge_t *, int, int, int);
static int straight_len(Agnode_t *);
static Agedge_t *straight_path(Agedge_t *, int, points_t *, size_t *);
static Agedge_t *top_bound(Agedge_t *, int);
static void align_concentrated_route_tangents(graph_t *, edge_t *);
static void align_arrow_tangents(graph_t *, edge_t *);
static void align_flat_arrow_tangents_in_graph(graph_t *);
static void align_concentrated_junction_trunks(graph_t *);
static void repair_route_junctions(graph_t *, const route_pieces_t *,
                                   const route_junctions_t *);
static bool route_cubic_clears_nodes(graph_t *, edge_t *, const pointf[4]);
static bool route_crossing_signatures_equal(const route_crossings_t *,
                                            const route_crossings_t *);
static bool route_graph_crossing_signature(graph_t *, const route_junction_t *,
                                           route_crossings_t *);

static edge_t *getmainedge(edge_t *e) {
  edge_t *le = e;
  while (ED_to_virt(le))
    le = ED_to_virt(le);
  while (ED_to_orig(le))
    le = ED_to_orig(le);
  return le;
}

static bool edge_has_no_labels(edge_t *edge) {
  return ED_label(edge) == NULL && ED_xlabel(edge) == NULL;
}

static double edge_penwidth(edge_t *edge) {
  if (E_penwidth == NULL)
    return 1.0;
  return late_double(edge, E_penwidth, 1.0, 0.0);
}

static bool concentrated_routes_share_visible_trunk(edge_t *edge,
                                                    edge_t *prior_edge) {
  if (aghead(edge) != aghead(prior_edge))
    return false;

  const splines *const edge_spl = ED_spl(edge);
  const splines *const prior_spl = ED_spl(prior_edge);
  if (edge_spl == NULL || prior_spl == NULL)
    return false;

  const double threshold =
      3.0 * MAX(edge_penwidth(edge), edge_penwidth(prior_edge));
  const double min_shared_trunk = 30.0;
  for (size_t i = 0; i < edge_spl->size; i++) {
    const bezier *const edge_bz = &edge_spl->list[i];
    if (edge_bz->size < 4)
      continue;
    const size_t edge_cubics = (edge_bz->size - 1) / 3;
    for (size_t j = 0; j < prior_spl->size; j++) {
      const bezier *const prior_bz = &prior_spl->list[j];
      if (prior_bz->size < 4)
        continue;
      const size_t prior_cubics = (prior_bz->size - 1) / 3;
      for (size_t a = 0; a < edge_cubics; a++) {
        const pointf *const edge_cubic = &edge_bz->list[3 * a];
        if (DIST(edge_cubic[0], edge_cubic[3]) < min_shared_trunk)
          continue;
        for (size_t b = 0; b < prior_cubics; b++) {
          const pointf *const prior_cubic = &prior_bz->list[3 * b];
          if ((APPROXEQPT(edge_cubic[0], prior_cubic[0], threshold) &&
               APPROXEQPT(edge_cubic[1], prior_cubic[1], threshold) &&
               APPROXEQPT(edge_cubic[2], prior_cubic[2], threshold) &&
               APPROXEQPT(edge_cubic[3], prior_cubic[3], threshold)) ||
              (APPROXEQPT(edge_cubic[0], prior_cubic[3], threshold) &&
               APPROXEQPT(edge_cubic[1], prior_cubic[2], threshold) &&
               APPROXEQPT(edge_cubic[2], prior_cubic[1], threshold) &&
               APPROXEQPT(edge_cubic[3], prior_cubic[0], threshold)))
            return true;
        }
      }
    }
  }
  return false;
}

static bool concentrated_label_dedupe_match(edge_t *edge, edge_t *prior_edge) {
  const textlabel_t *const label = ED_label(edge);
  const textlabel_t *const prior_label = ED_label(prior_edge);
  if (label == NULL || prior_label == NULL || !label->set ||
      !prior_label->set || !gv_edge_attributes_are_equal(prior_edge, edge) ||
      strcmp(label->text, prior_label->text) != 0) {
    return false;
  }
  if (APPROXEQPT(label->pos, prior_label->pos, MILLIPOINT)) {
    return true;
  }
  if (same_self_edge_node_pair(edge, prior_edge) &&
      gv_edge_ports_are_equal(edge, prior_edge)) {
    return true;
  }
  if (aghead(edge) != aghead(prior_edge)) {
    return false;
  }
  if (!concentrated_routes_share_visible_trunk(edge, prior_edge)) {
    return false;
  }

  size_t matching_labels = 0;
  for (edge_t *candidate = agfstin(agraphof(edge), aghead(edge));
       candidate != NULL; candidate = agnxtin(agraphof(edge), candidate)) {
    const textlabel_t *const candidate_label = ED_label(candidate);
    if (candidate_label != NULL &&
        gv_edge_attributes_are_equal(edge, candidate) &&
        strcmp(label->text, candidate_label->text) == 0 &&
        concentrated_routes_share_visible_trunk(edge, candidate)) {
      matching_labels++;
    }
  }
  return matching_labels >= 3;
}

static bool
same_direction_edges_are_concentrated_duplicates(edge_t *retained,
                                                 edge_t *candidate) {
  return retained != candidate && agtail(retained) == agtail(candidate) &&
         aghead(retained) == aghead(candidate) &&
         edge_has_no_labels(retained) && edge_has_no_labels(candidate) &&
         gv_edge_ports_are_equal(retained, candidate) &&
         gv_edge_attributes_are_equal(retained, candidate) &&
         same_direction_edge_arrow_decorations_are_mergeable(retained,
                                                             candidate);
}

static unsigned
suppress_and_compact_concentrated_duplicate_routes(edge_t **edges,
                                                   unsigned cnt) {
  if (!Concentrate) {
    return cnt;
  }

  for (unsigned i = 0; i < cnt; i++) {
    edge_t *const retained_route = edges[i];
    edge_t *const retained = getmainedge(retained_route);
    if (ED_edge_type(retained) == IGNORED) {
      ED_edge_type(retained_route) = IGNORED;
      continue;
    }

    for (unsigned j = i + 1; j < cnt; j++) {
      edge_t *const candidate_route = edges[j];
      edge_t *const candidate = getmainedge(candidate_route);
      if (ED_edge_type(candidate) == IGNORED) {
        ED_edge_type(candidate_route) = IGNORED;
        continue;
      }

      if (same_direction_edges_are_concentrated_duplicates(retained,
                                                           candidate)) {
        fold_concentrated_edge_arrow_decorations(retained, candidate, false);
        ED_edge_type(candidate) = IGNORED;
        ED_edge_type(candidate_route) = IGNORED;
      }
    }
  }

  unsigned kept = 0;
  for (unsigned i = 0; i < cnt; i++) {
    if (ED_edge_type(edges[i]) != IGNORED) {
      edges[kept++] = edges[i];
    }
  }
  return kept;
}

static bool spline_merge(node_t *n) {
  return ND_node_type(n) == VIRTUAL &&
         (ND_in(n).size > 1 || ND_out(n).size > 1);
}

static edge_t *route_spline_owner(edge_t *edge) {
  while (ED_to_orig(edge) != NULL && ED_edge_type(edge) != NORMAL)
    edge = ED_to_orig(edge);
  return edge;
}

static bool
route_spline_metadata_valid(const route_spline_metadata_t *metadata) {
  return metadata->barriers != NULL && metadata->barrier_count >= 3 &&
         metadata->corridor != NULL && metadata->corridor_count > 0 &&
         metadata->portal_count + 1 == metadata->corridor_count &&
         (metadata->portal_count == 0 || metadata->portals != NULL) &&
         metadata->template_points != NULL &&
         metadata->template_point_count >= 2;
}

static void copy_route_spline_metadata(route_spline_metadata_t *destination,
                                       const route_spline_metadata_t *source) {
  *destination = (route_spline_metadata_t){0};
  if (!route_spline_metadata_valid(source))
    return;
  destination->barriers = gv_calloc(source->barrier_count, sizeof(Pedge_t));
  memcpy(destination->barriers, source->barriers,
         source->barrier_count * sizeof(Pedge_t));
  destination->barrier_count = source->barrier_count;
  destination->corridor = gv_calloc(source->corridor_count, sizeof(boxf));
  memcpy(destination->corridor, source->corridor,
         source->corridor_count * sizeof(boxf));
  destination->corridor_count = source->corridor_count;
  if (source->portal_count > 0) {
    destination->portals = gv_calloc(source->portal_count, sizeof(Pedge_t));
    memcpy(destination->portals, source->portals,
           source->portal_count * sizeof(Pedge_t));
    destination->portal_count = source->portal_count;
  }
  destination->template_points =
      gv_calloc(source->template_point_count, sizeof(Ppoint_t));
  memcpy(destination->template_points, source->template_points,
         source->template_point_count * sizeof(Ppoint_t));
  destination->template_point_count = source->template_point_count;
}

static void capture_route_spline_metadata(route_spline_metadata_t *first,
                                          route_spline_metadata_t *last,
                                          route_spline_metadata_t *current) {
  if (!route_spline_metadata_valid(first))
    copy_route_spline_metadata(first, current);
  route_spline_metadata_free(last);
  *last = *current;
  *current = (route_spline_metadata_t){0};
}

static void free_route_piece_metadata(route_pieces_t *pieces) {
  for (size_t i = 0; i < LIST_SIZE(pieces); i++) {
    route_piece_metadata_t *const piece = LIST_AT(pieces, i);
    route_spline_metadata_free(&piece->start.route);
    route_spline_metadata_free(&piece->end.route);
  }
  LIST_FREE(pieces);
}

static const route_piece_metadata_t *
find_route_piece(const route_pieces_t *pieces, edge_t *edge,
                 size_t spline_index, size_t *piece_index) {
  for (size_t i = 0; i < LIST_SIZE(pieces); i++) {
    const route_piece_metadata_t *const piece = LIST_AT(pieces, i);
    if (piece->edge == edge && piece->spline_index == spline_index) {
      *piece_index = i;
      return piece;
    }
  }
  return NULL;
}

static bool route_junction_metadata_valid(const route_junction_t *junction) {
  if (junction->edge == NULL || junction->portal == NULL ||
      junction->kind != ROUTE_JOIN_CONCENTRATED_PIECES)
    return false;
  const splines *const edge_splines = ED_spl(junction->edge);
  if (edge_splines == NULL || junction->left_spline >= edge_splines->size ||
      junction->right_spline >= edge_splines->size ||
      junction->left_spline + 1 != junction->right_spline)
    return false;

  const bezier *const left = &edge_splines->list[junction->left_spline];
  const bezier *const right = &edge_splines->list[junction->right_spline];
  if (left->size < 4 || right->size < 4 || left->size % 3 != 1 ||
      right->size % 3 != 1 || junction->left_cubic != (left->size - 4) / 3 ||
      junction->right_cubic != 0)
    return false;

  const pointf left_joint = left->list[left->size - 1];
  const pointf right_joint = right->list[0];
  if (DIST(left_joint, right_joint) > MILLIPOINT ||
      DIST(left_joint, junction->joint) > MILLIPOINT)
    return false;

  return fabs(hypot(junction->t_ref.x, junction->t_ref.y) - 1.0) <= 1e-6;
}

static void record_route_piece(route_pieces_t *pieces,
                               route_junctions_t *junctions,
                               edge_t *routed_edge, size_t prior_spline_count,
                               route_endpoint_metadata_t start,
                               route_endpoint_metadata_t end, double offset) {
  if (!Concentrate || fabs(offset) > MILLIPOINT ||
      !route_spline_metadata_valid(&start.route) ||
      !route_spline_metadata_valid(&end.route))
    return;
  edge_t *const owner = route_spline_owner(routed_edge);
  splines *const edge_splines = ED_spl(owner);
  if (edge_splines == NULL || edge_splines->size != prior_spline_count + 1)
    return;

  route_spline_metadata_t start_copy;
  route_spline_metadata_t end_copy;
  copy_route_spline_metadata(&start_copy, &start.route);
  copy_route_spline_metadata(&end_copy, &end.route);
  start.route = start_copy;
  end.route = end_copy;
  const route_piece_metadata_t piece = {
      .edge = owner,
      .spline_index = prior_spline_count,
      .start = start,
      .end = end,
  };

  if (prior_spline_count > 0) {
    size_t prior_piece_index = 0;
    const route_piece_metadata_t *const prior = find_route_piece(
        pieces, owner, prior_spline_count - 1, &prior_piece_index);
    bezier *const left = &edge_splines->list[prior_spline_count - 1];
    bezier *const right = &edge_splines->list[prior_spline_count];
    if (prior != NULL && prior->end.portal != NULL &&
        prior->end.portal == start.portal && spline_merge(start.portal) &&
        prior->end.constrained && start.constrained && left->size >= 4 &&
        right->size >= 4 &&
        DIST(left->list[left->size - 1], right->list[0]) <= MILLIPOINT &&
        prior->end.tangent.x * start.tangent.x +
                prior->end.tangent.y * start.tangent.y >
            1.0 - 1e-6) {
      const route_junction_t junction = {
          .edge = owner,
          .kind = ROUTE_JOIN_CONCENTRATED_PIECES,
          .joint = left->list[left->size - 1],
          .left_router_portal = prior->end.router_portal,
          .right_router_portal = start.router_portal,
          .left_installed_portal = prior->end.installed_portal,
          .right_installed_portal = start.installed_portal,
          .left_spline = prior_spline_count - 1,
          .left_cubic = (left->size - 4) / 3,
          .right_spline = prior_spline_count,
          .right_cubic = 0,
          .t_ref = start.tangent,
          .portal_id = (size_t)AGSEQ(start.portal),
          .portal = start.portal,
          .local_barriers = {prior->end.local_barrier, start.local_barrier},
          .left_piece = prior_piece_index,
          .right_piece = LIST_SIZE(pieces),
      };
      if (route_junction_metadata_valid(&junction))
        LIST_APPEND(junctions, junction);
    }
  }

  LIST_APPEND(pieces, piece);
}

static bool has_grouped_flat_endpoint(edge_t *edge) {
  edge = getmainedge(edge);
  const char *const samehead =
      E_samehead != NULL ? agxget(edge, E_samehead) : NULL;
  const char *const sametail =
      E_sametail != NULL ? agxget(edge, E_sametail) : NULL;
  return (samehead != NULL && samehead[0] != '\0') ||
         (sametail != NULL && sametail[0] != '\0');
}

static bool same_self_edge_node_pair(edge_t *edge, edge_t *other) {
  return agtail(edge) == aghead(edge) && agtail(other) == aghead(other) &&
         agtail(edge) == agtail(other);
}

static bool swap_ends_p(edge_t *e) {
  while (ED_to_orig(e))
    e = ED_to_orig(e);
  if (ND_rank(aghead(e)) > ND_rank(agtail(e)))
    return false;
  if (ND_rank(aghead(e)) < ND_rank(agtail(e)))
    return true;
  if (ND_order(aghead(e)) >= ND_order(agtail(e)))
    return false;
  return true;
}

static splineInfo sinfo = {.swapEnds = swap_ends_p,
                           .splineMerge = spline_merge};

int portcmp(port p0, port p1) {
  if (!p1.defined)
    return p0.defined ? 1 : 0;
  if (!p0.defined)
    return -1;
  if (p0.p.x < p1.p.x)
    return -1;
  if (p0.p.x > p1.p.x)
    return 1;
  if (p0.p.y < p1.p.y)
    return -1;
  if (p0.p.y > p1.p.y)
    return 1;
  return 0;
}

static void swap_bezier(bezier *b) {
  const size_t sz = b->size;
  for (size_t i = 0; i < sz / 2; ++i) { // reverse list of points
    SWAP(&b->list[i], &b->list[sz - 1 - i]);
  }

  SWAP(&b->sflag, &b->eflag);
  SWAP(&b->suppress_sflag, &b->suppress_eflag);
  SWAP(&b->sp, &b->ep);
}

static bool has_well_formed_bezier(const bezier *b) {
  return b->list != NULL && b->size >= 4 && (b->size - 1) % 3 == 0;
}

static bool has_well_formed_spline(const splines *s) {
  for (size_t i = 0; i < s->size; ++i) {
    if (!has_well_formed_bezier(&s->list[i])) {
      return false;
    }
  }
  return true;
}

static void swap_spline(splines *s) {
  const size_t sz = s->size;

  // reverse list
  for (size_t i = 0; i < sz / 2; ++i) {
    SWAP(&s->list[i], &s->list[sz - 1 - i]);
  }

  // swap Béziers
  for (size_t i = 0; i < sz; ++i) {
    swap_bezier(&s->list[i]);
  }
}

/* Some back edges are reversed during layout and the reversed edge
 * is used to compute the spline. We would like to guarantee that
 * the order of control points always goes from tail to head, so
 * we reverse them if necessary.
 */
static void edge_normalize(graph_t *g) {
  static atomic_flag warned;

  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (sinfo.swapEnds(e) && ED_spl(e)) {
        if (!has_well_formed_spline(ED_spl(e))) {
          if (!atomic_flag_test_and_set(&warned)) {
            agwarningf("dropping malformed spline\n");
          }
          ED_spl(e) = NULL;
          continue;
        }
        swap_spline(ED_spl(e));
      }
    }
  }
}

/* In position, each node has its rw stored in mval and,
 * if a node is part of a loop, rw may be increased to
 * reflect the loops and associated labels. We restore
 * the original value here.
 */
static void resetRW(graph_t *g) {
  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    if (ND_other(n).list) {
      SWAP(&ND_rw(n), &ND_mval(n));
    }
  }
}

/* Set edge label position information for regular and non-adjacent flat edges.
 * Dot has allocated space and position for these labels. This info will be
 * used when routing orthogonal edges.
 */
static void setEdgeLabelPos(graph_t *g) {
  textlabel_t *l;

  /* place regular edge labels */
  for (node_t *n = GD_nlist(g); n; n = ND_next(n)) {
    if (ND_node_type(n) == VIRTUAL) {
      if (ND_alg(n)) { // label of non-adjacent flat edge
        edge_t *fe = ND_alg(n);
        l = ED_label(fe);
        assert(l);
        l->pos = ND_coord(n);
        l->set = true;
      } else if ((l = ND_label(n))) { // label of regular edge
        place_vnlabel(n);
      }
      if (l)
        updateBB(g, l);
    }
  }
}

/** Main spline routing code.
 * The normalize parameter allows this function to be called by the
 * recursive call in make_flat_edge without normalization occurring,
 * so that the edge will only be normalized once in the top level call
 * of dot_splines.
 *
 * @return 0 on success
 */
static int dot_splines_(graph_t *g, int normalize) {
  int i, j, n_nodes;
  node_t *n;
  Agedgeinfo_t fwdedgeai, fwdedgebi;
  Agedgepair_t fwdedgea, fwdedgeb;
  edge_t *e, *e0, *e1, *ea, *eb, *le1;
  path P = {0};
  int et = EDGE_TYPE(g);
  fwdedgea.out.base.data = &fwdedgeai.hdr;
  fwdedgeb.out.base.data = &fwdedgebi.hdr;

  if (et == EDGETYPE_NONE)
    return 0;
  if (et == EDGETYPE_CURVED) {
    resetRW(g);
    if (GD_has_labels(g->root) & EDGE_LABEL) {
      agwarningf("edge labels with splines=curved not supported in dot - use "
                 "xlabels\n");
    }
  }
  spline_info_t sd = {0};
  LIST(edge_t *) edges = {0};
  route_pieces_t route_pieces = {0};
  route_junctions_t route_junctions = {0};
#ifdef ORTHO
  if (et == EDGETYPE_ORTHO) {
    resetRW(g);
    if (GD_has_labels(g->root) & EDGE_LABEL) {
      setEdgeLabelPos(g);
      const int rc = orthoEdges(g, true);
      if (rc != 0) {
        return rc;
      }
    } else {
      const int rc = orthoEdges(g, false);
      if (rc != 0) {
        return rc;
      }
    }
    goto finish;
  }
#else
  (void)setEdgeLabelPos;
#endif

  mark_lowclusters(g);
  if (routesplinesinit())
    return 0;
  sd = (spline_info_t){.Splinesep = GD_nodesep(g) / 4,
                       .Multisep = GD_nodesep(g)};

  /* compute boundaries and list of splines */
  n_nodes = 0;
  for (i = GD_minrank(g); i <= GD_maxrank(g); i++) {
    n_nodes += GD_rank(g)[i].n;
    if ((n = GD_rank(g)[i].v[0]))
      sd.LeftBound = MIN(sd.LeftBound, ND_coord(n).x - ND_lw(n));
    if (GD_rank(g)[i].n && (n = GD_rank(g)[i].v[GD_rank(g)[i].n - 1]))
      sd.RightBound = MAX(sd.RightBound, ND_coord(n).x + ND_rw(n));
    sd.LeftBound -= MINW;
    sd.RightBound += MINW;

    for (j = 0; j < GD_rank(g)[i].n; j++) {
      n = GD_rank(g)[i].v[j];
      /* if n is the label of a flat edge, copy its position to
       * the label.
       */
      if (ND_alg(n)) {
        edge_t *fe = ND_alg(n);
        assert(ED_label(fe));
        ED_label(fe)->pos = ND_coord(n);
        ED_label(fe)->set = true;
      }
      if (ND_node_type(n) != NORMAL && !sinfo.splineMerge(n))
        continue;
      for (int k = 0; (e = ND_out(n).list[k]); k++) {
        if (ED_edge_type(e) == FLATORDER || ED_edge_type(e) == IGNORED)
          continue;
        setflags(e, REGULAREDGE, FWDEDGE, MAINGRAPH);
        LIST_APPEND(&edges, e);
      }
      if (ND_flat_out(n).list)
        for (int k = 0; (e = ND_flat_out(n).list[k]); k++) {
          setflags(e, FLATEDGE, 0, AUXGRAPH);
          LIST_APPEND(&edges, e);
        }
      if (ND_other(n).list) {
        /* In position, each node has its rw stored in mval and,
         * if a node is part of a loop, rw may be increased to
         * reflect the loops and associated labels. We restore
         * the original value here.
         */
        if (ND_node_type(n) == NORMAL) {
          SWAP(&ND_rw(n), &ND_mval(n));
        }
        for (int k = 0; (e = ND_other(n).list[k]); k++) {
          setflags(e, 0, 0, AUXGRAPH);
          LIST_APPEND(&edges, e);
        }
      }
    }
  }

  /* Sort so that equivalent edges are contiguous.
   * Equivalence should basically mean that 2 edges have the
   * same set {(tailnode,tailport),(headnode,headport)}, or
   * alternatively, the edges would be routed identically if
   * routed separately.
   */
  LIST_SORT(&edges, edgecmp);

  /* FIXME: just how many boxes can there be? */
  P.boxes = gv_calloc(n_nodes + 20 * 2 * NSUB, sizeof(boxf));
  sd.Rank_box = gv_calloc(i, sizeof(boxf));

  if (et == EDGETYPE_LINE) {
    /* place regular edge labels */
    for (n = GD_nlist(g); n; n = ND_next(n)) {
      if (ND_node_type(n) == VIRTUAL && ND_label(n)) {
        place_vnlabel(n);
      }
    }
  }

  for (unsigned l = 0; l < LIST_SIZE(&edges);) {
    const unsigned ind = l;
    edge_t *le0 = getmainedge((e0 = LIST_GET(&edges, l++)));
    if (ED_tail_port(e0).defined || ED_head_port(e0).defined) {
      ea = e0;
    } else {
      ea = le0;
    }
    if (ED_tree_index(ea) & BWDEDGE) {
      makefwdedge(&fwdedgea.out, ea);
      ea = &fwdedgea.out;
    }
    unsigned cnt;
    for (cnt = 1; l < LIST_SIZE(&edges); cnt++, l++) {
      le1 = getmainedge((e1 = LIST_GET(&edges, l)));
      if (le0 != le1 && !same_self_edge_node_pair(le0, le1))
        break;
      if (ED_adjacent(e0))
        continue; /* all flat adjacent edges at once */
      if (ED_tail_port(e1).defined || ED_head_port(e1).defined) {
        eb = e1;
      } else {
        eb = le1;
      }
      if (ED_tree_index(eb) & BWDEDGE) {
        makefwdedge(&fwdedgeb.out, eb);
        eb = &fwdedgeb.out;
      }
      if (!gv_edge_ports_are_equal(ea, eb))
        break;
      if ((ED_tree_index(e0) & EDGETYPEMASK) == FLATEDGE &&
          ED_label(e0) != ED_label(e1))
        break;
      if (ED_tree_index(LIST_GET(&edges, l)) & MAINGRAPH) /* Aha! -C is on */
        break;
    }

    cnt = suppress_and_compact_concentrated_duplicate_routes(
        LIST_AT(&edges, ind), cnt);
    if (cnt == 0)
      continue;

    if (et == EDGETYPE_CURVED) {
      edge_t **edgelist = gv_calloc(cnt, sizeof(edge_t *));
      edgelist[0] = getmainedge(LIST_GET(&edges, ind));
      for (unsigned ii = 1; ii < cnt; ii++)
        edgelist[ii] = LIST_GET(&edges, ind + ii);
      makeStraightEdges(g, edgelist, cnt, et, &sinfo);
      free(edgelist);
    } else if (agtail(e0) == aghead(e0)) {
      double sizey;
      n = agtail(e0);
      const int r = ND_rank(n);
      if (r == GD_maxrank(g)) {
        int adj = r - 1;
        while (adj >= GD_minrank(g) &&
               (GD_rank(g)[adj].n == 0 || GD_rank(g)[adj].v[0] == NULL))
          adj--;
        if (adj >= GD_minrank(g))
          sizey = ND_coord(GD_rank(g)[adj].v[0]).y - ND_coord(n).y;
        else
          sizey = ND_ht(n);
      } else if (r == GD_minrank(g)) {
        int adj = r + 1;
        while (adj <= GD_maxrank(g) &&
               (GD_rank(g)[adj].n == 0 || GD_rank(g)[adj].v[0] == NULL))
          adj++;
        if (adj <= GD_maxrank(g))
          sizey = ND_coord(n).y - ND_coord(GD_rank(g)[adj].v[0]).y;
        else
          sizey = ND_ht(n);
      } else {
        int up = r - 1;
        while (up >= GD_minrank(g) &&
               (GD_rank(g)[up].n == 0 || GD_rank(g)[up].v[0] == NULL))
          up--;
        int down = r + 1;
        while (down <= GD_maxrank(g) &&
               (GD_rank(g)[down].n == 0 || GD_rank(g)[down].v[0] == NULL))
          down++;
        if (up >= GD_minrank(g) && down <= GD_maxrank(g)) {
          double upy = ND_coord(GD_rank(g)[up].v[0]).y - ND_coord(n).y;
          double dwny = ND_coord(n).y - ND_coord(GD_rank(g)[down].v[0]).y;
          sizey = fmin(upy, dwny);
        } else if (up >= GD_minrank(g)) {
          sizey = ND_coord(GD_rank(g)[up].v[0]).y - ND_coord(n).y;
        } else if (down <= GD_maxrank(g)) {
          sizey = ND_coord(n).y - ND_coord(GD_rank(g)[down].v[0]).y;
        } else {
          sizey = ND_ht(n);
        }
      }
      makeSelfEdge(LIST_AT(&edges, ind), cnt, sd.Multisep, sizey / 2, &sinfo);
      for (unsigned b = 0; b < cnt; b++) {
        e = LIST_GET(&edges, ind + b);
        if (ED_label(e))
          updateBB(g, ED_label(e));
      }
    } else if (ND_rank(agtail(e0)) == ND_rank(aghead(e0))) {
      const int rc = make_flat_edge(g, sd, &P, LIST_AT(&edges, ind), cnt, et);
      if (rc != 0) {
        free(sd.Rank_box);
        LIST_FREE(&edges);
        free_route_piece_metadata(&route_pieces);
        LIST_FREE(&route_junctions);
        free(P.boxes);
        return rc;
      }
    } else
      make_regular_edge(g, &sd, &P, LIST_AT(&edges, ind), cnt, et,
                        &route_pieces, &route_junctions);
  }

  /* place regular edge labels */
  for (n = GD_nlist(g); n; n = ND_next(n)) {
    if (ND_node_type(n) == VIRTUAL && ND_label(n)) {
      place_vnlabel(n);
      updateBB(g, ND_label(n));
    }
  }

  /* normalize splines so they always go from tail to head */
  /* place_portlabel relies on this being done first */
  align_concentrated_junction_trunks(g);
  repair_route_junctions(g, &route_pieces, &route_junctions);
  if (normalize)
    edge_normalize(g);

#ifdef ORTHO
finish:
#endif
  align_flat_arrow_tangents_in_graph(g);
  dedupe_concentrated_edge_labels(g);

  /* place port labels */
  /* FIX: head and tail labels are not part of cluster bbox */
  if (E_headlabel || E_taillabel) {
    endpoint_label_t *const endpoint_labels =
        gv_calloc((size_t)agnedges(g) * 2, sizeof(endpoint_label_t));
    size_t endpoint_label_count = 0;

    for (n = agfstnode(g); n; n = agnxtnode(g, n)) {
      if (E_headlabel) {
        for (e = agfstin(g, n); e; e = agnxtin(g, e)) {
          edge_t *const out_edge = AGMKOUT(e);
          if (ED_head_label(out_edge)) {
            if (ED_head_label(out_edge)->set ||
                place_portlabel(out_edge, true)) {
              const bool needs_node_clearance =
                  has_grouped_flat_endpoint(out_edge) &&
                  endpoint_label_uses_default_placement(out_edge) &&
                  label_overlaps_any_node(g, ED_head_label(out_edge));
              if (needs_node_clearance)
                place_grouped_endpoint_label_outside_node(g, out_edge, true);
              updateBB(g, ED_head_label(out_edge));
              endpoint_labels[endpoint_label_count++] = (endpoint_label_t){
                  .label = ED_head_label(out_edge),
                  .edge = out_edge,
                  .head_p = true,
                  .needs_node_clearance = needs_node_clearance};
            }
          }
        }
      }
      if (E_taillabel) {
        for (e = agfstout(g, n); e; e = agnxtout(g, e)) {
          if (ED_tail_label(e)) {
            if (ED_tail_label(e)->set || place_portlabel(e, false)) {
              const bool needs_node_clearance =
                  has_grouped_flat_endpoint(e) &&
                  endpoint_label_uses_default_placement(e) &&
                  label_overlaps_any_node(g, ED_tail_label(e));
              if (needs_node_clearance)
                place_grouped_endpoint_label_outside_node(g, e, false);
              updateBB(g, ED_tail_label(e));
              endpoint_labels[endpoint_label_count++] = (endpoint_label_t){
                  .label = ED_tail_label(e),
                  .edge = e,
                  .head_p = false,
                  .needs_node_clearance = needs_node_clearance};
            }
          }
        }
      }
    }
    for (size_t label_index = 0; label_index < endpoint_label_count;
         label_index++) {
      if (!endpoint_labels[label_index].needs_node_clearance)
        continue;
      node_t *const endpoint = endpoint_labels[label_index].head_p
                                   ? aghead(endpoint_labels[label_index].edge)
                                   : agtail(endpoint_labels[label_index].edge);
      for (size_t sibling_index = 0; sibling_index < endpoint_label_count;
           sibling_index++) {
        if (endpoint_labels[sibling_index].needs_node_clearance)
          continue;
        node_t *const other_endpoint =
            endpoint_labels[sibling_index].head_p
                ? aghead(endpoint_labels[sibling_index].edge)
                : agtail(endpoint_labels[sibling_index].edge);
        if (other_endpoint != endpoint)
          continue;
        endpoint_labels[sibling_index].needs_node_clearance = true;
        place_grouped_endpoint_label_outside_node(
            g, endpoint_labels[sibling_index].edge,
            endpoint_labels[sibling_index].head_p);
        updateBB(g, endpoint_labels[sibling_index].label);
      }
    }
    separate_endpoint_labels(g, endpoint_labels, endpoint_label_count);
    free(endpoint_labels);
  }

#ifdef ORTHO
  if (et != EDGETYPE_ORTHO && et != EDGETYPE_CURVED) {
#else
  if (et != EDGETYPE_CURVED) {
#endif
    routesplinesterm();
  }
  free(sd.Rank_box);
  LIST_FREE(&edges);
  free_route_piece_metadata(&route_pieces);
  LIST_FREE(&route_junctions);
  free(P.boxes);
  State = GVSPLINES;
  EdgeLabelsDone = 1;
  return 0;
}

/* If the splines attribute is defined but equal to "", skip edge routing.
 *
 * @return 0 on success
 */
int dot_splines(graph_t *g) { return dot_splines_(g, 1); }

/* assign position of an edge label from its virtual node
 * This is for regular edges only.
 */
static void place_vnlabel(node_t *n) {
  edge_t *e;
  if (ND_in(n).size == 0)
    return; /* skip flat edge labels here */
  for (e = ND_out(n).list[0]; ED_edge_type(e) != NORMAL; e = ED_to_orig(e))
    ;
  const pointf dimen = ED_label(e)->dimen;
  const double width = GD_flip(agraphof(n)) ? dimen.y : dimen.x;
  ED_label(e)->pos.x = ND_coord(n).x + width / 2.0;
  ED_label(e)->pos.y = ND_coord(n).y;
  ED_label(e)->set = true;
}

static void setflags(edge_t *e, int hint1, int hint2, int f3) {
  int f1, f2;
  if (hint1 != 0)
    f1 = hint1;
  else {
    if (agtail(e) == aghead(e))
      if (ED_tail_port(e).defined || ED_head_port(e).defined)
        f1 = SELFWPEDGE;
      else
        f1 = SELFNPEDGE;
    else if (ND_rank(agtail(e)) == ND_rank(aghead(e)))
      f1 = FLATEDGE;
    else
      f1 = REGULAREDGE;
  }
  if (hint2 != 0)
    f2 = hint2;
  else {
    if (f1 == REGULAREDGE)
      f2 = ND_rank(agtail(e)) < ND_rank(aghead(e)) ? FWDEDGE : BWDEDGE;
    else if (f1 == FLATEDGE)
      f2 = ND_order(agtail(e)) < ND_order(aghead(e)) ? FWDEDGE : BWDEDGE;
    else /* f1 == SELF*EDGE */
      f2 = FWDEDGE;
  }
  ED_tree_index(e) = f1 | f2 | f3;
}

/* lexicographically order edges by
 *  - edge type
 *  - |rank difference of nodes|
 *  - |x difference of nodes|
 *  - id of witness edge for equivalence class
 *  - port comparison
 *  - graph type
 *  - labels if flat edges
 *  - edge id
 */
static int edgecmp(const void *p0, const void *p1) {
  edge_t *const *ptr0 = p0;
  edge_t *const *ptr1 = p1;
  Agedgeinfo_t fwdedgeai, fwdedgebi;
  Agedgepair_t fwdedgea, fwdedgeb;
  int rv;

  fwdedgea.out.base.data = &fwdedgeai.hdr;
  fwdedgeb.out.base.data = &fwdedgebi.hdr;
  edge_t *const e0 = *ptr0;
  edge_t *const e1 = *ptr1;
  int et0 = ED_tree_index(e0) & EDGETYPEMASK;
  int et1 = ED_tree_index(e1) & EDGETYPEMASK;
  if (et0 < et1) {
    return 1;
  }
  if (et0 > et1) {
    return -1;
  }

  edge_t *le0 = getmainedge(e0);
  edge_t *le1 = getmainedge(e1);

  {
    const int rank_diff0 = ND_rank(agtail(le0)) - ND_rank(aghead(le0));
    const int rank_diff1 = ND_rank(agtail(le1)) - ND_rank(aghead(le1));
    const int v0 = abs(rank_diff0);
    const int v1 = abs(rank_diff1);
    if (v0 < v1) {
      return -1;
    }
    if (v0 > v1) {
      return 1;
    }
  }

  {
    const double t0 = ND_coord(agtail(le0)).x - ND_coord(aghead(le0)).x;
    const double t1 = ND_coord(agtail(le1)).x - ND_coord(aghead(le1)).x;
    const double v0 = fabs(t0);
    const double v1 = fabs(t1);
    if (v0 < v1) {
      return -1;
    }
    if (v0 > v1) {
      return 1;
    }
  }

  /* This provides a cheap test for edges having the same set of endpoints.  */
  if (AGSEQ(le0) < AGSEQ(le1)) {
    return -1;
  }
  if (AGSEQ(le0) > AGSEQ(le1)) {
    return 1;
  }

  edge_t *ea =
      (ED_tail_port(e0).defined || ED_head_port(e0).defined) ? e0 : le0;
  if (ED_tree_index(ea) & BWDEDGE) {
    makefwdedge(&fwdedgea.out, ea);
    ea = &fwdedgea.out;
  }
  edge_t *eb =
      (ED_tail_port(e1).defined || ED_head_port(e1).defined) ? e1 : le1;
  if (ED_tree_index(eb) & BWDEDGE) {
    makefwdedge(&fwdedgeb.out, eb);
    eb = &fwdedgeb.out;
  }
  if (!gv_edge_ports_are_equal(ea, eb)) {
    if ((rv = portcmp(ED_tail_port(ea), ED_tail_port(eb))))
      return rv;
    if ((rv = portcmp(ED_head_port(ea), ED_head_port(eb))))
      return rv;
  }

  et0 = ED_tree_index(e0) & GRAPHTYPEMASK;
  et1 = ED_tree_index(e1) & GRAPHTYPEMASK;
  if (et0 < et1) {
    return -1;
  }
  if (et0 > et1) {
    return 1;
  }

  if (et0 == FLATEDGE) {
    if (ED_label(e0) < ED_label(e1)) {
      return -1;
    }
    if (ED_label(e0) > ED_label(e1)) {
      return 1;
    }
  }

  if (AGSEQ(e0) < AGSEQ(e1)) {
    return -1;
  }
  if (AGSEQ(e0) > AGSEQ(e1)) {
    return 1;
  }
  return 0;
}

typedef struct {
  attrsym_t *E_constr;
  attrsym_t *E_dir;
  attrsym_t *E_samehead;
  attrsym_t *E_sametail;
  attrsym_t *E_weight;
  attrsym_t *E_minlen;
  attrsym_t *E_fontcolor;
  attrsym_t *E_fontname;
  attrsym_t *E_fontsize;
  attrsym_t *E_headclip;
  attrsym_t *E_headlabel;
  attrsym_t *E_label;
  attrsym_t *E_label_float;
  attrsym_t *E_labelfontcolor;
  attrsym_t *E_labelfontname;
  attrsym_t *E_labelfontsize;
  attrsym_t *E_tailclip;
  attrsym_t *E_taillabel;
  attrsym_t *E_xlabel;

  attrsym_t *N_height;
  attrsym_t *N_width;
  attrsym_t *N_shape;
  attrsym_t *N_style;
  attrsym_t *N_fontsize;
  attrsym_t *N_fontname;
  attrsym_t *N_fontcolor;
  attrsym_t *N_label;
  attrsym_t *N_xlabel;
  attrsym_t *N_showboxes;
  attrsym_t *N_ordering;
  attrsym_t *N_sides;
  attrsym_t *N_peripheries;
  attrsym_t *N_skew;
  attrsym_t *N_orientation;
  attrsym_t *N_distortion;
  attrsym_t *N_fixed;
  attrsym_t *N_nojustify;
  attrsym_t *N_group;

  attrsym_t *G_ordering;
  int State;
} attr_state_t;

static void setState(graph_t *auxg, attr_state_t *attr_state) {
  /* save state */
  attr_state->E_constr = E_constr;
  attr_state->E_dir = E_dir;
  attr_state->E_samehead = E_samehead;
  attr_state->E_sametail = E_sametail;
  attr_state->E_weight = E_weight;
  attr_state->E_minlen = E_minlen;
  attr_state->E_fontcolor = E_fontcolor;
  attr_state->E_fontname = E_fontname;
  attr_state->E_fontsize = E_fontsize;
  attr_state->E_headclip = E_headclip;
  attr_state->E_headlabel = E_headlabel;
  attr_state->E_label = E_label;
  attr_state->E_label_float = E_label_float;
  attr_state->E_labelfontcolor = E_labelfontcolor;
  attr_state->E_labelfontname = E_labelfontname;
  attr_state->E_labelfontsize = E_labelfontsize;
  attr_state->E_tailclip = E_tailclip;
  attr_state->E_taillabel = E_taillabel;
  attr_state->E_xlabel = E_xlabel;
  attr_state->N_height = N_height;
  attr_state->N_width = N_width;
  attr_state->N_shape = N_shape;
  attr_state->N_style = N_style;
  attr_state->N_fontsize = N_fontsize;
  attr_state->N_fontname = N_fontname;
  attr_state->N_fontcolor = N_fontcolor;
  attr_state->N_label = N_label;
  attr_state->N_xlabel = N_xlabel;
  attr_state->N_showboxes = N_showboxes;
  attr_state->N_ordering = N_ordering;
  attr_state->N_sides = N_sides;
  attr_state->N_peripheries = N_peripheries;
  attr_state->N_skew = N_skew;
  attr_state->N_orientation = N_orientation;
  attr_state->N_distortion = N_distortion;
  attr_state->N_fixed = N_fixed;
  attr_state->N_nojustify = N_nojustify;
  attr_state->N_group = N_group;
  attr_state->State = State;
  attr_state->G_ordering = G_ordering;

  E_constr = NULL;
  E_dir = agattr_text(auxg, AGEDGE, "dir", NULL);
  E_samehead = agattr_text(auxg, AGEDGE, "samehead", NULL);
  E_sametail = agattr_text(auxg, AGEDGE, "sametail", NULL);
  E_weight = agattr_text(auxg, AGEDGE, "weight", NULL);
  if (!E_weight)
    E_weight = agattr_text(auxg, AGEDGE, "weight", "");
  E_minlen = NULL;
  E_fontcolor = NULL;
  E_fontname = agfindedgeattr(auxg, "fontname");
  E_fontsize = agfindedgeattr(auxg, "fontsize");
  E_headclip = agfindedgeattr(auxg, "headclip");
  E_headlabel = NULL;
  E_label = agfindedgeattr(auxg, "label");
  E_label_float = agfindedgeattr(auxg, "label_float");
  E_labelfontcolor = NULL;
  E_labelfontname = agfindedgeattr(auxg, "labelfontname");
  E_labelfontsize = agfindedgeattr(auxg, "labelfontsize");
  E_tailclip = agfindedgeattr(auxg, "tailclip");
  E_taillabel = NULL;
  E_xlabel = NULL;
  N_height = agfindnodeattr(auxg, "height");
  N_width = agfindnodeattr(auxg, "width");
  N_shape = agfindnodeattr(auxg, "shape");
  N_style = NULL;
  N_fontsize = agfindnodeattr(auxg, "fontsize");
  N_fontname = agfindnodeattr(auxg, "fontname");
  N_fontcolor = NULL;
  N_label = agfindnodeattr(auxg, "label");
  N_xlabel = NULL;
  N_showboxes = NULL;
  N_ordering = agfindnodeattr(auxg, "ordering");
  N_sides = agfindnodeattr(auxg, "sides");
  N_peripheries = agfindnodeattr(auxg, "peripheries");
  N_skew = agfindnodeattr(auxg, "skew");
  N_orientation = agfindnodeattr(auxg, "orientation");
  N_distortion = agfindnodeattr(auxg, "distortion");
  N_fixed = agfindnodeattr(auxg, "fixed");
  N_nojustify = NULL;
  N_group = NULL;
  G_ordering = agfindgraphattr(auxg, "ordering");
}

/* Create clone graph. It stores the global Agsyms, to be
 * restored in cleanupCloneGraph. The graph uses the main
 * graph's settings for certain geometry parameters, and
 * declares all node and edge attributes used in the original
 * graph.
 */
static graph_t *cloneGraph(graph_t *g, attr_state_t *attr_state) {
  Agsym_t *sym;
  graph_t *auxg;
  if (agisdirected(g))
    auxg = agopen("auxg", Agdirected, NULL);
  else
    auxg = agopen("auxg", Agundirected, NULL);
  agbindrec(auxg, "Agraphinfo_t", sizeof(Agraphinfo_t), true);
  agattr_text(auxg, AGRAPH, "rank", "");
  GD_drawing(auxg) = gv_alloc(sizeof(layout_t));
  GD_drawing(auxg)->quantum = GD_drawing(g)->quantum;
  GD_drawing(auxg)->dpi = GD_drawing(g)->dpi;

  GD_charset(auxg) = GD_charset(g);
  if (GD_flip(g))
    SET_RANKDIR(auxg, RANKDIR_TB);
  else
    SET_RANKDIR(auxg, RANKDIR_LR);
  GD_nodesep(auxg) = GD_nodesep(g);
  GD_ranksep(auxg) = GD_ranksep(g);

  // copy node attrs to auxg
  sym = agnxtattr(agroot(g), AGNODE, NULL); // get the first attr.
  for (; sym; sym = agnxtattr(agroot(g), AGNODE, sym)) {
    const bool is_html = aghtmlstr(sym->defval);
    is_html ? agattr_html(auxg, AGNODE, sym->name, sym->defval)
            : agattr_text(auxg, AGNODE, sym->name, sym->defval);
  }

  // copy edge attributes
  sym = agnxtattr(agroot(g), AGEDGE, NULL); // get the first attr.
  for (; sym; sym = agnxtattr(agroot(g), AGEDGE, sym)) {
    const bool is_html = aghtmlstr(sym->defval);
    is_html ? agattr_html(auxg, AGEDGE, sym->name, sym->defval)
            : agattr_text(auxg, AGEDGE, sym->name, sym->defval);
  }

  if (!agattr_text(auxg, AGEDGE, "headport", NULL))
    agattr_text(auxg, AGEDGE, "headport", "");
  if (!agattr_text(auxg, AGEDGE, "tailport", NULL))
    agattr_text(auxg, AGEDGE, "tailport", "");

  setState(auxg, attr_state);

  return auxg;
}

static void cleanupCloneGraph(graph_t *g, attr_state_t *attr_state) {
  /* restore main graph syms */
  E_constr = attr_state->E_constr;
  E_dir = attr_state->E_dir;
  E_samehead = attr_state->E_samehead;
  E_sametail = attr_state->E_sametail;
  E_weight = attr_state->E_weight;
  E_minlen = attr_state->E_minlen;
  E_fontcolor = attr_state->E_fontcolor;
  E_fontname = attr_state->E_fontname;
  E_fontsize = attr_state->E_fontsize;
  E_headclip = attr_state->E_headclip;
  E_headlabel = attr_state->E_headlabel;
  E_label = attr_state->E_label;
  E_label_float = attr_state->E_label_float;
  E_labelfontcolor = attr_state->E_labelfontcolor;
  E_labelfontname = attr_state->E_labelfontname;
  E_labelfontsize = attr_state->E_labelfontsize;
  E_tailclip = attr_state->E_tailclip;
  E_taillabel = attr_state->E_taillabel;
  E_xlabel = attr_state->E_xlabel;
  N_height = attr_state->N_height;
  N_width = attr_state->N_width;
  N_shape = attr_state->N_shape;
  N_style = attr_state->N_style;
  N_fontsize = attr_state->N_fontsize;
  N_fontname = attr_state->N_fontname;
  N_fontcolor = attr_state->N_fontcolor;
  N_label = attr_state->N_label;
  N_xlabel = attr_state->N_xlabel;
  N_showboxes = attr_state->N_showboxes;
  N_ordering = attr_state->N_ordering;
  N_sides = attr_state->N_sides;
  N_peripheries = attr_state->N_peripheries;
  N_skew = attr_state->N_skew;
  N_orientation = attr_state->N_orientation;
  N_distortion = attr_state->N_distortion;
  N_fixed = attr_state->N_fixed;
  N_nojustify = attr_state->N_nojustify;
  N_group = attr_state->N_group;
  G_ordering = attr_state->G_ordering;
  State = attr_state->State;

  dot_cleanup(g);
  agclose(g);
}

/* If original graph has rankdir=LR or RL, records change shape,
 * so we wrap a record node's label in "{...}" to prevent this.
 */
static node_t *cloneNode(graph_t *g, node_t *orign) {
  node_t *n = agnode(g, agnameof(orign), 1);
  agbindrec(n, "Agnodeinfo_t", sizeof(Agnodeinfo_t), true);
  agcopyattr(orign, n);
  if (shapeOf(orign) == SH_RECORD) {
    agxbuf buf = {0};
    agxbprint(&buf, "{%s}", ND_label(orign)->text);
    agset(n, "label", agxbuse(&buf));
    agxbfree(&buf);
  }

  return n;
}

static edge_t *cloneEdge(graph_t *g, node_t *tn, node_t *hn, edge_t *orig) {
  edge_t *e = agedge(g, tn, hn, NULL, 1);
  agbindrec(e, "Agedgeinfo_t", sizeof(Agedgeinfo_t), true);
  agcopyattr(orig, e);

  return e;
}

static void copy_grouped_ports(edge_t *clone, const edge_t *original,
                               bool reversed) {
  const port tail_port =
      reversed ? ED_head_port(original) : ED_tail_port(original);
  const port head_port =
      reversed ? ED_tail_port(original) : ED_head_port(original);
  const char *const tail_group =
      agget((edge_t *)original, reversed ? "samehead" : "sametail");
  const char *const head_group =
      agget((edge_t *)original, reversed ? "sametail" : "samehead");

  /* Explicit clipping ports are resolved correctly from the copied edge
   * attributes in the rotated auxiliary graph. Only carry synthetic
   * samehead/sametail anchors, whose non-clipping coordinates are otherwise
   * lost when the auxiliary graph resolves its own ports. */
  if (tail_group != NULL && tail_group[0] != '\0' && tail_port.defined &&
      !tail_port.clip)
    ED_tail_port(clone) = tail_port;
  if (head_group != NULL && head_group[0] != '\0' && head_port.defined &&
      !head_port.clip)
    ED_head_port(clone) = head_port;
}

static void restore_flat_edge_ports(edge_t **edges, unsigned count,
                                    node_t *tail) {
  for (unsigned i = 0; i < count; ++i) {
    edge_t *edge = edges[i];
    while (ED_edge_type(edge) != NORMAL)
      edge = ED_to_orig(edge);

    edge_t *const clone = ED_alg(edge);
    if (clone != NULL)
      copy_grouped_ports(clone, edge, agtail(edge) != tail);
  }
}

static double endpoint_node_clearance(node_t *node, pointf pt) {
  const double rx = MAX(ND_lw(node), ND_rw(node)) + MULTIEDGE_NODE_MARGIN;
  const double ry = ND_ht(node) / 2 + MULTIEDGE_NODE_MARGIN;
  const pointf delta = sub_pointf(pt, ND_coord(node));
  return (delta.x / rx) * (delta.x / rx) + (delta.y / ry) * (delta.y / ry);
}

static pointf cubic_point(const pointf control[4], double t) {
  const double u = 1.0 - t;
  pointf pt = {0};
  pt.x = u * u * u * control[0].x + 3.0 * u * u * t * control[1].x +
         3.0 * u * t * t * control[2].x + t * t * t * control[3].x;
  pt.y = u * u * u * control[0].y + 3.0 * u * u * t * control[1].y +
         3.0 * u * t * t * control[2].y + t * t * t * control[3].y;
  return pt;
}

static bool terminal_reenters_node(node_t *node, const pointf control[4]) {
  size_t clear = 0;
  bool found_clearance = false;
  double distances[NSUB + 2];
  for (size_t i = 0; i < ARRAY_SIZE(distances); ++i) {
    distances[i] =
        endpoint_node_clearance(node, cubic_point(control, (double)i / NSUB));
    if (!found_clearance && distances[i] >= 1.0) {
      clear = i;
      found_clearance = true;
    }
  }
  for (size_t i = clear; i < ARRAY_SIZE(distances); ++i) {
    if (distances[i] < 0.98)
      return true;
  }
  return false;
}

static void bend_endpoint_control_outward(node_t *node, pointf endpoint,
                                          pointf *control) {
  const pointf normal = sub_pointf(endpoint, ND_coord(node));
  const pointf departure = sub_pointf(*control, endpoint);
  if (normal.x * departure.x + normal.y * departure.y > 0)
    return;

  const double normal_length = hypot(normal.x, normal.y);
  const double control_length = DIST(*control, endpoint);
  if (normal_length <= MILLIPOINT || control_length <= MILLIPOINT)
    return;

  *control =
      add_pointf(endpoint, scale(control_length / normal_length, normal));
}

static void keep_terminal_cubic_outside_node(node_t *node, bezier *spline,
                                             bool physical_start) {
  if (spline->size < 4)
    return;

  const size_t endpoint = physical_start ? 0 : spline->size - 1;
  const size_t near_control = physical_start ? 1 : spline->size - 2;
  const size_t far_control = physical_start ? 2 : spline->size - 3;
  const size_t far_endpoint = physical_start ? 3 : spline->size - 4;
  pointf terminal[4] = {
      spline->list[endpoint],
      spline->list[near_control],
      spline->list[far_control],
      spline->list[far_endpoint],
  };
  if (!terminal_reenters_node(node, terminal))
    return;

  const pointf normal = sub_pointf(spline->list[endpoint], ND_coord(node));
  const double normal_length = hypot(normal.x, normal.y);
  if (normal_length <= MILLIPOINT)
    return;

  const double control_length =
      MAX(DIST(spline->list[endpoint], spline->list[far_control]),
          FLAT_PORT_NORMAL_ARM);
  spline->list[far_control] = add_pointf(
      spline->list[endpoint], scale(control_length / normal_length, normal));
}

static void restore_flat_endpoint(bezier *spline, bool physical_start,
                                  pointf anchor, port resolved_port,
                                  double min_control_length) {
  const size_t endpoint = physical_start ? 0 : spline->size - 1;
  const size_t control = physical_start ? 1 : spline->size - 2;
  const int arrow = physical_start ? spline->sflag : spline->eflag;
  pointf *const arrow_tip = physical_start ? &spline->sp : &spline->ep;
  const double normal_length = hypot(resolved_port.p.x, resolved_port.p.y);
  /* A repeated aux control point has no departure direction to preserve. */
  const double control_length = MAX(
      DIST(spline->list[control], spline->list[endpoint]), min_control_length);
  const double arrow_gap = DIST(*arrow_tip, spline->list[endpoint]);

  if (normal_length > MILLIPOINT) {
    const pointf outward = scale(1.0 / normal_length, resolved_port.p);
    spline->list[endpoint] =
        add_pointf(anchor, scale(arrow == ARR_NONE ? 0.0 : arrow_gap, outward));
    spline->list[control] =
        add_pointf(spline->list[endpoint], scale(control_length, outward));
    if (arrow != ARR_NONE)
      *arrow_tip = anchor;
    return;
  }

  if (arrow != ARR_NONE) {
    const pointf offset = sub_pointf(anchor, *arrow_tip);
    spline->list[endpoint] = add_pointf(spline->list[endpoint], offset);
    spline->list[control] = add_pointf(spline->list[control], offset);
    *arrow_tip = anchor;
  } else {
    const pointf offset = sub_pointf(anchor, spline->list[endpoint]);
    spline->list[endpoint] = anchor;
    spline->list[control] = add_pointf(spline->list[control], offset);
  }
}

static void straighten_flat_port_progression(bezier *spline) {
  if (spline->size <= 4)
    return;

  const pointf start = spline->list[0];
  const pointf end = spline->list[spline->size - 1];
  for (size_t i = 2; i + 2 < spline->size; ++i) {
    const double fraction = (double)i / (double)(spline->size - 1);
    spline->list[i].x = start.x + (end.x - start.x) * fraction;
  }
}

static void restore_flat_endpoints(edge_t *edge, bezier *spline) {
  const pointf tail_center = ND_coord(agtail(edge));
  const pointf head_center = ND_coord(aghead(edge));
  const bool tail_at_start = DIST(spline->list[0], tail_center) <=
                             DIST(spline->list[spline->size - 1], tail_center);
  const bool head_at_start = DIST(spline->list[0], head_center) <=
                             DIST(spline->list[spline->size - 1], head_center);

  const bool grouped_tail =
      E_sametail != NULL && agxget(edge, E_sametail)[0] != '\0';
  const bool grouped_head =
      E_samehead != NULL && agxget(edge, E_samehead)[0] != '\0';

  if (ED_tail_port(edge).defined && !ED_tail_port(edge).clip) {
    const pointf anchor =
        add_pointf(ND_coord(agtail(edge)), ED_tail_port(edge).p);
    restore_flat_endpoint(spline, tail_at_start, anchor, ED_tail_port(edge),
                          grouped_tail ? 6.0 : FLAT_PORT_NORMAL_ARM);
  }
  if (ED_head_port(edge).defined && !ED_head_port(edge).clip) {
    const pointf anchor =
        add_pointf(ND_coord(aghead(edge)), ED_head_port(edge).p);
    restore_flat_endpoint(spline, head_at_start, anchor, ED_head_port(edge),
                          grouped_head ? 6.0 : FLAT_PORT_NORMAL_ARM);
  }
  if (ED_tail_port(edge).defined && !ED_tail_port(edge).clip &&
      ED_head_port(edge).defined && !ED_head_port(edge).clip && !grouped_tail &&
      !grouped_head) {
    straighten_flat_port_progression(spline);
  }
}

/// rotate, if necessary, then translate points
static pointf transformf(pointf p, pointf del, int flip) {
  if (flip) {
    double i = p.x;
    p.x = p.y;
    p.y = -i;
  }
  return add_pointf(p, del);
}

/* lexicographically order edges by
 *  - has label
 *  - label is wider
 *  - label is higher
 */
static int edgelblcmpfn(const void *x, const void *y) {
  edge_t *const *const ptr0 = x;
  edge_t *const *const ptr1 = y;
  pointf sz0, sz1;

  edge_t *e0 = *ptr0;
  edge_t *e1 = *ptr1;

  if (ED_label(e0)) {
    if (ED_label(e1)) {
      sz0 = ED_label(e0)->dimen;
      sz1 = ED_label(e1)->dimen;
      if (sz0.x > sz1.x)
        return -1;
      if (sz0.x < sz1.x)
        return 1;
      if (sz0.y > sz1.y)
        return -1;
      if (sz0.y < sz1.y)
        return 1;
      return 0;
    }
    return -1;
  }
  if (ED_label(e1)) {
    return 1;
  }
  return 0;
}

#define LBL_SPACE 6 /* space between labels, in points */

/* This handles the second simplest case for flat edges between
 * two adjacent nodes. We still invoke a dot on a rotated problem
 * to handle edges with ports. This usually works, but fails for
 * records because of their weird nature.
 */
static void makeSimpleFlatLabels(node_t *tn, node_t *hn, edge_t **edges,
                                 unsigned cnt, int et, unsigned n_lbls) {
  Ppoly_t poly;
  edge_t *e = *edges;
  pointf points[10], tp, hp;
  double leftend, rightend, ctrx, ctry, miny, maxy;
  double uminx, umaxx;
  double lminx = 0.0, lmaxx = 0.0;

  edge_t **earray = gv_calloc(cnt, sizeof(edge_t *));

  for (unsigned i = 0; i < cnt; i++) {
    earray[i] = edges[i];
  }

  qsort(earray, cnt, sizeof(edge_t *), edgelblcmpfn);

  tp = add_pointf(ND_coord(tn), ED_tail_port(e).p);
  hp = add_pointf(ND_coord(hn), ED_head_port(e).p);

  leftend = tp.x + ND_rw(tn);
  rightend = hp.x - ND_lw(hn);
  ctrx = (leftend + rightend) / 2.0;

  /* do first edge */
  e = earray[0];
  size_t pointn = 0;
  points[pointn++] = tp;
  points[pointn++] = tp;
  points[pointn++] = hp;
  points[pointn++] = hp;
  clip_and_install(e, aghead(e), points, pointn, &sinfo);
  align_concentrated_route_tangents(agraphof(tn), e);
  if (Concentrate)
    align_arrow_tangents(agraphof(tn), e);
  ED_label(e)->pos.x = ctrx;
  ED_label(e)->pos.y = tp.y + (ED_label(e)->dimen.y + LBL_SPACE) / 2.0;
  ED_label(e)->set = true;

  miny = tp.y + LBL_SPACE / 2.0;
  maxy = miny + ED_label(e)->dimen.y;
  uminx = ctrx - ED_label(e)->dimen.x / 2.0;
  umaxx = ctrx + ED_label(e)->dimen.x / 2.0;

  unsigned i;
  for (i = 1; i < n_lbls; i++) {
    e = earray[i];
    if (i % 2) { /* down */
      if (i == 1) {
        lminx = ctrx - ED_label(e)->dimen.x / 2.0;
        lmaxx = ctrx + ED_label(e)->dimen.x / 2.0;
      }
      miny -= LBL_SPACE + ED_label(e)->dimen.y;
      points[0] = tp;
      points[1] = (pointf){.x = tp.x, .y = miny - LBL_SPACE};
      points[2] = (pointf){.x = hp.x, .y = points[1].y};
      points[3] = hp;
      points[4] = (pointf){.x = lmaxx, .y = hp.y};
      points[5] = (pointf){.x = lmaxx, .y = miny};
      points[6] = (pointf){.x = lminx, .y = miny};
      points[7] = (pointf){.x = lminx, .y = tp.y};
      ctry = miny + ED_label(e)->dimen.y / 2.0;
    } else { /* up */
      points[0] = tp;
      points[1] = (pointf){.x = uminx, .y = tp.y};
      points[2] = (pointf){.x = uminx, .y = maxy};
      points[3] = (pointf){.x = umaxx, .y = maxy};
      points[4] = (pointf){.x = umaxx, .y = hp.y};
      points[5] = hp;
      points[6] = (pointf){.x = hp.x, .y = maxy + LBL_SPACE};
      points[7] = (pointf){.x = tp.x, .y = maxy + LBL_SPACE};
      ctry = maxy + ED_label(e)->dimen.y / 2.0 + LBL_SPACE;
      maxy += ED_label(e)->dimen.y + LBL_SPACE;
    }
    poly.pn = 8;
    poly.ps = (Ppoint_t *)points;
    size_t pn;
    pointf *ps = simpleSplineRoute(tp, hp, poly, &pn, et == EDGETYPE_PLINE);
    if (ps == NULL || pn == 0) {
      free(ps);
      free(earray);
      return;
    }
    ED_label(e)->pos.x = ctrx;
    ED_label(e)->pos.y = ctry;
    ED_label(e)->set = true;
    clip_and_install(e, aghead(e), ps, pn, &sinfo);
    align_concentrated_route_tangents(agraphof(tn), e);
    if (Concentrate)
      align_arrow_tangents(agraphof(tn), e);
    free(ps);
  }

  /* edges with no labels */
  for (; i < cnt; i++) {
    e = earray[i];
    if (i % 2) { /* down */
      if (i == 1) {
        lminx = (2 * leftend + rightend) / 3.0;
        lmaxx = (leftend + 2 * rightend) / 3.0;
      }
      miny -= LBL_SPACE;
      points[0] = tp;
      points[1] = (pointf){.x = tp.x, .y = miny - LBL_SPACE};
      points[2] = (pointf){.x = hp.x, .y = points[1].y};
      points[3] = hp;
      points[4] = (pointf){.x = lmaxx, .y = hp.y};
      points[5] = (pointf){.x = lmaxx, .y = miny};
      points[6] = (pointf){.x = lminx, .y = miny};
      points[7] = (pointf){.x = lminx, .y = tp.y};
    } else { /* up */
      points[0] = tp;
      points[1] = (pointf){.x = uminx, .y = tp.y};
      points[2] = (pointf){.x = uminx, .y = maxy};
      points[3] = (pointf){.x = umaxx, .y = maxy};
      points[4] = (pointf){.x = umaxx, .y = hp.y};
      points[5] = hp;
      points[6] = (pointf){.x = hp.x, .y = maxy + LBL_SPACE};
      points[7] = (pointf){.x = tp.x, .y = maxy + LBL_SPACE};
      maxy += +LBL_SPACE;
    }
    poly.pn = 8;
    poly.ps = (Ppoint_t *)points;
    size_t pn;
    pointf *ps = simpleSplineRoute(tp, hp, poly, &pn, et == EDGETYPE_PLINE);
    if (ps == NULL || pn == 0) {
      free(ps);
      free(earray);
      return;
    }
    clip_and_install(e, aghead(e), ps, pn, &sinfo);
    align_concentrated_route_tangents(agraphof(tn), e);
    if (Concentrate)
      align_arrow_tangents(agraphof(tn), e);
    free(ps);
  }

  free(earray);
}

static void makeSimpleFlat(node_t *tn, node_t *hn, edge_t **edges, unsigned cnt,
                           int et) {
  edge_t *e = *edges;
  pointf points[10], tp, hp;
  double stepy, dy;

  tp = add_pointf(ND_coord(tn), ED_tail_port(e).p);
  hp = add_pointf(ND_coord(hn), ED_head_port(e).p);

  stepy = cnt > 1 ? ND_ht(tn) / (double)(cnt - 1) : 0.;
  dy = tp.y - (cnt > 1 ? ND_ht(tn) / 2. : 0.);

  for (unsigned i = 0; i < cnt; i++) {
    e = edges[i];
    size_t pointn = 0;
    if (et == EDGETYPE_SPLINE || et == EDGETYPE_LINE) {
      points[pointn++] = tp;
      const double arrow_length = NOMINAL_ARROW_LENGTH;
      const double clear_span = fabs(hp.x - tp.x) - ND_rw(tn) - ND_lw(hn);
      if (et == EDGETYPE_SPLINE && cnt == 1 && ED_conc_opp_flag(e) &&
          ((ED_head_label(e) != NULL || ED_tail_label(e) != NULL) ||
           clear_span < 2 * arrow_length)) {
        const double lift = MAX(2 * arrow_length, MAX(ND_ht(tn), ND_ht(hn)));
        const double middle = (tp.x + hp.x) / 2;
        const double arm = MAX(arrow_length, fabs(hp.x - tp.x) / 6);
        points[pointn++] = (pointf){tp.x + arm, tp.y};
        points[pointn++] = (pointf){middle, tp.y + lift};
        points[pointn++] = (pointf){middle, tp.y + lift};
        points[pointn++] = (pointf){middle, hp.y + lift};
        points[pointn++] = (pointf){hp.x - arm, hp.y};
      } else {
        points[pointn++] = (pointf){(2 * tp.x + hp.x) / 3, dy};
        points[pointn++] = (pointf){(2 * hp.x + tp.x) / 3, dy};
      }
      points[pointn++] = hp;
    } else { /* EDGETYPE_PLINE */
      points[pointn++] = tp;
      points[pointn++] = tp;
      points[pointn++] = (pointf){(2 * tp.x + hp.x) / 3, dy};
      points[pointn++] = (pointf){(2 * tp.x + hp.x) / 3, dy};
      points[pointn++] = (pointf){(2 * tp.x + hp.x) / 3, dy};
      points[pointn++] = (pointf){(2 * hp.x + tp.x) / 3, dy};
      points[pointn++] = (pointf){(2 * hp.x + tp.x) / 3, dy};
      points[pointn++] = (pointf){(2 * hp.x + tp.x) / 3, dy};
      points[pointn++] = hp;
      points[pointn++] = hp;
    }
    dy += stepy;
    clip_and_install(e, aghead(e), points, pointn, &sinfo);
    if (Concentrate) {
      align_concentrated_route_tangents(agraphof(tn), e);
      align_arrow_tangents(agraphof(tn), e);
    }
  }
}

/* In the simple case, with no labels or ports, this creates a simple
 * spindle of splines.
 * If there are only labels, cobble something together.
 * Otherwise, we run dot recursively on the 2 nodes and the edges,
 * essentially using rankdir=LR, to get the needed spline info.
 * This is probably to cute and fragile, and should be rewritten in a
 * more straightforward and laborious fashion.
 *
 * @return 0 on success
 */
static int make_flat_adj_edges(graph_t *g, edge_t **edges, unsigned cnt,
                               edge_t *e0, int et) {
  node_t *n;
  node_t *tn, *hn;
  edge_t *e;
  graph_t *auxg;
  graph_t *subg;
  node_t *auxt, *auxh;
  edge_t *auxe;
  double midx, midy, leftx, rightx;
  pointf del;
  edge_t *hvye = NULL;
  static atomic_flag warned;

  tn = agtail(e0), hn = aghead(e0);
  node_t *const physical_tail = tn;
  if (shapeOf(tn) == SH_RECORD || shapeOf(hn) == SH_RECORD) {
    if (!atomic_flag_test_and_set(&warned)) {
      agwarningf("flat edge between adjacent nodes one of which has a record "
                 "shape - replace records with HTML-like labels\n");
      agerr(AGPREV, "  Edge %s %s %s\n", agnameof(tn),
            agisdirected(g) ? "->" : "--", agnameof(hn));
    }
    return 0;
  }
  unsigned labels = 0;
  bool ports = false;
  for (unsigned i = 0; i < cnt; i++) {
    e = edges[i];
    if (ED_label(e))
      labels++;
    if (ED_tail_port(e).defined || ED_head_port(e).defined)
      ports = true;
  }

  if (!ports) {
    /* flat edges without ports and labels can go straight left to right */
    if (labels == 0) {
      makeSimpleFlat(tn, hn, edges, cnt, et);
    }
    /* flat edges without ports but with labels take more work */
    else {
      makeSimpleFlatLabels(tn, hn, edges, cnt, et, labels);
    }
    return 0;
  }

  attr_state_t attrs = {0};
  auxg = cloneGraph(g, &attrs);
  subg = agsubg(auxg, "xxx", 1);
  agbindrec(subg, "Agraphinfo_t", sizeof(Agraphinfo_t), true);
  agset(subg, "rank", "source");
  rightx = ND_coord(hn).x;
  leftx = ND_coord(tn).x;
  if (GD_flip(g)) {
    SWAP(&tn, &hn);
  }
  auxt = cloneNode(subg, tn);
  auxh = cloneNode(auxg, hn);
  for (unsigned i = 0; i < cnt; i++) {
    e = edges[i];
    for (; ED_edge_type(e) != NORMAL; e = ED_to_orig(e))
      ;
    if (agtail(e) == tn)
      auxe = cloneEdge(auxg, auxt, auxh, e);
    else
      auxe = cloneEdge(auxg, auxh, auxt, e);
    ED_alg(e) = auxe;
    if (!hvye && !ED_tail_port(e).defined && !ED_head_port(e).defined) {
      hvye = auxe;
      ED_alg(hvye) = e;
    }
  }
  if (!hvye) {
    hvye = agedge(auxg, auxt, auxh, NULL, 1);
  }
  agxset(hvye, E_weight, "10000");
  GD_gvc(auxg) = GD_gvc(g);
  GD_dotroot(auxg) = auxg;
  setEdgeType(auxg, et);
  dot_init_node_edge(auxg);
  restore_flat_edge_ports(edges, cnt, physical_tail);

  dot_rank(auxg);
  const int r = dot_mincross(auxg);
  if (r != 0) {
    return r;
  }
  {
    const int rc = dot_position(auxg);
    if (rc != 0) {
      return rc;
    }
  }

  /* reposition */
  midx = (ND_coord(tn).x - ND_rw(tn) + ND_coord(hn).x + ND_lw(hn)) / 2;
  midy = (ND_coord(auxt).x + ND_coord(auxh).x) / 2;
  for (n = GD_nlist(auxg); n; n = ND_next(n)) {
    if (n == auxt) {
      ND_coord(n).y = rightx;
      ND_coord(n).x = midy;
    } else if (n == auxh) {
      ND_coord(n).y = leftx;
      ND_coord(n).x = midy;
    } else
      ND_coord(n).y = midx;
  }
  dot_sameports(auxg);
  restore_flat_edge_ports(edges, cnt, physical_tail);
  const int rc = dot_splines_(auxg, 0);
  if (rc != 0) {
    return rc;
  }
  dotneato_postprocess(auxg);

  /* copy splines */
  if (GD_flip(g)) {
    del.x = ND_coord(tn).x - ND_coord(auxt).y;
    del.y = ND_coord(tn).y + ND_coord(auxt).x;
  } else {
    del.x = ND_coord(tn).x - ND_coord(auxt).x;
    del.y = ND_coord(tn).y - ND_coord(auxt).y;
  }
  for (unsigned i = 0; i < cnt; i++) {
    bezier *auxbz;
    bezier *bz;

    e = edges[i];
    for (; ED_edge_type(e) != NORMAL; e = ED_to_orig(e))
      ;
    auxe = ED_alg(e);
    if ((auxe == hvye) & !ED_alg(auxe))
      continue; /* pseudo-edge */
    splines *auxspl = ED_spl(auxe);
    if (auxspl == NULL)
      continue;
    auxbz = auxspl->list;
    bz = new_spline(e, auxbz->size);
    bz->sflag = auxbz->sflag;
    bz->suppress_sflag = auxbz->suppress_sflag;
    bz->sp = transformf(auxbz->sp, del, GD_flip(g));
    bz->eflag = auxbz->eflag;
    bz->suppress_eflag = auxbz->suppress_eflag;
    bz->ep = transformf(auxbz->ep, del, GD_flip(g));
    for (size_t j = 0; j < auxbz->size; ++j)
      bz->list[j] = transformf(auxbz->list[j], del, GD_flip(g));
    restore_flat_endpoints(e, bz);
    if (Concentrate)
      align_arrow_tangents(g, e);
    for (size_t j = 0; j + 3 < bz->size; j += 3)
      update_bb_bz(&GD_bb(g), &bz->list[j]);
    if (ED_label(e)) {
      ED_label(e)->pos = transformf(ED_label(auxe)->pos, del, GD_flip(g));
      ED_label(e)->set = true;
      updateBB(g, ED_label(e));
    }
  }

  cleanupCloneGraph(auxg, &attrs);
  return 0;
}

static void makeFlatEnd(graph_t *g, const spline_info_t sp, path *P, node_t *n,
                        edge_t *e, pathend_t *endp, bool isBegin) {
  boxf b = endp->nb = maximal_bbox(g, sp, n, NULL, e);
  endp->sidemask = TOP;
  if (isBegin)
    beginpath(P, e, FLATEDGE, endp, false);
  else
    endpath(P, e, FLATEDGE, endp, false);
  b.UR.y = endp->boxes[endp->boxn - 1].UR.y;
  b.LL.y = endp->boxes[endp->boxn - 1].LL.y;
  b = makeregularend(b, TOP, ND_coord(n).y + GD_rank(g)[ND_rank(n)].ht2);
  if (b.LL.x < b.UR.x && b.LL.y < b.UR.y)
    endp->boxes[endp->boxn++] = b;
}

static void makeBottomFlatEnd(graph_t *g, const spline_info_t sp, path *P,
                              node_t *n, edge_t *e, pathend_t *endp,
                              bool isBegin) {
  boxf b = endp->nb = maximal_bbox(g, sp, n, NULL, e);
  endp->sidemask = BOTTOM;
  if (isBegin)
    beginpath(P, e, FLATEDGE, endp, false);
  else
    endpath(P, e, FLATEDGE, endp, false);
  b.UR.y = endp->boxes[endp->boxn - 1].UR.y;
  b.LL.y = endp->boxes[endp->boxn - 1].LL.y;
  b = makeregularend(b, BOTTOM, ND_coord(n).y - GD_rank(g)[ND_rank(n)].ht2);
  if (b.LL.x < b.UR.x && b.LL.y < b.UR.y)
    endp->boxes[endp->boxn++] = b;
}

static void make_flat_labeled_edge(graph_t *g, const spline_info_t sp, path *P,
                                   edge_t *e, int et) {
  node_t *tn, *hn, *ln;
  pointf *ps;
  bool ps_needs_free = false;
  pathend_t tend, hend;
  boxf lb;
  int i;
  edge_t *f;
  pointf points[7];

  tn = agtail(e);
  hn = aghead(e);

  for (f = ED_to_virt(e); ED_to_virt(f); f = ED_to_virt(f))
    ;
  ln = agtail(f);
  ED_label(e)->pos = ND_coord(ln);
  ED_label(e)->set = true;

  size_t pn;
  if (et == EDGETYPE_LINE) {
    pointf startp, endp, lp;

    startp = add_pointf(ND_coord(tn), ED_tail_port(e).p);
    endp = add_pointf(ND_coord(hn), ED_head_port(e).p);

    lp = ED_label(e)->pos;
    lp.y -= ED_label(e)->dimen.y / 2.0;
    points[1] = points[0] = startp;
    points[2] = points[3] = points[4] = lp;
    points[5] = points[6] = endp;
    ps = points;
    pn = 7;
  } else {
    lb.LL.x = ND_coord(ln).x - ND_lw(ln);
    lb.UR.x = ND_coord(ln).x + ND_rw(ln);
    lb.UR.y = ND_coord(ln).y + ND_ht(ln) / 2;
    double ydelta = ND_coord(ln).y - GD_rank(g)[ND_rank(tn)].ht1 -
                    ND_coord(tn).y + GD_rank(g)[ND_rank(tn)].ht2;
    ydelta /= 6;
    lb.LL.y = lb.UR.y - MAX(5, ydelta);

    makeFlatEnd(g, sp, P, tn, e, &tend, true);
    makeFlatEnd(g, sp, P, hn, e, &hend, false);

    boxf boxes[] = {
        {
            .LL =
                {
                    .x = tend.boxes[tend.boxn - 1].LL.x,
                    .y = tend.boxes[tend.boxn - 1].UR.y,
                },
            .UR = lb.LL,
        },
        {
            .LL =
                {
                    .x = tend.boxes[tend.boxn - 1].LL.x,
                    .y = lb.LL.y,
                },
            .UR =
                {
                    .x = hend.boxes[hend.boxn - 1].UR.x,
                    .y = lb.UR.y,
                },
        },
        {
            .LL =
                {
                    .x = lb.UR.x,
                    .y = hend.boxes[hend.boxn - 1].UR.y,
                },
            .UR =
                {
                    .x = hend.boxes[hend.boxn - 1].UR.x,
                    .y = lb.LL.y,
                },
        },
    };
    const size_t boxn = sizeof(boxes) / sizeof(boxes[0]);

    for (i = 0; i < tend.boxn; i++)
      add_box(P, tend.boxes[i]);
    for (size_t j = 0; j < boxn; j++)
      add_box(P, boxes[j]);
    for (i = hend.boxn - 1; i >= 0; i--)
      add_box(P, hend.boxes[i]);

    ps_needs_free = true;
    if (et == EDGETYPE_SPLINE)
      ps = routesplines(P, &pn);
    else
      ps = routepolylines(P, &pn);
    if (pn == 0) {
      free(ps);
      return;
    }
  }
  clip_and_install(e, aghead(e), ps, pn, &sinfo);
  align_concentrated_route_tangents(g, e);
  if (Concentrate)
    align_arrow_tangents(g, e);
  if (ps_needs_free)
    free(ps);
}

static void make_flat_bottom_edges(graph_t *g, const spline_info_t sp, path *P,
                                   edge_t **edges, unsigned cnt, edge_t *e,
                                   bool use_splines) {
  node_t *tn, *hn;
  int j, r;
  double stepx, stepy, vspace;
  rank_t *nextr;
  pathend_t tend, hend;

  tn = agtail(e);
  hn = aghead(e);
  r = ND_rank(tn);
  if (r < GD_maxrank(g)) {
    nextr = GD_rank(g) + (r + 1);
    vspace = ND_coord(tn).y - GD_rank(g)[r].pht1 -
             (ND_coord(nextr->v[0]).y + nextr->pht2);
  } else {
    vspace = GD_ranksep(g);
  }
  stepx = sp.Multisep / (cnt + 1);
  stepy = vspace / (cnt + 1);

  makeBottomFlatEnd(g, sp, P, tn, e, &tend, true);
  makeBottomFlatEnd(g, sp, P, hn, e, &hend, false);

  for (unsigned i = 0; i < cnt; i++) {
    boxf b;
    e = edges[i];
    size_t boxn = 0;

    boxf boxes[3];

    b = tend.boxes[tend.boxn - 1];
    boxes[boxn].LL.x = b.LL.x;
    boxes[boxn].UR.y = b.LL.y;
    boxes[boxn].UR.x = b.UR.x + (i + 1) * stepx;
    boxes[boxn].LL.y = b.LL.y - (i + 1) * stepy;
    boxn++;
    boxes[boxn].LL.x = tend.boxes[tend.boxn - 1].LL.x;
    boxes[boxn].UR.y = boxes[boxn - 1].LL.y;
    boxes[boxn].UR.x = hend.boxes[hend.boxn - 1].UR.x;
    boxes[boxn].LL.y = boxes[boxn].UR.y - stepy;
    boxn++;
    b = hend.boxes[hend.boxn - 1];
    boxes[boxn].UR.x = b.UR.x;
    boxes[boxn].UR.y = b.LL.y;
    boxes[boxn].LL.x = b.LL.x - (i + 1) * stepx;
    boxes[boxn].LL.y = boxes[boxn - 1].UR.y;
    boxn++;
    assert(boxn == sizeof(boxes) / sizeof(boxes[0]));

    for (j = 0; j < tend.boxn; j++)
      add_box(P, tend.boxes[j]);
    for (size_t k = 0; k < boxn; k++)
      add_box(P, boxes[k]);
    for (j = hend.boxn - 1; j >= 0; j--)
      add_box(P, hend.boxes[j]);

    pointf *ps = NULL;
    size_t pn = 0;
    if (use_splines)
      ps = routesplines(P, &pn);
    else
      ps = routepolylines(P, &pn);
    if (pn == 0) {
      free(ps);
      return;
    }
    clip_and_install(e, aghead(e), ps, pn, &sinfo);
    align_concentrated_route_tangents(g, e);
    if (Concentrate)
      align_arrow_tangents(g, e);
    free(ps);
    P->nbox = 0;
  }
}

/* Construct flat edges edges[ind...ind+cnt-1]
 * There are 4 main cases:
 *  - all edges between a and b where a and b are adjacent
 *  - one labeled edge
 *  - all non-labeled edges with identical ports between non-adjacent a and b
 *     = connecting bottom to bottom/left/right - route along bottom
 *     = the rest - route along top
 *
 * @return 0 on success
 */
static int make_flat_edge(graph_t *g, const spline_info_t sp, path *P,
                          edge_t **edges, unsigned cnt, int et) {
  Agedgeinfo_t fwdedgei;
  Agedgepair_t fwdedge;
  int j;
  double stepx, stepy, vspace;
  pathend_t tend, hend;

  fwdedge.out.base.data = &fwdedgei.hdr;

  /* Get sample edge; normalize to go from left to right */
  edge_t *e = *edges;
  bool isAdjacent = ED_adjacent(e) != 0;
  if (ED_tree_index(e) & BWDEDGE) {
    makefwdedge(&fwdedge.out, e);
    e = &fwdedge.out;
  }
  for (unsigned i = 1; i < cnt; i++) {
    if (ED_adjacent(edges[i])) {
      isAdjacent = true;
      break;
    }
  }
  // The lead edge edges[0] might not have been marked earlier as adjacent, so
  // check them all.
  if (isAdjacent) {
    return make_flat_adj_edges(g, edges, cnt, e, et);
  }
  if (ED_label(e)) { /* edges with labels aren't multi-edges */
    make_flat_labeled_edge(g, sp, P, e, et);
    return 0;
  }

  if (et == EDGETYPE_LINE) {
    makeSimpleFlat(agtail(e), aghead(e), edges, cnt, et);
    return 0;
  }

  const int tside = ED_tail_port(e).side;
  const int hside = ED_head_port(e).side;
  if ((tside == BOTTOM && hside != TOP) || (hside == BOTTOM && tside != TOP)) {
    make_flat_bottom_edges(g, sp, P, edges, cnt, e, et == EDGETYPE_SPLINE);
    return 0;
  }

  node_t *tn = agtail(e);
  node_t *hn = aghead(e);
  const int r = ND_rank(tn);
  // With edge labels, an auxiliary rank sits immediately below each node rank.
  // There may be no preceding node rank.
  const int prev_rank = r - ((GD_has_labels(g->root) & EDGE_LABEL) ? 2 : 1);
  if (prev_rank >= 0) {
    rank_t *const prevr = GD_rank(g) + prev_rank;
    vspace = ND_coord(prevr->v[0]).y - prevr->ht1 - ND_coord(tn).y -
             GD_rank(g)[r].ht2;
  } else {
    vspace = GD_ranksep(g);
  }
  stepx = sp.Multisep / (cnt + 1);
  stepy = vspace / (cnt + 1);

  makeFlatEnd(g, sp, P, tn, e, &tend, true);
  makeFlatEnd(g, sp, P, hn, e, &hend, false);

  for (unsigned i = 0; i < cnt; i++) {
    boxf b;
    e = edges[i];
    size_t boxn = 0;

    boxf boxes[3];

    b = tend.boxes[tend.boxn - 1];
    boxes[boxn].LL.x = b.LL.x;
    boxes[boxn].LL.y = b.UR.y;
    boxes[boxn].UR.x = b.UR.x + (i + 1) * stepx;
    boxes[boxn].UR.y = b.UR.y + (i + 1) * stepy;
    boxn++;
    boxes[boxn].LL.x = tend.boxes[tend.boxn - 1].LL.x;
    boxes[boxn].LL.y = boxes[boxn - 1].UR.y;
    boxes[boxn].UR.x = hend.boxes[hend.boxn - 1].UR.x;
    boxes[boxn].UR.y = boxes[boxn].LL.y + stepy;
    boxn++;
    b = hend.boxes[hend.boxn - 1];
    boxes[boxn].UR.x = b.UR.x;
    boxes[boxn].LL.y = b.UR.y;
    boxes[boxn].LL.x = b.LL.x - (i + 1) * stepx;
    boxes[boxn].UR.y = boxes[boxn - 1].LL.y;
    boxn++;
    assert(boxn == sizeof(boxes) / sizeof(boxes[0]));

    for (j = 0; j < tend.boxn; j++)
      add_box(P, tend.boxes[j]);
    for (size_t k = 0; k < boxn; k++)
      add_box(P, boxes[k]);
    for (j = hend.boxn - 1; j >= 0; j--)
      add_box(P, hend.boxes[j]);

    pointf *ps = NULL;
    size_t pn = 0;
    if (et == EDGETYPE_SPLINE)
      ps = routesplines(P, &pn);
    else
      ps = routepolylines(P, &pn);
    if (pn == 0) {
      free(ps);
      return 0;
    }
    clip_and_install(e, aghead(e), ps, pn, &sinfo);
    align_concentrated_route_tangents(g, e);
    if (Concentrate)
      align_arrow_tangents(g, e);
    free(ps);
    P->nbox = 0;
  }
  return 0;
}

/// Return true if p3 is to left of ray p1->p2
static bool leftOf(pointf p1, pointf p2, pointf p3) {
  return (p1.y - p2.y) * (p3.x - p2.x) - (p3.y - p2.y) * (p1.x - p2.x) > 0;
}

/* Create an edge as line segment. We guarantee that the points
 * are always drawn downwards. This means that for flipped edges,
 * we draw from the head to the tail. The routine returns the
 * end node of the edge in *hp. The points are stored in the
 * given array of points, and the number of points is returned.
 *
 * If the edge has a label, the edge is draw as two segments, with
 * the bend near the label.
 *
 * If the endpoints are on adjacent ranks, revert to usual code by
 * returning 0.
 * This is done because the usual code handles the interaction of
 * multiple edges better.
 */
static int makeLineEdge(graph_t *g, edge_t *fe, points_t *points, node_t **hp) {
  int delr, pn;
  node_t *hn;
  node_t *tn;
  edge_t *e = fe;
  pointf startp, endp, lp;
  pointf dimen;
  double width, height;

  while (ED_edge_type(e) != NORMAL)
    e = ED_to_orig(e);
  hn = aghead(e);
  tn = agtail(e);
  delr = abs(ND_rank(hn) - ND_rank(tn));
  if (delr == 1 || (delr == 2 && (GD_has_labels(g->root) & EDGE_LABEL)))
    return 0;
  if (agtail(fe) == agtail(e)) {
    *hp = hn;
    startp = add_pointf(ND_coord(tn), ED_tail_port(e).p);
    endp = add_pointf(ND_coord(hn), ED_head_port(e).p);
  } else {
    *hp = tn;
    startp = add_pointf(ND_coord(hn), ED_head_port(e).p);
    endp = add_pointf(ND_coord(tn), ED_tail_port(e).p);
  }

  if (ED_label(e)) {
    dimen = ED_label(e)->dimen;
    if (GD_flip(agraphof(hn))) {
      width = dimen.y;
      height = dimen.x;
    } else {
      width = dimen.x;
      height = dimen.y;
    }

    lp = ED_label(e)->pos;
    if (leftOf(endp, startp, lp)) {
      lp.x += width / 2.0;
      lp.y -= height / 2.0;
    } else {
      lp.x -= width / 2.0;
      lp.y += height / 2.0;
    }

    LIST_APPEND(points, startp);
    LIST_APPEND(points, startp);
    LIST_APPEND(points, lp);
    LIST_APPEND(points, lp);
    LIST_APPEND(points, lp);
    LIST_APPEND(points, endp);
    LIST_APPEND(points, endp);
    pn = 7;
  } else {
    LIST_APPEND(points, startp);
    LIST_APPEND(points, startp);
    LIST_APPEND(points, endp);
    LIST_APPEND(points, endp);
    pn = 4;
  }

  return pn;
}

static void align_control_arm(pointf *control, pointf endpoint,
                              pointf arrow_tip, double min_control_length) {
  const pointf axis = sub_pointf(arrow_tip, endpoint);
  const double axis_length = hypot(axis.x, axis.y);
  const double control_length =
      MAX(DIST(*control, endpoint), min_control_length);
  if (axis_length <= MILLIPOINT || control_length <= MILLIPOINT)
    return;

  *control = sub_pointf(endpoint, scale(control_length / axis_length, axis));
}

static void point_control_arm_at(pointf *control, pointf endpoint,
                                 pointf target) {
  const pointf axis = sub_pointf(target, endpoint);
  const double axis_length = hypot(axis.x, axis.y);
  const double control_length = DIST(*control, endpoint);
  if (axis_length <= MILLIPOINT || control_length <= MILLIPOINT)
    return;

  *control = add_pointf(endpoint, scale(control_length / axis_length, axis));
}

static bool unit_vector(pointf vector, pointf *unit) {
  const double length = hypot(vector.x, vector.y);
  if (length <= MILLIPOINT)
    return false;

  unit->x = vector.x / length;
  unit->y = vector.y / length;
  return true;
}

static void smooth_consecutive_spline_joints(splines *spline) {
  for (size_t i = 0; i + 1 < spline->size; i++) {
    bezier *const left = &spline->list[i];
    bezier *const right = &spline->list[i + 1];
    if (left->size < 4 || right->size < 4)
      continue;

    const size_t left_last = left->size - 1;
    if (DIST(left->list[left_last], right->list[0]) > MILLIPOINT)
      continue;

    pointf incoming;
    pointf outgoing;
    if (!unit_vector(
            sub_pointf(left->list[left_last], left->list[left_last - 1]),
            &incoming) ||
        !unit_vector(sub_pointf(right->list[1], right->list[0]), &outgoing))
      continue;

    pointf tangent = add_pointf(incoming, outgoing);
    if (!unit_vector(tangent, &tangent))
      continue;

    const double left_length =
        DIST(left->list[left_last], left->list[left_last - 1]);
    const double right_length = DIST(right->list[1], right->list[0]);
    left->list[left_last - 1] =
        sub_pointf(left->list[left_last], scale(left_length, tangent));
    right->list[1] = add_pointf(right->list[0], scale(right_length, tangent));
  }
}

static void align_arrow_arm(bezier *spline, pointf arrow_tip,
                            double min_control_length) {
  const bool at_start = DIST(spline->list[0], arrow_tip) <=
                        DIST(spline->list[spline->size - 1], arrow_tip);
  const size_t endpoint = at_start ? 0 : spline->size - 1;
  const size_t control = at_start ? 1 : spline->size - 2;
  align_control_arm(&spline->list[control], spline->list[endpoint], arrow_tip,
                    min_control_length);
}

static double arrow_arm_angle(const bezier *spline, bool at_start,
                              pointf arrow_tip) {
  const size_t endpoint = at_start ? 0 : spline->size - 1;
  const int step = at_start ? 1 : -1;
  int control = (int)endpoint + step;
  while (control >= 0 && control < (int)spline->size &&
         DIST(spline->list[endpoint], spline->list[control]) <= MILLIPOINT) {
    control += step;
  }
  if (control < 0 || control >= (int)spline->size)
    return 180.0;

  const pointf shaft =
      sub_pointf(spline->list[control], spline->list[endpoint]);
  const pointf axis = sub_pointf(arrow_tip, spline->list[endpoint]);
  const double denominator = hypot(shaft.x, shaft.y) * hypot(axis.x, axis.y);
  if (denominator <= MILLIPOINT)
    return 180.0;

  const double cosine =
      fabs((shaft.x * axis.x + shaft.y * axis.y) / denominator);
  return acos(MIN(1.0, MAX(-1.0, cosine))) * 180.0 / M_PI;
}

static void align_arrow_tangents(graph_t *g, edge_t *edge) {
  while (ED_to_orig(edge) != NULL && ED_edge_type(edge) != NORMAL)
    edge = ED_to_orig(edge);

  assert(ED_spl(edge) != NULL && ED_spl(edge)->size > 0);
  bezier *const spline = &ED_spl(edge)->list[ED_spl(edge)->size - 1];
  if (spline->size < 4)
    return;

  // Multi-edge offsets are applied before clipping. Realign the final control
  // arms afterward, when clipping has established the visible arrow axes.
  if (spline->sflag != ARR_NONE)
    align_arrow_arm(spline, spline->sp, 0.0);
  if (spline->eflag != ARR_NONE)
    align_arrow_arm(spline, spline->ep, 0.0);

  const bool grouped_head =
      E_samehead != NULL && agxget(edge, E_samehead)[0] != '\0';
  const port head_port = ED_head_port(edge);
  if (grouped_head && head_port.defined && !head_port.clip) {
    const bool head_at_start =
        DIST(spline->list[0], ND_coord(aghead(edge))) <=
        DIST(spline->list[spline->size - 1], ND_coord(aghead(edge)));
    const size_t endpoint = head_at_start ? 0 : spline->size - 1;
    const size_t near_control = head_at_start ? 1 : spline->size - 2;
    bend_endpoint_control_outward(aghead(edge), spline->list[endpoint],
                                  &spline->list[near_control]);
  }
  for (size_t i = 0; i + 3 < spline->size; i += 3)
    update_bb_bz(&GD_bb(g), &spline->list[i]);
}

static void align_installed_flat_arrow_tangents(graph_t *g, edge_t *edge) {
  if (ED_spl(edge) == NULL || ED_spl(edge)->size == 0)
    return;

  bezier *const spline = &ED_spl(edge)->list[ED_spl(edge)->size - 1];
  if (spline->size < 4)
    return;

  edge_t *const arrow_edge = getmainedge(edge);
  if (spline->sflag != ARR_NONE &&
      arrow_arm_angle(spline, true, spline->sp) > 2.0)
    align_arrow_arm(spline, spline->sp,
                    NOMINAL_ARROW_LENGTH *
                        edge_arrow_arrowsize(arrow_edge, EDGE_ARROW_START));
  if (spline->eflag != ARR_NONE &&
      arrow_arm_angle(spline, false, spline->ep) > 2.0)
    align_arrow_arm(spline, spline->ep,
                    NOMINAL_ARROW_LENGTH *
                        edge_arrow_arrowsize(arrow_edge, EDGE_ARROW_END));

  const bool grouped_tail =
      E_sametail != NULL && agxget(arrow_edge, E_sametail)[0] != '\0';
  const port tail_port = ED_tail_port(arrow_edge);
  if (grouped_tail && tail_port.defined && !tail_port.clip) {
    const bool tail_at_start =
        DIST(spline->list[0], ND_coord(agtail(arrow_edge))) <=
        DIST(spline->list[spline->size - 1], ND_coord(agtail(arrow_edge)));
    keep_terminal_cubic_outside_node(agtail(arrow_edge), spline, tail_at_start);
  }

  for (size_t i = 0; i + 3 < spline->size; i += 3)
    update_bb_bz(&GD_bb(g), &spline->list[i]);
}

static void align_flat_arrow_tangents_in_graph(graph_t *g) {
  for (node_t *node = agfstnode(g); node != NULL; node = agnxtnode(g, node)) {
    for (edge_t *edge = agfstout(g, node); edge != NULL;
         edge = agnxtout(g, edge)) {
      edge_t *const main_edge = getmainedge(edge);
      if (ED_spl(edge) == NULL || ED_spl(edge)->size == 0)
        continue;
      if (ND_rank(agtail(main_edge)) != ND_rank(aghead(main_edge)))
        continue;
      if (!has_grouped_flat_endpoint(main_edge))
        continue;

      align_installed_flat_arrow_tangents(g, edge);
    }
  }
}

static double turn_angle(pointf a, pointf b, pointf c) {
  const pointf ab = sub_pointf(b, a);
  const pointf bc = sub_pointf(c, b);
  return atan2(ab.x * bc.y - ab.y * bc.x, ab.x * bc.x + ab.y * bc.y);
}

static int turn_sign(double angle) {
  if (fabs(angle) < M_PI / 180.0)
    return 0;
  return angle < 0 ? -1 : 1;
}

static double terminal_departure(node_t *node, pointf endpoint,
                                 pointf control) {
  const pointf normal = sub_pointf(endpoint, ND_coord(node));
  const pointf departure = sub_pointf(control, endpoint);
  return normal.x * departure.x + normal.y * departure.y;
}

static node_t *node_nearest_spline_endpoint(edge_t *edge, pointf endpoint) {
  edge = getmainedge(edge);
  node_t *const tail = agtail(edge);
  node_t *const head = aghead(edge);
  return DIST(endpoint, ND_coord(tail)) <= DIST(endpoint, ND_coord(head))
             ? tail
             : head;
}

static void restore_outward_terminal_controls(bezier *spline, edge_t *edge,
                                              pointf start_control,
                                              pointf end_control) {
  const size_t last = spline->size - 1;
  node_t *const start_node =
      node_nearest_spline_endpoint(edge, spline->list[0]);
  node_t *const end_node =
      node_nearest_spline_endpoint(edge, spline->list[last]);

  // Smoothing may reshape an outward arm, but must not reverse it into its
  // physical endpoint node. Restore only the arm whose sign was reversed.
  if (terminal_departure(start_node, spline->list[0], start_control) > 0 &&
      terminal_departure(start_node, spline->list[0], spline->list[1]) <= 0)
    spline->list[1] = start_control;
  if (terminal_departure(end_node, spline->list[last], end_control) > 0 &&
      terminal_departure(end_node, spline->list[last],
                         spline->list[last - 1]) <= 0)
    spline->list[last - 1] = end_control;
}

static void smooth_alternating_concentrated_controls(bezier *spline,
                                                     edge_t *edge) {
  if (spline->size <= 4)
    return;

  const pointf start_control = spline->list[1];
  const pointf end_control = spline->list[spline->size - 2];

  bool alternating = false;
  bool repeated_interior_corner = false;
  pointf repeated_peak = {0};
  for (size_t i = 1; i + 1 < spline->size; i++) {
    if (DIST(spline->list[i], spline->list[i + 1]) > MILLIPOINT)
      continue;

    size_t previous = i;
    while (previous > 0 &&
           DIST(spline->list[previous], spline->list[i]) <= MILLIPOINT) {
      previous--;
    }

    size_t next = i + 1;
    while (next + 1 < spline->size &&
           DIST(spline->list[next], spline->list[i]) <= MILLIPOINT) {
      next++;
    }

    if (previous == i || next == i + 1)
      continue;

    if (fabs(turn_angle(spline->list[previous], spline->list[i],
                        spline->list[next])) >= M_PI / 6.0) {
      repeated_interior_corner = true;
      repeated_peak = spline->list[i];
      break;
    }
  }

  if (repeated_interior_corner) {
    const pointf start = spline->list[0];
    const pointf end = spline->list[spline->size - 1];
    const pointf chord = sub_pointf(end, start);
    const pointf midpoint = {.x = (start.x + end.x) / 2.0,
                             .y = (start.y + end.y) / 2.0};
    const pointf peak_offset = sub_pointf(repeated_peak, midpoint);
    for (size_t i = 1; i + 1 < spline->size; i++) {
      const double t = (double)i / (double)(spline->size - 1);
      const pointf chord_point = add_pointf(start, scale(t, chord));
      spline->list[i] =
          add_pointf(chord_point, scale(sin(M_PI * t), peak_offset));
    }
    restore_outward_terminal_controls(spline, edge, start_control, end_control);
    return;
  }

  for (size_t i = 0; i + 3 < spline->size; i += 3) {
    const double angle1 =
        turn_angle(spline->list[i], spline->list[i + 1], spline->list[i + 2]);
    const double angle2 = turn_angle(spline->list[i + 1], spline->list[i + 2],
                                     spline->list[i + 3]);
    const int sign1 = turn_sign(angle1);
    const int sign2 = turn_sign(angle2);
    if (sign1 == 0 || sign2 == 0 || sign1 == sign2)
      continue;
    if (fabs(angle1) < M_PI / 6.0 && fabs(angle2) < M_PI / 6.0)
      continue;
    alternating = true;
    break;
  }
  if (!alternating)
    return;

  const pointf start = spline->list[0];
  const pointf chord = sub_pointf(spline->list[spline->size - 1], start);
  for (size_t i = 1; i + 1 < spline->size; i++) {
    const double t = (double)i / (double)(spline->size - 1);
    spline->list[i].x = start.x + chord.x * t;
    spline->list[i].y = start.y + chord.y * t;
  }
  restore_outward_terminal_controls(spline, edge, start_control, end_control);
}

static size_t count_spline_points(const splines *edge_splines) {
  size_t point_count = 0;
  for (size_t i = 0; i < edge_splines->size; i++)
    point_count += edge_splines->list[i].size;
  return point_count;
}

static pointf *copy_spline_points(const splines *edge_splines) {
  pointf *const points =
      gv_calloc(count_spline_points(edge_splines), sizeof(pointf));
  size_t point_index = 0;
  for (size_t i = 0; i < edge_splines->size; i++) {
    const bezier *const curve = &edge_splines->list[i];
    for (size_t j = 0; j < curve->size; j++)
      points[point_index++] = curve->list[j];
  }
  return points;
}

static bool node_shape_contains(node_t *node, pointf sample_point) {
  if (ND_shape(node) == NULL || ND_shape(node)->fns == NULL ||
      ND_shape(node)->fns->insidefn == NULL)
    return false;

  inside_t inside_context = {.s = {.n = node}};
  const pointf local = sub_pointf(sample_point, ND_coord(node));
  const double saved_right_width = ND_rw(node);
  const bool inside = ND_shape(node)->fns->insidefn(&inside_context, local);
  ND_rw(node) = saved_right_width;
  return inside;
}

static size_t edge_route_node_crossings(graph_t *g, edge_t *edge,
                                        const splines *edge_splines) {
  edge = getmainedge(edge);
  const node_t *const tail = agtail(edge);
  const node_t *const head = aghead(edge);
  size_t crossings = 0;
  for (node_t *node = agfstnode(g); node != NULL; node = agnxtnode(g, node)) {
    if (node == tail || node == head || ND_node_type(node) != NORMAL)
      continue;

    for (size_t spline_index = 0; spline_index < edge_splines->size;
         spline_index++) {
      const bezier *const spline = &edge_splines->list[spline_index];
      for (size_t start = 0; start + 3 < spline->size; start += 3) {
        pointf control[4];
        for (size_t i = 0; i < 4; i++)
          control[i] = spline->list[start + i];
        for (size_t sample = 0; sample <= 12; sample++) {
          if (node_shape_contains(node, cubic_point(control, sample / 12.0))) {
            crossings++;
            goto next_node;
          }
        }
      }
    }
  next_node:;
  }
  return crossings;
}

static size_t partially_restore_spline_point(graph_t *g, edge_t *edge,
                                             splines *edge_splines,
                                             pointf *point, pointf old_point,
                                             size_t target_crossings) {
  const pointf candidate = *point;
  double bad_fraction = 0.0;
  double good_fraction = 1.0;
  for (size_t i = 0; i < 12; i++) {
    const double fraction = (bad_fraction + good_fraction) / 2.0;
    point->x = candidate.x + fraction * (old_point.x - candidate.x);
    point->y = candidate.y + fraction * (old_point.y - candidate.y);
    if (edge_route_node_crossings(g, edge, edge_splines) <= target_crossings)
      good_fraction = fraction;
    else
      bad_fraction = fraction;
  }

  // Move halfway from the sampled boundary toward the known-safe old point.
  const double safe_fraction = (good_fraction + 1.0) / 2.0;
  point->x = candidate.x + safe_fraction * (old_point.x - candidate.x);
  point->y = candidate.y + safe_fraction * (old_point.y - candidate.y);
  size_t crossings = edge_route_node_crossings(g, edge, edge_splines);
  if (crossings > target_crossings) {
    *point = old_point;
    crossings = edge_route_node_crossings(g, edge, edge_splines);
  }
  assert(crossings <= target_crossings);
  return crossings;
}

static void restore_worsened_spline_units_locally(graph_t *g, edge_t *edge,
                                                  splines *edge_splines,
                                                  const pointf *old_points,
                                                  size_t crossings_before) {
  size_t crossings = edge_route_node_crossings(g, edge, edge_splines);
  bool *const restored =
      gv_calloc(count_spline_points(edge_splines), sizeof(*restored));

  while (crossings > crossings_before) {
    size_t best_spline = SIZE_MAX;
    size_t best_point = SIZE_MAX;
    size_t best_flat_point = SIZE_MAX;
    size_t best_crossings = crossings;
    double best_distance = HUGE_VAL;
    size_t fallback_spline = SIZE_MAX;
    size_t fallback_point = SIZE_MAX;
    size_t fallback_flat_point = SIZE_MAX;
    double fallback_distance = HUGE_VAL;
    size_t point_offset = 0;

    for (size_t i = 0; i < edge_splines->size; i++) {
      bezier *const spline = &edge_splines->list[i];
      for (size_t point_index = 1; point_index + 1 < spline->size;
           point_index++) {
        const size_t flat_point = point_offset + point_index;
        if (restored[flat_point])
          continue;

        const pointf candidate = spline->list[point_index];
        const pointf old = old_points[flat_point];
        if (candidate.x == old.x && candidate.y == old.y) {
          restored[flat_point] = true;
          continue;
        }

        const double distance = DIST(candidate, old);
        spline->list[point_index] = old;
        const size_t trial = edge_route_node_crossings(g, edge, edge_splines);
        spline->list[point_index] = candidate;

        if (trial < crossings &&
            (best_spline == SIZE_MAX || trial < best_crossings ||
             (trial == best_crossings && distance < best_distance))) {
          best_spline = i;
          best_point = point_index;
          best_flat_point = flat_point;
          best_crossings = trial;
          best_distance = distance;
        }
        if (distance < fallback_distance) {
          fallback_spline = i;
          fallback_point = point_index;
          fallback_flat_point = flat_point;
          fallback_distance = distance;
        }
      }
      point_offset += spline->size;
    }

    // If no point helps alone, take the smallest rollback; another point can
    // then complete the local combination on the next iteration.
    const bool have_improvement = best_spline != SIZE_MAX;
    const size_t chosen_spline =
        have_improvement ? best_spline : fallback_spline;
    const size_t chosen_point = have_improvement ? best_point : fallback_point;
    const size_t chosen_flat_point =
        have_improvement ? best_flat_point : fallback_flat_point;
    assert(chosen_spline != SIZE_MAX && chosen_point != SIZE_MAX &&
           chosen_flat_point != SIZE_MAX);
    pointf *const chosen =
        &edge_splines->list[chosen_spline].list[chosen_point];
    if (have_improvement) {
      crossings = partially_restore_spline_point(g, edge, edge_splines, chosen,
                                                 old_points[chosen_flat_point],
                                                 best_crossings);
    } else {
      *chosen = old_points[chosen_flat_point];
      restored[chosen_flat_point] = true;
      crossings = edge_route_node_crossings(g, edge, edge_splines);
    }
  }
  free(restored);
  assert(crossings <= crossings_before);
}

static void match_restored_spline_joint_derivatives(graph_t *g, edge_t *edge,
                                                    splines *edge_splines,
                                                    size_t target_crossings) {
  for (size_t i = 0; i + 1 < edge_splines->size; i++) {
    bezier *const left = &edge_splines->list[i];
    bezier *const right = &edge_splines->list[i + 1];
    if (left->size < 4 || right->size < 4)
      continue;

    const size_t left_last = left->size - 1;
    if (DIST(left->list[left_last], right->list[0]) > MILLIPOINT)
      continue;

    pointf *const left_control = &left->list[left_last - 1];
    pointf *const right_control = &right->list[1];
    const pointf saved_left = *left_control;
    const pointf saved_right = *right_control;
    const pointf joint = left->list[left_last];
    const pointf candidate_left = sub_pointf(scale(2.0, joint), saved_right);
    const pointf candidate_right = sub_pointf(scale(2.0, joint), saved_left);

    const bool try_left_first =
        DIST(saved_left, candidate_left) <= DIST(saved_right, candidate_right);
    if (try_left_first) {
      *left_control = candidate_left;
      if (edge_route_node_crossings(g, edge, edge_splines) <= target_crossings)
        continue;
      *left_control = saved_left;
      *right_control = candidate_right;
      if (edge_route_node_crossings(g, edge, edge_splines) <= target_crossings)
        continue;
      *right_control = saved_right;
    } else {
      *right_control = candidate_right;
      if (edge_route_node_crossings(g, edge, edge_splines) <= target_crossings)
        continue;
      *right_control = saved_right;
      *left_control = candidate_left;
      if (edge_route_node_crossings(g, edge, edge_splines) <= target_crossings)
        continue;
      *left_control = saved_left;
    }
  }
}

static void align_concentrated_route_tangents(graph_t *g, edge_t *edge) {
  const bool merged_tail = spline_merge(agtail(edge));
  const bool merged_head = spline_merge(aghead(edge));
  while (ED_to_orig(edge) != NULL && ED_edge_type(edge) != NORMAL)
    edge = ED_to_orig(edge);

  splines *const edge_splines = ED_spl(edge);
  assert(edge_splines != NULL && edge_splines->size > 0);
  bezier *const spline = &edge_splines->list[edge_splines->size - 1];
  if (spline->size < 4)
    return;

  edge_t *const main_edge = getmainedge(edge);
  const bool bidirectional_concentration =
      (edge_has_concentrated_arrow_decorations(edge) ||
       ED_conc_opp_flag(edge)) &&
      ND_rank(agtail(edge)) == ND_rank(aghead(edge));
  const bool smooth_route =
      Concentrate &&
      (merged_tail || merged_head || bidirectional_concentration ||
       ED_label(main_edge) != NULL || ED_head_label(main_edge) != NULL ||
       ED_tail_label(main_edge) != NULL ||
       has_grouped_flat_endpoint(main_edge));
  if (!smooth_route && !merged_tail && !merged_head &&
      !bidirectional_concentration)
    return;

  pointf *const saved_points = copy_spline_points(edge_splines);
  const size_t node_crossings_before =
      edge_route_node_crossings(g, main_edge, edge_splines);
  if (smooth_route)
    smooth_alternating_concentrated_controls(spline, main_edge);
  if (!merged_tail && !merged_head && !bidirectional_concentration) {
    if (edge_route_node_crossings(g, main_edge, edge_splines) >
        node_crossings_before)
      restore_worsened_spline_units_locally(
          g, main_edge, edge_splines, saved_points, node_crossings_before);
    free(saved_points);
    return;
  }

  const size_t first = 0;
  const size_t last = spline->size - 1;
  const pointf start = spline->list[first];
  const pointf end = spline->list[last];
  if (merged_tail || bidirectional_concentration)
    point_control_arm_at(&spline->list[first + 1], start, end);
  if (merged_head || bidirectional_concentration)
    point_control_arm_at(&spline->list[last - 1], end, start);
  if (Concentrate && (merged_tail || merged_head) && edge_splines->size > 1)
    smooth_consecutive_spline_joints(edge_splines);

  if (edge_route_node_crossings(g, main_edge, edge_splines) >
      node_crossings_before) {
    restore_worsened_spline_units_locally(g, main_edge, edge_splines,
                                          saved_points, node_crossings_before);
    match_restored_spline_joint_derivatives(g, main_edge, edge_splines,
                                            node_crossings_before);
  }
  free(saved_points);
  for (size_t i = 0; i + 3 < spline->size; i += 3)
    update_bb_bz(&GD_bb(g), &spline->list[i]);
}

typedef struct {
  edge_t *edge;
  bezier *spline;
  size_t spline_index;
  size_t endpoint;
  size_t control;
  pointf vector;
  double length;
} junction_arm_t;

static bool terminal_spline_arm(edge_t *edge, pointf junction,
                                junction_arm_t *arm) {
  while (ED_to_orig(edge) != NULL && ED_edge_type(edge) != NORMAL)
    edge = ED_to_orig(edge);
  if (ED_spl(edge) == NULL)
    return false;

  double best = 1.0;
  bool found = false;
  for (size_t i = 0; i < ED_spl(edge)->size; i++) {
    bezier *const spline = &ED_spl(edge)->list[i];
    if (spline->size < 4)
      continue;

    const size_t last = spline->size - 1;
    const double start_distance = DIST(spline->list[0], junction);
    const double end_distance = DIST(spline->list[last], junction);
    size_t endpoint;
    size_t control;
    double distance;
    if (start_distance <= end_distance) {
      endpoint = 0;
      control = 1;
      distance = start_distance;
    } else {
      endpoint = last;
      control = last - 1;
      distance = end_distance;
    }
    if (distance > best)
      continue;

    const pointf vector =
        sub_pointf(spline->list[control], spline->list[endpoint]);
    const double length = hypot(vector.x, vector.y);
    if (length <= MILLIPOINT)
      continue;

    *arm = (junction_arm_t){.edge = edge,
                            .spline = spline,
                            .spline_index = i,
                            .endpoint = endpoint,
                            .control = control,
                            .vector = vector,
                            .length = length};
    best = distance;
    found = true;
  }
  return found;
}

static bool same_junction_arm(const junction_arm_t *a,
                              const junction_arm_t *b) {
  return a->spline == b->spline && a->endpoint == b->endpoint;
}

static size_t collect_junction_arms(edge_t **edges, int count, pointf junction,
                                    junction_arm_t *arms, size_t arm_count) {
  for (int i = 0; i < count; i++) {
    junction_arm_t arm;
    if (!terminal_spline_arm(edges[i], junction, &arm))
      continue;

    bool seen = false;
    for (size_t j = 0; j < arm_count; j++) {
      if (same_junction_arm(&arms[j], &arm)) {
        seen = true;
        break;
      }
    }
    if (!seen)
      arms[arm_count++] = arm;
  }
  return arm_count;
}

static bool mean_unit_vector(const junction_arm_t *arms, size_t arm_count,
                             pointf *mean) {
  *mean = (pointf){0};
  for (size_t i = 0; i < arm_count; i++) {
    mean->x += arms[i].vector.x / arms[i].length;
    mean->y += arms[i].vector.y / arms[i].length;
  }

  const double length = hypot(mean->x, mean->y);
  if (length <= MILLIPOINT)
    return false;

  mean->x /= length;
  mean->y /= length;
  return true;
}

static bool junction_arm_g1_residual(const junction_arm_t *arm, pointf control,
                                     double *residual) {
  const splines *const edge_splines = ED_spl(arm->edge);
  if (edge_splines == NULL || arm->spline_index >= edge_splines->size ||
      &edge_splines->list[arm->spline_index] != arm->spline)
    return false;

  const pointf joint = arm->spline->list[arm->endpoint];
  pointf incoming;
  pointf outgoing;
  if (arm->endpoint == 0) {
    if (arm->spline_index == 0)
      return false;
    const bezier *const left = &edge_splines->list[arm->spline_index - 1];
    if (left->size < 4 || DIST(left->list[left->size - 1], joint) > MILLIPOINT)
      return false;
    incoming = sub_pointf(joint, left->list[left->size - 2]);
    outgoing = sub_pointf(control, joint);
  } else {
    if (arm->endpoint + 1 != arm->spline->size ||
        arm->spline_index + 1 >= edge_splines->size)
      return false;
    const bezier *const right = &edge_splines->list[arm->spline_index + 1];
    if (right->size < 4 || DIST(right->list[0], joint) > MILLIPOINT)
      return false;
    incoming = sub_pointf(joint, control);
    outgoing = sub_pointf(right->list[1], joint);
  }

  const double incoming_length = hypot(incoming.x, incoming.y);
  const double outgoing_length = hypot(outgoing.x, outgoing.y);
  if (!isfinite(incoming_length) || !isfinite(outgoing_length) ||
      incoming_length <= MILLIPOINT || outgoing_length <= MILLIPOINT)
    return false;
  const double cross = fabs(incoming.x * outgoing.y - incoming.y * outgoing.x);
  const double product = incoming.x * outgoing.x + incoming.y * outgoing.y;
  *residual = atan2(cross, product);
  return isfinite(*residual);
}

static bool junction_arm_seam(const junction_arm_t *arm,
                              route_junction_t *junction) {
  const splines *const edge_splines = ED_spl(arm->edge);
  if (edge_splines == NULL || arm->spline_index >= edge_splines->size ||
      &edge_splines->list[arm->spline_index] != arm->spline)
    return false;

  size_t left_spline;
  size_t right_spline;
  if (arm->endpoint == 0) {
    if (arm->spline_index == 0)
      return false;
    left_spline = arm->spline_index - 1;
    right_spline = arm->spline_index;
  } else {
    if (arm->endpoint + 1 != arm->spline->size ||
        arm->spline_index + 1 >= edge_splines->size)
      return false;
    left_spline = arm->spline_index;
    right_spline = arm->spline_index + 1;
  }

  const bezier *const left = &edge_splines->list[left_spline];
  const bezier *const right = &edge_splines->list[right_spline];
  if (left->size < 4 || right->size < 4 || left->size % 3 != 1 ||
      right->size % 3 != 1 ||
      DIST(left->list[left->size - 1], right->list[0]) > MILLIPOINT)
    return false;

  *junction = (route_junction_t){.edge = arm->edge,
                                 .joint = right->list[0],
                                 .left_spline = left_spline,
                                 .left_cubic = (left->size - 4) / 3,
                                 .right_spline = right_spline,
                                 .right_cubic = 0};
  return true;
}

static void align_trunk_to_arms(graph_t *g, junction_arm_t *trunk,
                                const junction_arm_t *arms, size_t arm_count) {
  pointf mean;
  if (!mean_unit_vector(arms, arm_count, &mean))
    return;

  const pointf continuity_candidate = trunk->spline->list[trunk->control];
  const pointf baseline_control = add_pointf(
      trunk->spline->list[trunk->endpoint], scale(-trunk->length, mean));
  trunk->spline->list[trunk->control] = baseline_control;

  double baseline_residual;
  double candidate_residual;
  route_junction_t junction;
  if (junction_arm_g1_residual(trunk, baseline_control, &baseline_residual) &&
      junction_arm_g1_residual(trunk, continuity_candidate,
                               &candidate_residual) &&
      candidate_residual <= ROUTE_G1_RESIDUAL_TOLERANCE &&
      candidate_residual + ROUTE_G1_RESIDUAL_TOLERANCE < baseline_residual &&
      junction_arm_seam(trunk, &junction)) {
    route_crossings_t baseline_crossings = {0};
    route_crossings_t candidate_crossings = {0};
    const bool baseline_signature_valid =
        route_graph_crossing_signature(g, &junction, &baseline_crossings);
    if (baseline_signature_valid) {
      trunk->spline->list[trunk->control] = continuity_candidate;
      const splines *const edge_splines = ED_spl(junction.edge);
      const bezier *const left = &edge_splines->list[junction.left_spline];
      const bezier *const right = &edge_splines->list[junction.right_spline];
      const bool valid =
          route_graph_crossing_signature(g, &junction, &candidate_crossings) &&
          route_crossing_signatures_equal(&baseline_crossings,
                                          &candidate_crossings) &&
          route_cubic_clears_nodes(g, junction.edge,
                                   &left->list[left->size - 4]) &&
          route_cubic_clears_nodes(g, junction.edge, &right->list[0]);
      if (!valid)
        trunk->spline->list[trunk->control] = baseline_control;
    }
    LIST_FREE(&baseline_crossings);
    LIST_FREE(&candidate_crossings);
  }

  for (size_t i = 0; i + 3 < trunk->spline->size; i += 3)
    update_bb_bz(&GD_bb(g), &trunk->spline->list[i]);
}

static void align_concentrated_junction_trunk(graph_t *g, node_t *node) {
  const int incoming_count = ND_in(node).size;
  const int outgoing_count = ND_out(node).size;
  if (!((incoming_count == 1 && outgoing_count >= 2) ||
        (outgoing_count == 1 && incoming_count >= 2)))
    return;

  const size_t max_arms = (size_t)incoming_count + (size_t)outgoing_count;
  junction_arm_t *const incoming = gv_calloc(max_arms, sizeof(junction_arm_t));
  junction_arm_t *const outgoing = gv_calloc(max_arms, sizeof(junction_arm_t));
  const pointf junction = ND_coord(node);
  const size_t incoming_arms = collect_junction_arms(
      ND_in(node).list, incoming_count, junction, incoming, 0);
  const size_t outgoing_arms = collect_junction_arms(
      ND_out(node).list, outgoing_count, junction, outgoing, 0);

  if (incoming_arms == 1 && outgoing_arms >= 2)
    align_trunk_to_arms(g, &incoming[0], outgoing, outgoing_arms);
  else if (outgoing_arms == 1 && incoming_arms >= 2)
    align_trunk_to_arms(g, &outgoing[0], incoming, incoming_arms);

  free(incoming);
  free(outgoing);
}

static void align_concentrated_junction_trunks(graph_t *g) {
  if (!Concentrate)
    return;

  for (int rank = GD_minrank(g); rank <= GD_maxrank(g); rank++) {
    for (int i = 0; i < GD_rank(g)[rank].n; i++) {
      node_t *const node = GD_rank(g)[rank].v[i];
      if (spline_merge(node))
        align_concentrated_junction_trunk(g, node);
    }
  }
}

static bool bezier_intersects_box(const pointf control[4], boxf obstacle) {
  boxf control_box = {.LL = control[0], .UR = control[0]};
  for (size_t i = 1; i < 4; i++)
    expandbp(&control_box, control[i]);
  if (!boxf_overlap(control_box, obstacle))
    return false;

  const double flatness_squared =
      MULTIEDGE_BEZIER_FLATNESS * MULTIEDGE_BEZIER_FLATNESS;
  if (ptToLine2(control[0], control[3], control[1]) <= flatness_squared &&
      ptToLine2(control[0], control[3], control[2]) <= flatness_squared)
    return lineToBox(control[0], control[3], obstacle) != -1;

  pointf left[4];
  pointf right[4];
  Bezier(control, 0.5, left, right);
  return bezier_intersects_box(left, obstacle) ||
         bezier_intersects_box(right, obstacle);
}

static boxf route_cubic_bounds(const pointf control[4]) {
  boxf bounds = {.LL = control[0], .UR = control[0]};
  for (size_t i = 1; i < 4; i++)
    expandbp(&bounds, control[i]);
  return bounds;
}

static double route_point_segment_distance_squared(pointf query, pointf a,
                                                   pointf b) {
  const pointf direction = sub_pointf(b, a);
  const double length_squared =
      direction.x * direction.x + direction.y * direction.y;
  if (length_squared <= 1e-24)
    return DIST2(query, a);
  const pointf offset = sub_pointf(query, a);
  const double t =
      MAX(0.0, MIN(1.0, (offset.x * direction.x + offset.y * direction.y) /
                            length_squared));
  const pointf closest = add_pointf(a, scale(t, direction));
  return DIST2(query, closest);
}

#define ROUTE_VALIDATION_FLATNESS 1e-2
#define ROUTE_VALIDATION_MAX_DEPTH 20

static bool flatten_route_cubic_recursive(const pointf control[4], double t0,
                                          double t1, unsigned depth,
                                          route_flat_points_t *points) {
  const double error_squared = MAX(
      route_point_segment_distance_squared(control[1], control[0], control[3]),
      route_point_segment_distance_squared(control[2], control[0], control[3]));
  if (error_squared <= ROUTE_VALIDATION_FLATNESS * ROUTE_VALIDATION_FLATNESS) {
    LIST_APPEND(points, ((route_flat_point_t){.point = control[3],
                                              .t = t1,
                                              .error = sqrt(error_squared)}));
    return true;
  }
  if (depth == ROUTE_VALIDATION_MAX_DEPTH)
    return false;

  pointf left[4];
  pointf right[4];
  Bezier(control, 0.5, left, right);
  const double middle = (t0 + t1) / 2.0;
  return flatten_route_cubic_recursive(left, t0, middle, depth + 1, points) &&
         flatten_route_cubic_recursive(right, middle, t1, depth + 1, points);
}

static bool flatten_route_cubic(const pointf control[4],
                                route_flat_points_t *points) {
  LIST_APPEND(points, ((route_flat_point_t){
                          .point = control[0], .t = 0.0, .error = 0.0}));
  return flatten_route_cubic_recursive(control, 0.0, 1.0, 0, points);
}

static double route_cross_product(pointf a, pointf b) {
  return a.x * b.y - a.y * b.x;
}

static double route_segments_distance_squared(pointf a, pointf b, pointf c,
                                              pointf d) {
  return MIN(MIN(route_point_segment_distance_squared(a, c, d),
                 route_point_segment_distance_squared(b, c, d)),
             MIN(route_point_segment_distance_squared(c, a, b),
                 route_point_segment_distance_squared(d, a, b)));
}

static route_segment_intersection_t
route_segments_intersect(pointf a, pointf b, double ab_error, pointf c,
                         pointf d, double cd_error, double *along_ab,
                         double *along_cd) {
  const pointf ab = sub_pointf(b, a);
  const pointf cd = sub_pointf(d, c);
  const double ab_squared = ab.x * ab.x + ab.y * ab.y;
  const double cd_squared = cd.x * cd.x + cd.y * cd.y;
  const double uncertainty = ab_error + cd_error + 1e-9;
  if (ab_squared <= 1e-18 || cd_squared <= 1e-18)
    return route_segments_distance_squared(a, b, c, d) <=
                   uncertainty * uncertainty
               ? ROUTE_SEGMENT_AMBIGUOUS
               : ROUTE_SEGMENT_DISJOINT;

  const double denominator = route_cross_product(ab, cd);
  const pointf ac = sub_pointf(c, a);
  const double parallel_tolerance =
      1e-10 * sqrt(ab_squared * cd_squared) + 1e-12;
  if (fabs(denominator) <= parallel_tolerance)
    return route_segments_distance_squared(a, b, c, d) <=
                   uncertainty * uncertainty
               ? ROUTE_SEGMENT_AMBIGUOUS
               : ROUTE_SEGMENT_DISJOINT;

  const double t = route_cross_product(ac, cd) / denominator;
  const double u = route_cross_product(ac, ab) / denominator;
  if (t < 0.0 || t > 1.0 || u < 0.0 || u > 1.0) {
    return route_segments_distance_squared(a, b, c, d) <=
                   uncertainty * uncertainty
               ? ROUTE_SEGMENT_AMBIGUOUS
               : ROUTE_SEGMENT_DISJOINT;
  }
  const double ab_length = sqrt(ab_squared);
  const double cd_length = sqrt(cd_squared);
  const double sine = fabs(denominator) / (ab_length * cd_length);
  const double endpoint_margin = uncertainty / sine;
  if (MIN(t, 1.0 - t) * ab_length <= endpoint_margin ||
      MIN(u, 1.0 - u) * cd_length <= endpoint_margin)
    return ROUTE_SEGMENT_AMBIGUOUS;
  *along_ab = MIN(1.0, MAX(0.0, t));
  *along_cd = MIN(1.0, MAX(0.0, u));
  return ROUTE_SEGMENT_INTERSECTION;
}

static void append_unique_route_crossing(route_crossings_t *crossings,
                                         route_crossing_t crossing) {
  for (size_t i = 0; i < LIST_SIZE(crossings); i++) {
    const route_crossing_t existing = LIST_GET(crossings, i);
    if (existing.affected_cubic == crossing.affected_cubic &&
        existing.other_edge == crossing.other_edge &&
        existing.other_spline == crossing.other_spline &&
        existing.other_cubic == crossing.other_cubic &&
        fabs(existing.affected_t - crossing.affected_t) <= 1e-6 &&
        fabs(existing.other_t - crossing.other_t) <= 1e-6)
      return;
  }
  LIST_APPEND(crossings, crossing);
}

static bool route_flat_segments_share_curve_endpoint(route_flat_point_t a,
                                                     route_flat_point_t b,
                                                     route_flat_point_t c,
                                                     route_flat_point_t d) {
  const route_flat_point_t first_endpoints[2] = {a, b};
  const route_flat_point_t second_endpoints[2] = {c, d};
  for (size_t first = 0; first < 2; first++) {
    if (first_endpoints[first].t != 0.0 && first_endpoints[first].t != 1.0)
      continue;
    for (size_t second = 0; second < 2; second++) {
      if (second_endpoints[second].t != 0.0 &&
          second_endpoints[second].t != 1.0)
        continue;
      if (DIST2(first_endpoints[first].point, second_endpoints[second].point) <=
          1e-18)
        return true;
    }
  }
  return false;
}

static bool append_route_crossings(const route_flat_points_t *affected,
                                   const route_flat_points_t *other,
                                   size_t affected_cubic, size_t other_edge,
                                   size_t other_spline, size_t other_cubic,
                                   route_crossings_t *crossings) {
  for (size_t i = 0; i + 1 < LIST_SIZE(affected); i++) {
    const route_flat_point_t a = LIST_GET(affected, i);
    const route_flat_point_t b = LIST_GET(affected, i + 1);
    for (size_t j = 0; j + 1 < LIST_SIZE(other); j++) {
      const route_flat_point_t c = LIST_GET(other, j);
      const route_flat_point_t d = LIST_GET(other, j + 1);
      const double uncertainty = b.error + d.error + 1e-9;
      if (MAX(a.point.x, b.point.x) + uncertainty < MIN(c.point.x, d.point.x) ||
          MAX(c.point.x, d.point.x) + uncertainty < MIN(a.point.x, b.point.x) ||
          MAX(a.point.y, b.point.y) + uncertainty < MIN(c.point.y, d.point.y) ||
          MAX(c.point.y, d.point.y) + uncertainty < MIN(a.point.y, b.point.y))
        continue;
      if (route_flat_segments_share_curve_endpoint(a, b, c, d))
        continue;
      double affected_segment_t;
      double other_segment_t;
      const route_segment_intersection_t intersection =
          route_segments_intersect(a.point, b.point, b.error, c.point, d.point,
                                   d.error, &affected_segment_t,
                                   &other_segment_t);
      if (intersection == ROUTE_SEGMENT_AMBIGUOUS)
        return false;
      if (intersection == ROUTE_SEGMENT_DISJOINT)
        continue;
      const double affected_t = a.t + affected_segment_t * (b.t - a.t);
      if (affected_t <= 1e-6 || affected_t >= 1.0 - 1e-6)
        continue;
      append_unique_route_crossing(
          crossings,
          (route_crossing_t){.affected_cubic = affected_cubic,
                             .affected_t = affected_t,
                             .other_edge = other_edge,
                             .other_spline = other_spline,
                             .other_cubic = other_cubic,
                             .other_t = c.t + other_segment_t * (d.t - c.t)});
    }
  }
  return true;
}

static bool append_route_segment_crossings(const route_flat_points_t *affected,
                                           pointf a, pointf b,
                                           size_t affected_cubic,
                                           size_t segment_id,
                                           route_crossings_t *crossings) {
  for (size_t i = 0; i + 1 < LIST_SIZE(affected); i++) {
    const route_flat_point_t p = LIST_GET(affected, i);
    const route_flat_point_t q = LIST_GET(affected, i + 1);
    if (((p.t == 0.0 &&
          route_point_segment_distance_squared(p.point, a, b) <= 1e-18) ||
         (q.t == 1.0 &&
          route_point_segment_distance_squared(q.point, a, b) <= 1e-18)))
      continue;
    double affected_segment_t;
    double other_segment_t;
    const route_segment_intersection_t intersection =
        route_segments_intersect(p.point, q.point, q.error, a, b, 0.0,
                                 &affected_segment_t, &other_segment_t);
    if (intersection == ROUTE_SEGMENT_AMBIGUOUS)
      return false;
    if (intersection == ROUTE_SEGMENT_DISJOINT)
      continue;
    const double affected_t = p.t + affected_segment_t * (q.t - p.t);
    if (affected_t <= 1e-6 || affected_t >= 1.0 - 1e-6)
      continue;
    append_unique_route_crossing(
        crossings, (route_crossing_t){.affected_cubic = affected_cubic,
                                      .affected_t = affected_t,
                                      .other_edge = segment_id,
                                      .other_t = other_segment_t});
  }
  return true;
}

static int compare_route_crossings(const void *a, const void *b) {
  const route_crossing_t *const left = a;
  const route_crossing_t *const right = b;
#define ROUTE_COMPARE_FIELD(field)                                             \
  do {                                                                         \
    if (left->field < right->field)                                            \
      return -1;                                                               \
    if (left->field > right->field)                                            \
      return 1;                                                                \
  } while (0)
  ROUTE_COMPARE_FIELD(affected_cubic);
  if (left->affected_t < right->affected_t)
    return -1;
  if (left->affected_t > right->affected_t)
    return 1;
  ROUTE_COMPARE_FIELD(other_edge);
  ROUTE_COMPARE_FIELD(other_spline);
  ROUTE_COMPARE_FIELD(other_cubic);
  if (left->other_t < right->other_t)
    return -1;
  if (left->other_t > right->other_t)
    return 1;
#undef ROUTE_COMPARE_FIELD
  return 0;
}

static void sort_route_crossings(route_crossings_t *crossings) {
  if (LIST_SIZE(crossings) > 1) {
    qsort(LIST_FRONT(crossings), LIST_SIZE(crossings), sizeof(route_crossing_t),
          compare_route_crossings);
  }
}

static bool route_crossing_signatures_equal(const route_crossings_t *left,
                                            const route_crossings_t *right) {
  if (LIST_SIZE(left) != LIST_SIZE(right))
    return false;
  for (size_t i = 0; i < LIST_SIZE(left); i++) {
    const route_crossing_t a = LIST_GET(left, i);
    const route_crossing_t b = LIST_GET(right, i);
    if (a.affected_cubic != b.affected_cubic || a.other_edge != b.other_edge ||
        a.other_spline != b.other_spline || a.other_cubic != b.other_cubic)
      return false;
  }
  for (size_t i = 0; i < LIST_SIZE(left); i++) {
    for (size_t j = i + 1; j < LIST_SIZE(left); j++) {
      const route_crossing_t a = LIST_GET(left, i);
      const route_crossing_t b = LIST_GET(left, j);
      const route_crossing_t c = LIST_GET(right, i);
      const route_crossing_t d = LIST_GET(right, j);
      if (a.other_edge != b.other_edge || a.other_spline != b.other_spline ||
          a.other_cubic != b.other_cubic)
        continue;
      const double original_order = a.other_t - b.other_t;
      const double candidate_order = c.other_t - d.other_t;
      if (original_order * candidate_order < -1e-12)
        return false;
    }
  }
  return true;
}

static bool route_graph_crossing_signature(graph_t *graph,
                                           const route_junction_t *junction,
                                           route_crossings_t *crossings) {
  splines *const affected_splines = ED_spl(junction->edge);
  bezier *const left = &affected_splines->list[junction->left_spline];
  bezier *const right = &affected_splines->list[junction->right_spline];
  const pointf *const affected_controls[2] = {&left->list[left->size - 4],
                                              &right->list[0]};
  const size_t affected_spline_indices[2] = {junction->left_spline,
                                             junction->right_spline};
  const size_t affected_cubic_indices[2] = {junction->left_cubic,
                                            junction->right_cubic};

  for (size_t affected_index = 0; affected_index < 2; affected_index++) {
    route_flat_points_t affected = {0};
    if (!flatten_route_cubic(affected_controls[affected_index], &affected)) {
      LIST_FREE(&affected);
      return false;
    }
    const boxf affected_bounds =
        route_cubic_bounds(affected_controls[affected_index]);
    size_t edge_index = 0;
    for (node_t *node = agfstnode(graph); node != NULL;
         node = agnxtnode(graph, node)) {
      for (edge_t *edge = agfstout(graph, node); edge != NULL;
           edge = agnxtout(graph, edge), edge_index++) {
        const splines *const edge_splines = ED_spl(edge);
        if (edge_splines == NULL)
          continue;
        for (size_t spline_index = 0; spline_index < edge_splines->size;
             spline_index++) {
          const bezier *const spline = &edge_splines->list[spline_index];
          for (size_t cubic_start = 0; cubic_start + 3 < spline->size;
               cubic_start += 3) {
            const pointf *const other_control = &spline->list[cubic_start];
            const size_t other_cubic = cubic_start / 3;
            const bool same_route = edge_splines == affected_splines;
            const bool adjacent_in_spline =
                same_route &&
                spline_index == affected_spline_indices[affected_index] &&
                (other_cubic + 1 == affected_cubic_indices[affected_index] ||
                 affected_cubic_indices[affected_index] + 1 == other_cubic);
            // The two cubics intentionally meet at this tagged seam. Their
            // shared endpoint is not an edge crossing; G1 is checked
            // separately below. Including this pair turns the endpoint touch
            // into a quadratic segment-pair scan without adding a crossing
            // invariant.
            const bool artificial_joint_neighbor =
                same_route && ((affected_index == 0 &&
                                spline_index == junction->right_spline &&
                                other_cubic == junction->right_cubic) ||
                               (affected_index == 1 &&
                                spline_index == junction->left_spline &&
                                other_cubic == junction->left_cubic));
            if (other_control == affected_controls[affected_index] ||
                adjacent_in_spline || artificial_joint_neighbor ||
                !boxf_overlap(affected_bounds,
                              route_cubic_bounds(other_control)))
              continue;
            route_flat_points_t other = {0};
            if (!flatten_route_cubic(other_control, &other)) {
              LIST_FREE(&other);
              LIST_FREE(&affected);
              return false;
            }
            const bool unambiguous = append_route_crossings(
                &affected, &other, affected_index, edge_index, spline_index,
                other_cubic, crossings);
            LIST_FREE(&other);
            if (!unambiguous) {
              LIST_FREE(&affected);
              return false;
            }
          }
        }
      }
    }
    LIST_FREE(&affected);
  }
  sort_route_crossings(crossings);
  return true;
}

static bool route_barrier_signature(const route_junction_t *junction,
                                    const route_pieces_t *pieces,
                                    route_crossings_t *crossings) {
  if (junction->left_piece >= LIST_SIZE(pieces) ||
      junction->right_piece >= LIST_SIZE(pieces))
    return false;
  const route_piece_metadata_t *const left_piece =
      LIST_AT(pieces, junction->left_piece);
  const route_piece_metadata_t *const right_piece =
      LIST_AT(pieces, junction->right_piece);
  const route_spline_metadata_t *const routes[2] = {&left_piece->end.route,
                                                    &right_piece->start.route};
  splines *const affected_splines = ED_spl(junction->edge);
  bezier *const left = &affected_splines->list[junction->left_spline];
  bezier *const right = &affected_splines->list[junction->right_spline];
  const pointf *const affected_controls[2] = {&left->list[left->size - 4],
                                              &right->list[0]};

  for (size_t affected_index = 0; affected_index < 2; affected_index++) {
    route_flat_points_t affected = {0};
    if (!flatten_route_cubic(affected_controls[affected_index], &affected)) {
      LIST_FREE(&affected);
      return false;
    }
    if (!route_spline_metadata_valid(routes[affected_index])) {
      LIST_FREE(&affected);
      return false;
    }
    for (size_t barrier_index = 0;
         barrier_index < routes[affected_index]->barrier_count;
         barrier_index++) {
      const Pedge_t barrier = routes[affected_index]->barriers[barrier_index];
      if (!append_route_segment_crossings(&affected, barrier.a, barrier.b,
                                          affected_index, barrier_index,
                                          crossings)) {
        LIST_FREE(&affected);
        return false;
      }
    }
    LIST_FREE(&affected);
  }
  sort_route_crossings(crossings);
  return true;
}

static bool route_corridor_signature(const route_junction_t *junction,
                                     const route_pieces_t *pieces,
                                     route_crossings_t *crossings) {
  if (junction->left_piece >= LIST_SIZE(pieces) ||
      junction->right_piece >= LIST_SIZE(pieces))
    return false;
  const route_piece_metadata_t *const left_piece =
      LIST_AT(pieces, junction->left_piece);
  const route_piece_metadata_t *const right_piece =
      LIST_AT(pieces, junction->right_piece);
  const route_spline_metadata_t *const routes[2] = {&left_piece->end.route,
                                                    &right_piece->start.route};
  splines *const affected_splines = ED_spl(junction->edge);
  bezier *const left = &affected_splines->list[junction->left_spline];
  bezier *const right = &affected_splines->list[junction->right_spline];
  const pointf *const affected_controls[2] = {&left->list[left->size - 4],
                                              &right->list[0]};

  for (size_t affected_index = 0; affected_index < 2; affected_index++) {
    if (!route_spline_metadata_valid(routes[affected_index]))
      return false;
    route_flat_points_t affected = {0};
    if (!flatten_route_cubic(affected_controls[affected_index], &affected)) {
      LIST_FREE(&affected);
      return false;
    }
    for (size_t portal_index = 0;
         portal_index < routes[affected_index]->portal_count; portal_index++) {
      const Pedge_t portal = routes[affected_index]->portals[portal_index];
      if (!append_route_segment_crossings(&affected, portal.a, portal.b,
                                          affected_index, portal_index,
                                          crossings)) {
        LIST_FREE(&affected);
        return false;
      }
    }
    LIST_FREE(&affected);
  }
  sort_route_crossings(crossings);
  return true;
}

static bool route_barrier_side_signature(const route_junction_t *junction,
                                         const route_pieces_t *pieces,
                                         route_sides_t *sides) {
  if (junction->left_piece >= LIST_SIZE(pieces) ||
      junction->right_piece >= LIST_SIZE(pieces))
    return false;
  const route_piece_metadata_t *const left_piece =
      LIST_AT(pieces, junction->left_piece);
  const route_piece_metadata_t *const right_piece =
      LIST_AT(pieces, junction->right_piece);
  const route_spline_metadata_t *const routes[2] = {&left_piece->end.route,
                                                    &right_piece->start.route};
  splines *const affected_splines = ED_spl(junction->edge);
  bezier *const left = &affected_splines->list[junction->left_spline];
  bezier *const right = &affected_splines->list[junction->right_spline];
  const pointf *const affected_controls[2] = {&left->list[left->size - 4],
                                              &right->list[0]};

  for (size_t affected_index = 0; affected_index < 2; affected_index++) {
    if (!route_spline_metadata_valid(routes[affected_index]))
      return false;
    pointf first_half[4];
    pointf second_half[4];
    Bezier(affected_controls[affected_index], 0.5, first_half, second_half);
    const pointf midpoint = first_half[3];
    for (size_t barrier_index = 0;
         barrier_index < routes[affected_index]->barrier_count;
         barrier_index++) {
      const Pedge_t barrier = routes[affected_index]->barriers[barrier_index];
      const pointf direction = sub_pointf(barrier.b, barrier.a);
      const pointf offset = sub_pointf(midpoint, barrier.a);
      const double orientation = route_cross_product(direction, offset);
      const double tolerance = 1e-9 *
                               MAX(1.0, hypot(direction.x, direction.y)) *
                               MAX(1.0, hypot(offset.x, offset.y));
      LIST_APPEND(sides, orientation > tolerance    ? 1
                         : orientation < -tolerance ? -1
                                                    : 0);
    }
  }
  return true;
}

static bool route_side_signatures_equal(const route_sides_t *left,
                                        const route_sides_t *right) {
  if (LIST_SIZE(left) != LIST_SIZE(right))
    return false;
  for (size_t i = 0; i < LIST_SIZE(left); i++) {
    if (LIST_GET(left, i) != LIST_GET(right, i))
      return false;
  }
  return true;
}

static route_bezier_invariants_t route_bezier_invariants(const bezier *spline) {
  assert(spline->size > 0);
  return (route_bezier_invariants_t){
      .size = spline->size,
      .start = spline->list[0],
      .end = spline->list[spline->size - 1],
      .sflag = spline->sflag,
      .eflag = spline->eflag,
      .suppress_sflag = spline->suppress_sflag,
      .suppress_eflag = spline->suppress_eflag,
      .sp = spline->sp,
      .ep = spline->ep,
  };
}

static bool route_bezier_invariants_equal(route_bezier_invariants_t expected,
                                          const bezier *actual) {
  return expected.size == actual->size && actual->size % 3 == 1 &&
         memcmp(&expected.start, &actual->list[0], sizeof(pointf)) == 0 &&
         memcmp(&expected.end, &actual->list[actual->size - 1],
                sizeof(pointf)) == 0 &&
         expected.sflag == actual->sflag && expected.eflag == actual->eflag &&
         expected.suppress_sflag == actual->suppress_sflag &&
         expected.suppress_eflag == actual->suppress_eflag &&
         memcmp(&expected.sp, &actual->sp, sizeof(pointf)) == 0 &&
         memcmp(&expected.ep, &actual->ep, sizeof(pointf)) == 0;
}

static double route_barrier_width(boxf barrier, pointf tangent) {
  const double width = MAX(0.0, barrier.UR.x - barrier.LL.x);
  const double height = MAX(0.0, barrier.UR.y - barrier.LL.y);
  return fabs(tangent.y) * width + fabs(tangent.x) * height;
}

static bool route_ray_box_limit(pointf origin, pointf direction, boxf bounds,
                                double *limit) {
  const double tolerance = MILLIPOINT;
  if (origin.x < bounds.LL.x - tolerance ||
      origin.x > bounds.UR.x + tolerance ||
      origin.y < bounds.LL.y - tolerance || origin.y > bounds.UR.y + tolerance)
    return false;

  *limit = DBL_MAX;
  if (direction.x > 1e-12)
    *limit = MIN(*limit, (bounds.UR.x - origin.x) / direction.x);
  else if (direction.x < -1e-12)
    *limit = MIN(*limit, (bounds.LL.x - origin.x) / direction.x);
  if (direction.y > 1e-12)
    *limit = MIN(*limit, (bounds.UR.y - origin.y) / direction.y);
  else if (direction.y < -1e-12)
    *limit = MIN(*limit, (bounds.LL.y - origin.y) / direction.y);
  return isfinite(*limit) && *limit >= 0.0;
}

static bool route_ray_trust_interval(pointf origin, pointf direction,
                                     pointf original, double radius,
                                     double *minimum, double *maximum) {
  const pointf delta = sub_pointf(original, origin);
  const double projection = delta.x * direction.x + delta.y * direction.y;
  const double perpendicular_squared =
      MAX(0.0, delta.x * delta.x + delta.y * delta.y - projection * projection);
  if (perpendicular_squared > radius * radius)
    return false;
  const double span = sqrt(MAX(0.0, radius * radius - perpendicular_squared));
  *minimum = MAX(0.0, projection - span);
  *maximum = projection + span;
  return *maximum >= 0.0;
}

static void include_route_scale(double value, double *scale) {
  if (isfinite(value) && value > MILLIPOINT)
    *scale = MIN(*scale, value);
}

static bool fixed_template_g1_candidate(const route_junction_t *junction,
                                        pointf *left_control,
                                        pointf *right_control) {
  const splines *const edge_splines = ED_spl(junction->edge);
  const bezier *const left = &edge_splines->list[junction->left_spline];
  const bezier *const right = &edge_splines->list[junction->right_spline];
  const pointf *const left_cubic = &left->list[left->size - 4];
  const pointf *const right_cubic = &right->list[0];
  const double left_chord = DIST(left_cubic[0], left_cubic[3]);
  const double right_chord = DIST(right_cubic[0], right_cubic[3]);
  const double left_length = DIST(left_cubic[2], junction->joint);
  const double right_length = DIST(right_cubic[1], junction->joint);

  double left_scale = DBL_MAX;
  double right_scale = DBL_MAX;
  include_route_scale(left_chord, &left_scale);
  include_route_scale(
      route_barrier_width(junction->local_barriers[0], junction->t_ref),
      &left_scale);
  include_route_scale(right_chord, &right_scale);
  include_route_scale(
      route_barrier_width(junction->local_barriers[1], junction->t_ref),
      &right_scale);
  if (left_scale == DBL_MAX || right_scale == DBL_MAX)
    return false;

  const double left_length_floor = MAX(2 * MILLIPOINT, left_scale * 1e-3);
  const double right_length_floor = MAX(2 * MILLIPOINT, right_scale * 1e-3);
  const double left_trust_radius = MAX(4 * MILLIPOINT, left_scale * 0.75);
  const double right_trust_radius = MAX(4 * MILLIPOINT, right_scale * 0.75);
  double left_box_limit;
  double right_box_limit;
  double left_trust_minimum;
  double left_trust_maximum;
  double right_trust_minimum;
  double right_trust_maximum;
  if (!route_ray_box_limit(junction->joint, scale(-1.0, junction->t_ref),
                           junction->local_barriers[0], &left_box_limit) ||
      !route_ray_box_limit(junction->joint, junction->t_ref,
                           junction->local_barriers[1], &right_box_limit) ||
      !route_ray_trust_interval(junction->joint, scale(-1.0, junction->t_ref),
                                left_cubic[2], left_trust_radius,
                                &left_trust_minimum, &left_trust_maximum) ||
      !route_ray_trust_interval(junction->joint, junction->t_ref,
                                right_cubic[1], right_trust_radius,
                                &right_trust_minimum, &right_trust_maximum))
    return false;
  const double left_minimum = MAX(left_length_floor, left_trust_minimum);
  const double right_minimum = MAX(right_length_floor, right_trust_minimum);
  const double left_ceiling =
      MIN(left_trust_maximum,
          MIN(left_box_limit,
              MIN(left_chord, MAX(left_minimum, left_length * 2.0))));
  const double right_ceiling =
      MIN(right_trust_maximum,
          MIN(right_box_limit,
              MIN(right_chord, MAX(right_minimum, right_length * 2.0))));
  if (left_ceiling < left_minimum || right_ceiling < right_minimum)
    return false;
  const double projected_left =
      MIN(left_ceiling, MAX(left_minimum, left_length));
  const double projected_right =
      MIN(right_ceiling, MAX(right_minimum, right_length));
  *left_control =
      sub_pointf(junction->joint, scale(projected_left, junction->t_ref));
  *right_control =
      add_pointf(junction->joint, scale(projected_right, junction->t_ref));
  return true;
}

static bool fixed_template_g1_valid(const route_junction_t *junction,
                                    pointf left_control, pointf right_control) {
  const pointf incoming = sub_pointf(junction->joint, left_control);
  const pointf outgoing = sub_pointf(right_control, junction->joint);
  const double incoming_length = hypot(incoming.x, incoming.y);
  const double outgoing_length = hypot(outgoing.x, outgoing.y);
  if (incoming_length <= MILLIPOINT || outgoing_length <= MILLIPOINT)
    return false;
  const double residual = fabs(route_cross_product(incoming, outgoing));
  return residual <= 1e-9 * incoming_length * outgoing_length &&
         incoming.x * outgoing.x + incoming.y * outgoing.y > 0.0;
}

static double route_g1_angular_residual(const route_junction_t *junction,
                                        pointf left_control,
                                        pointf right_control) {
  const pointf incoming = sub_pointf(junction->joint, left_control);
  const pointf outgoing = sub_pointf(right_control, junction->joint);
  const double incoming_length = hypot(incoming.x, incoming.y);
  const double outgoing_length = hypot(outgoing.x, outgoing.y);
  if (!isfinite(incoming_length) || !isfinite(outgoing_length) ||
      incoming_length <= MILLIPOINT || outgoing_length <= MILLIPOINT)
    return INFINITY;

  const double cross = fabs(route_cross_product(incoming, outgoing));
  const double product = incoming.x * outgoing.x + incoming.y * outgoing.y;
  if (!isfinite(cross) || !isfinite(product))
    return INFINITY;
  return atan2(cross, product);
}

static bool route_g1_residual_improves(double original, double candidate) {
  return isfinite(candidate) && candidate <= ROUTE_G1_RESIDUAL_TOLERANCE &&
         candidate + ROUTE_G1_RESIDUAL_TOLERANCE < original;
}

static bool route_cubic_clears_nodes(graph_t *graph, edge_t *edge,
                                     const pointf control[4]) {
  edge = getmainedge(edge);
  const double clearance =
      late_double(edge, E_penwidth, 1.0, 0.0) / 2 + MULTIEDGE_NODE_MARGIN;
  const node_t *const tail = agtail(edge);
  const node_t *const head = aghead(edge);
  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    if (node == tail || node == head || ND_node_type(node) != NORMAL)
      continue;
    const boxf obstacle = {
        .LL = {ND_coord(node).x - ND_lw(node) - clearance,
               ND_coord(node).y - ND_ht(node) / 2 - clearance},
        .UR = {ND_coord(node).x + ND_rw(node) + clearance,
               ND_coord(node).y + ND_ht(node) / 2 + clearance},
    };
    if (bezier_intersects_box(control, obstacle))
      return false;
  }
  return true;
}

static bool
route_metadata_sequence_valid(const route_spline_metadata_t *metadata) {
  if (!route_spline_metadata_valid(metadata))
    return false;
  for (size_t i = 0; i < metadata->barrier_count; i++) {
    const Pedge_t barrier = metadata->barriers[i];
    if (!isfinite(barrier.a.x) || !isfinite(barrier.a.y) ||
        !isfinite(barrier.b.x) || !isfinite(barrier.b.y))
      return false;
  }
  for (size_t i = 0; i < metadata->corridor_count; i++) {
    const boxf corridor_box = metadata->corridor[i];
    if (!isfinite(corridor_box.LL.x) || !isfinite(corridor_box.LL.y) ||
        !isfinite(corridor_box.UR.x) || !isfinite(corridor_box.UR.y) ||
        corridor_box.LL.x > corridor_box.UR.x ||
        corridor_box.LL.y > corridor_box.UR.y ||
        (i > 0 && !boxes_overlap(metadata->corridor[i - 1], corridor_box)))
      return false;
  }
  for (size_t i = 0; i < metadata->portal_count; i++) {
    const Pedge_t portal = metadata->portals[i];
    const boxf first = metadata->corridor[i];
    const boxf second = metadata->corridor[i + 1];
    const pointf portal_points[2] = {portal.a, portal.b};
    for (size_t point_index = 0; point_index < 2; point_index++) {
      const pointf portal_point = portal_points[point_index];
      if (!isfinite(portal_point.x) || !isfinite(portal_point.y) ||
          portal_point.x < MAX(first.LL.x, second.LL.x) - MILLIPOINT ||
          portal_point.x > MIN(first.UR.x, second.UR.x) + MILLIPOINT ||
          portal_point.y < MAX(first.LL.y, second.LL.y) - MILLIPOINT ||
          portal_point.y > MIN(first.UR.y, second.UR.y) + MILLIPOINT)
        return false;
    }
  }
  for (size_t i = 0; i < metadata->template_point_count; i++) {
    const Ppoint_t template_point = metadata->template_points[i];
    if (!isfinite(template_point.x) || !isfinite(template_point.y))
      return false;
  }
  return true;
}

static bool
route_junction_topology_metadata_valid(const route_junction_t *junction,
                                       const route_pieces_t *pieces) {
  if (!route_junction_metadata_valid(junction) ||
      junction->portal_id != (size_t)AGSEQ(junction->portal) ||
      junction->left_piece >= LIST_SIZE(pieces) ||
      junction->right_piece >= LIST_SIZE(pieces))
    return false;
  const route_piece_metadata_t *const left =
      LIST_AT(pieces, junction->left_piece);
  const route_piece_metadata_t *const right =
      LIST_AT(pieces, junction->right_piece);
  if (left->edge != junction->edge || right->edge != junction->edge ||
      left->spline_index != junction->left_spline ||
      right->spline_index != junction->right_spline ||
      left->end.portal != junction->portal ||
      right->start.portal != junction->portal ||
      !route_metadata_sequence_valid(&left->end.route) ||
      !route_metadata_sequence_valid(&right->start.route))
    return false;

  const pointf left_template_end =
      left->end.route.template_points[left->end.route.template_point_count - 1];
  const pointf right_template_start = right->start.route.template_points[0];
  const pointf portal_position = ND_coord(junction->portal);
  const pointf left_router_offset =
      sub_pointf(junction->left_router_portal, junction->left_installed_portal);
  const pointf right_router_offset = sub_pointf(
      junction->right_router_portal, junction->right_installed_portal);
  return left_template_end.x == junction->left_router_portal.x &&
         left_template_end.y == junction->left_router_portal.y &&
         right_template_start.x == junction->right_router_portal.x &&
         right_template_start.y == junction->right_router_portal.y &&
         DIST(junction->joint, portal_position) <= MILLIPOINT &&
         DIST(junction->joint, junction->left_installed_portal) <= MILLIPOINT &&
         DIST(junction->joint, junction->right_installed_portal) <=
             MILLIPOINT &&
         fabs(left_router_offset.x) <= MILLIPOINT &&
         fabs(left_router_offset.y - 1.0) <= MILLIPOINT &&
         fabs(right_router_offset.x) <= MILLIPOINT &&
         fabs(right_router_offset.y + 1.0) <= MILLIPOINT;
}

static bool repair_route_junction(graph_t *graph, const route_pieces_t *pieces,
                                  const route_junction_t *junction) {
  if (!route_junction_topology_metadata_valid(junction, pieces))
    return false;
  splines *const edge_splines = ED_spl(junction->edge);
  bezier *const left = &edge_splines->list[junction->left_spline];
  bezier *const right = &edge_splines->list[junction->right_spline];
  pointf *const left_control = &left->list[left->size - 2];
  pointf *const right_control = &right->list[1];
  const pointf saved_left = *left_control;
  const pointf saved_right = *right_control;
  const double saved_residual =
      route_g1_angular_residual(junction, saved_left, saved_right);
  const route_bezier_invariants_t saved_left_invariants =
      route_bezier_invariants(left);
  const route_bezier_invariants_t saved_right_invariants =
      route_bezier_invariants(right);

  pointf candidate_left;
  pointf candidate_right;
  pointf repeated_left;
  pointf repeated_right;
  if (!fixed_template_g1_candidate(junction, &candidate_left,
                                   &candidate_right) ||
      !fixed_template_g1_candidate(junction, &repeated_left, &repeated_right) ||
      DIST(candidate_left, repeated_left) > 1e-12 ||
      DIST(candidate_right, repeated_right) > 1e-12 ||
      !fixed_template_g1_valid(junction, candidate_left, candidate_right) ||
      !route_g1_residual_improves(
          saved_residual,
          route_g1_angular_residual(junction, candidate_left, candidate_right)))
    return false;

  route_crossings_t original_crossings = {0};
  route_crossings_t candidate_crossings = {0};
  route_crossings_t original_barriers = {0};
  route_crossings_t candidate_barriers = {0};
  route_crossings_t original_corridor = {0};
  route_crossings_t candidate_corridor = {0};
  route_sides_t original_sides = {0};
  route_sides_t candidate_sides = {0};
  const bool original_signatures_valid =
      route_graph_crossing_signature(graph, junction, &original_crossings) &&
      route_barrier_signature(junction, pieces, &original_barriers) &&
      route_corridor_signature(junction, pieces, &original_corridor) &&
      route_barrier_side_signature(junction, pieces, &original_sides);
  if (!original_signatures_valid || !LIST_IS_EMPTY(&original_barriers)) {
    LIST_FREE(&original_crossings);
    LIST_FREE(&original_barriers);
    LIST_FREE(&original_corridor);
    LIST_FREE(&original_sides);
    return false;
  }

  *left_control = candidate_left;
  *right_control = candidate_right;
  const bool candidate_signatures_valid =
      route_graph_crossing_signature(graph, junction, &candidate_crossings) &&
      route_barrier_signature(junction, pieces, &candidate_barriers) &&
      route_corridor_signature(junction, pieces, &candidate_corridor) &&
      route_barrier_side_signature(junction, pieces, &candidate_sides);

  pointf idempotent_left;
  pointf idempotent_right;
  const bool valid =
      candidate_signatures_valid &&
      route_junction_topology_metadata_valid(junction, pieces) &&
      route_bezier_invariants_equal(saved_left_invariants, left) &&
      route_bezier_invariants_equal(saved_right_invariants, right) &&
      fixed_template_g1_valid(junction, *left_control, *right_control) &&
      route_g1_residual_improves(
          saved_residual,
          route_g1_angular_residual(junction, *left_control, *right_control)) &&
      route_cubic_clears_nodes(graph, junction->edge,
                               &left->list[left->size - 4]) &&
      route_cubic_clears_nodes(graph, junction->edge, &right->list[0]) &&
      route_crossing_signatures_equal(&original_crossings,
                                      &candidate_crossings) &&
      route_crossing_signatures_equal(&original_barriers,
                                      &candidate_barriers) &&
      LIST_IS_EMPTY(&candidate_barriers) &&
      route_crossing_signatures_equal(&original_corridor,
                                      &candidate_corridor) &&
      route_side_signatures_equal(&original_sides, &candidate_sides) &&
      fixed_template_g1_candidate(junction, &idempotent_left,
                                  &idempotent_right) &&
      DIST(idempotent_left, *left_control) <= 1e-9 &&
      DIST(idempotent_right, *right_control) <= 1e-9;
  if (!valid) {
    *left_control = saved_left;
    *right_control = saved_right;
  } else {
    update_bb_bz(&GD_bb(graph), &left->list[left->size - 4]);
    update_bb_bz(&GD_bb(graph), &right->list[0]);
  }

  LIST_FREE(&original_crossings);
  LIST_FREE(&candidate_crossings);
  LIST_FREE(&original_barriers);
  LIST_FREE(&candidate_barriers);
  LIST_FREE(&original_corridor);
  LIST_FREE(&candidate_corridor);
  LIST_FREE(&original_sides);
  LIST_FREE(&candidate_sides);
  return valid;
}

static void repair_route_junctions(graph_t *graph, const route_pieces_t *pieces,
                                   const route_junctions_t *junctions) {
  for (size_t i = 0; i < LIST_SIZE(junctions); i++)
    repair_route_junction(graph, pieces, LIST_AT(junctions, i));
}

static bool shifted_route_intersects_box(const points_t *points, double offset,
                                         boxf obstacle) {
  assert(LIST_SIZE(points) % 3 == 1);
  for (size_t start = 0; start + 3 < LIST_SIZE(points); start += 3) {
    pointf control[4];
    for (size_t i = 0; i < 4; i++) {
      const size_t index = start + i;
      control[i] = LIST_GET(points, index);
      if (index > 0 && index + 1 < LIST_SIZE(points))
        control[i].x += offset;
    }
    if (bezier_intersects_box(control, obstacle))
      return true;
  }
  return false;
}

static bool shifted_route_clears_nodes(graph_t *g, edge_t *edge,
                                       const points_t *points, double offset) {
  edge = getmainedge(edge);
  const double clearance =
      late_double(edge, E_penwidth, 1.0, 0.0) / 2 + MULTIEDGE_NODE_MARGIN;
  const node_t *const tail = agtail(edge);
  const node_t *const head = aghead(edge);

  for (node_t *node = agfstnode(g); node; node = agnxtnode(g, node)) {
    if (node == tail || node == head || ND_node_type(node) != NORMAL)
      continue;
    const boxf obstacle = {
        .LL = {ND_coord(node).x - ND_lw(node) - clearance,
               ND_coord(node).y - ND_ht(node) / 2 - clearance},
        .UR = {ND_coord(node).x + ND_rw(node) + clearance,
               ND_coord(node).y + ND_ht(node) / 2 + clearance},
    };
    if (shifted_route_intersects_box(points, offset, obstacle))
      return false;
  }
  return true;
}

static double constrain_multiedge_offset(graph_t *g, edge_t *edge,
                                         const points_t *points,
                                         double requested) {
  if (shifted_route_clears_nodes(g, edge, points, requested))
    return requested;

  // The center route already passed through the node-aware path router. If it
  // does not provide the requested stroke clearance, the displacement did not
  // cause the conflict and contracting it cannot reliably solve the problem.
  if (!shifted_route_clears_nodes(g, edge, points, 0))
    return requested;

  double safe = 0;
  double unsafe = requested;
  while (fabs(unsafe - safe) > MULTIEDGE_BEZIER_FLATNESS) {
    const double candidate = (safe + unsafe) / 2;
    if (shifted_route_clears_nodes(g, edge, points, candidate))
      safe = candidate;
    else
      unsafe = candidate;
  }
  return safe;
}

static void make_regular_edge(graph_t *g, spline_info_t *sp, path *P,
                              edge_t **edges, unsigned cnt, int et,
                              route_pieces_t *route_pieces,
                              route_junctions_t *route_junctions) {
  node_t *tn, *hn;
  Agedgeinfo_t fwdedgeai, fwdedgebi, fwdedgei;
  Agedgepair_t fwdedgea, fwdedgeb, fwdedge;
  edge_t *e, *fe, *le, *segfirst;
  pathend_t tend, hend;
  boxf b;
  int sl;
  points_t pointfs = {0};
  points_t pointfs2 = {0};
  route_endpoint_metadata_t route_start = {0};
  route_endpoint_metadata_t route_end = {0};
  route_spline_metadata_t first_route = {0};
  route_spline_metadata_t last_route = {0};

  fwdedgea.out.base.data = &fwdedgeai.hdr;
  fwdedgeb.out.base.data = &fwdedgebi.hdr;
  fwdedge.out.base.data = &fwdedgei.hdr;

  sl = 0;
  e = *edges;
  bool hackflag = false;
  if (abs(ND_rank(agtail(e)) - ND_rank(aghead(e))) > 1) {
    fwdedgeai = *(Agedgeinfo_t *)((uintptr_t)e->base.data -
                                  offsetof(Agedgeinfo_t, hdr));
    fwdedgea.out = *e;
    fwdedgea.in = *AGOUT2IN(e);
    fwdedgea.out.base.data = &fwdedgeai.hdr;
    if (ED_tree_index(e) & BWDEDGE) {
      makefwdedge(&fwdedgeb.out, e);
      agtail(&fwdedgea.out) = aghead(e);
      ED_tail_port(&fwdedgea.out) = ED_head_port(e);
    } else {
      fwdedgebi = *(Agedgeinfo_t *)((uintptr_t)e->base.data -
                                    offsetof(Agedgeinfo_t, hdr));
      fwdedgeb.out = *e;
      fwdedgeb.out.base.data = &fwdedgebi.hdr;
      agtail(&fwdedgea.out) = agtail(e);
      fwdedgeb.in = *AGOUT2IN(e);
    }
    le = getmainedge(e);
    while (ED_to_virt(le))
      le = ED_to_virt(le);
    aghead(&fwdedgea.out) = aghead(le);
    ED_head_port(&fwdedgea.out).defined = false;
    ED_edge_type(&fwdedgea.out) = VIRTUAL;
    ED_head_port(&fwdedgea.out).p.x = ED_head_port(&fwdedgea.out).p.y = 0;
    ED_to_orig(&fwdedgea.out) = e;
    e = &fwdedgea.out;
    hackflag = true;
  } else {
    if (ED_tree_index(e) & BWDEDGE) {
      makefwdedge(&fwdedgea.out, e);
      e = &fwdedgea.out;
    }
  }
  fe = e;

  /* compute the spline points for the edge */

  if (et == EDGETYPE_LINE && makeLineEdge(g, fe, &pointfs, &hn)) {
  } else {
    bool is_spline = et == EDGETYPE_SPLINE;
    boxes_t boxes = {0};
    size_t pending_straight_bridge = SIZE_MAX;
    segfirst = e;
    tn = agtail(e);
    hn = aghead(e);
    b = tend.nb = maximal_bbox(g, *sp, tn, NULL, e);
    beginpath(P, e, REGULAREDGE, &tend, spline_merge(tn));
    route_start = (route_endpoint_metadata_t){
        .portal = spline_merge(tn) ? tn : NULL,
        .router_portal = P->start.p,
        .installed_portal = ND_coord(tn),
        .tangent = P->start.constrained ? (pointf){.x = cos(P->start.theta),
                                                   .y = sin(P->start.theta)}
                                        : (pointf){0},
        .local_barrier = tend.nb,
        .constrained = P->start.constrained,
    };
    b.UR.y = tend.boxes[tend.boxn - 1].UR.y;
    b.LL.y = tend.boxes[tend.boxn - 1].LL.y;
    b = makeregularend(b, BOTTOM, ND_coord(tn).y - GD_rank(g)[ND_rank(tn)].ht1);
    if (b.LL.x < b.UR.x && b.LL.y < b.UR.y)
      tend.boxes[tend.boxn++] = b;
    bool smode = false;
    bool si = false;
    while (ND_node_type(hn) == VIRTUAL && !sinfo.splineMerge(hn)) {
      LIST_APPEND(&boxes, rank_box(sp, g, ND_rank(tn)));
      if (!smode && ((sl = straight_len(hn)) >=
                     ((GD_has_labels(g->root) & EDGE_LABEL) ? 4 + 1 : 2 + 1))) {
        smode = true;
        si = true;
        sl -= 2;
      }
      if (!smode || si) {
        si = false;
        LIST_APPEND(&boxes, maximal_bbox(g, *sp, hn, e, ND_out(hn).list[0]));
        e = ND_out(hn).list[0];
        tn = agtail(e);
        hn = aghead(e);
        continue;
      }
      hend.nb = maximal_bbox(g, *sp, hn, e, ND_out(hn).list[0]);
      endpath(P, e, REGULAREDGE, &hend, spline_merge(aghead(e)));
      b = makeregularend(hend.boxes[hend.boxn - 1], TOP,
                         ND_coord(hn).y + GD_rank(g)[ND_rank(hn)].ht2);
      if (b.LL.x < b.UR.x && b.LL.y < b.UR.y)
        hend.boxes[hend.boxn++] = b;
      P->end.theta = M_PI / 2, P->end.constrained = true;
      completeregularpath(P, segfirst, e, &tend, &hend, &boxes);
      pointf *ps = NULL;
      size_t pn = 0;
      if (is_spline) {
        route_spline_metadata_t current_route = {0};
        ps = routesplines_with_metadata(P, &pn, &current_route);
        capture_route_spline_metadata(&first_route, &last_route,
                                      &current_route);
      } else {
        ps = routepolylines(P, &pn);
        if (et == EDGETYPE_LINE && pn > 4) {
          ps[1] = ps[0];
          ps[3] = ps[2] = ps[pn - 1];
          pn = 4;
        }
      }
      if (pn == 0) {
        free(ps);
        LIST_FREE(&boxes);
        LIST_FREE(&pointfs);
        LIST_FREE(&pointfs2);
        route_spline_metadata_free(&first_route);
        route_spline_metadata_free(&last_route);
        return;
      }

      for (size_t i = 0; i < pn; i++) {
        LIST_APPEND(&pointfs, ps[i]);
      }
      regularize_straight_bridge(&pointfs, &pending_straight_bridge);
      free(ps);
      e = straight_path(ND_out(hn).list[0], sl, &pointfs,
                        &pending_straight_bridge);
      recover_slack(segfirst, P);
      segfirst = e;
      tn = agtail(e);
      hn = aghead(e);
      LIST_CLEAR(&boxes);
      tend.nb = maximal_bbox(g, *sp, tn, ND_in(tn).list[0], e);
      beginpath(P, e, REGULAREDGE, &tend, spline_merge(tn));
      b = makeregularend(tend.boxes[tend.boxn - 1], BOTTOM,
                         ND_coord(tn).y - GD_rank(g)[ND_rank(tn)].ht1);
      if (b.LL.x < b.UR.x && b.LL.y < b.UR.y)
        tend.boxes[tend.boxn++] = b;
      P->start.theta = -M_PI / 2, P->start.constrained = true;
      smode = false;
    }
    LIST_APPEND(&boxes, rank_box(sp, g, ND_rank(tn)));
    b = hend.nb = maximal_bbox(g, *sp, hn, e, NULL);
    endpath(P, hackflag ? &fwdedgeb.out : e, REGULAREDGE, &hend,
            spline_merge(aghead(e)));
    route_end = (route_endpoint_metadata_t){
        .portal = spline_merge(aghead(e)) ? aghead(e) : NULL,
        .router_portal = P->end.p,
        .installed_portal = ND_coord(aghead(e)),
        .tangent = P->end.constrained ? (pointf){.x = -cos(P->end.theta),
                                                 .y = -sin(P->end.theta)}
                                      : (pointf){0},
        .local_barrier = hend.nb,
        .constrained = P->end.constrained,
    };
    b.UR.y = hend.boxes[hend.boxn - 1].UR.y;
    b.LL.y = hend.boxes[hend.boxn - 1].LL.y;
    b = makeregularend(b, TOP, ND_coord(hn).y + GD_rank(g)[ND_rank(hn)].ht2);
    if (b.LL.x < b.UR.x && b.LL.y < b.UR.y)
      hend.boxes[hend.boxn++] = b;
    completeregularpath(P, segfirst, e, &tend, &hend, &boxes);
    LIST_FREE(&boxes);
    pointf *ps = NULL;
    size_t pn = 0;
    if (is_spline) {
      route_spline_metadata_t current_route = {0};
      ps = routesplines_with_metadata(P, &pn, &current_route);
      capture_route_spline_metadata(&first_route, &last_route, &current_route);
    } else
      ps = routepolylines(P, &pn);
    if (et == EDGETYPE_LINE && pn > 4) {
      /* Here we have used the polyline case to handle
       * an edge between two nodes on adjacent ranks. If the
       * results really is a polyline, straighten it.
       */
      ps[1] = ps[0];
      ps[3] = ps[2] = ps[pn - 1];
      pn = 4;
    }
    if (pn == 0) {
      free(ps);
      LIST_FREE(&pointfs);
      LIST_FREE(&pointfs2);
      route_spline_metadata_free(&first_route);
      route_spline_metadata_free(&last_route);
      return;
    }
    for (size_t i = 0; i < pn; i++) {
      LIST_APPEND(&pointfs, ps[i]);
    }
    regularize_straight_bridge(&pointfs, &pending_straight_bridge);
    free(ps);
    assert(pending_straight_bridge == SIZE_MAX);
    recover_slack(segfirst, P);
    hn = hackflag ? aghead(&fwdedgeb.out) : aghead(e);
  }

  route_start.route = first_route;
  route_end.route = last_route;
  if (route_spline_metadata_valid(&route_start.route))
    route_start.local_barrier = route_start.route.corridor[0];
  if (route_spline_metadata_valid(&route_end.route))
    route_end.local_barrier =
        route_end.route.corridor[route_end.route.corridor_count - 1];

  /* make copies of the spline points, one per multi-edge */

  if (cnt == 1) {
    LIST_SYNC(&pointfs);
    edge_t *const owner = route_spline_owner(fe);
    const size_t prior_spline_count =
        ED_spl(owner) == NULL ? 0 : ED_spl(owner)->size;
    clip_and_install(fe, hn, LIST_FRONT(&pointfs), LIST_SIZE(&pointfs), &sinfo);
    align_concentrated_route_tangents(g, fe);
    if (et == EDGETYPE_SPLINE)
      record_route_piece(route_pieces, route_junctions, fe, prior_spline_count,
                         route_start, route_end, 0.0);
    if (Concentrate)
      align_arrow_tangents(g, fe);
    LIST_FREE(&pointfs);
    LIST_FREE(&pointfs2);
    route_spline_metadata_free(&first_route);
    route_spline_metadata_free(&last_route);
    return;
  }
  const double dx = sp->Multisep * (cnt - 1) / 2;
  for (unsigned j = 0; j < cnt; j++) {
    node_t *install_head;
    if (j == 0) {
      e = fe;
      install_head = hn;
    } else {
      e = edges[j];
      if (ED_tree_index(e) & BWDEDGE) {
        makefwdedge(&fwdedge.out, e);
        e = &fwdedge.out;
      }
      install_head = aghead(e);
    }
    const double requested = -dx + j * sp->Multisep;
    const double offset = constrain_multiedge_offset(g, e, &pointfs, requested);
    LIST_CLEAR(&pointfs2);
    for (size_t k = 0; k < LIST_SIZE(&pointfs); k++) {
      pointf route_point = LIST_GET(&pointfs, k);
      if (k > 0 && k + 1 < LIST_SIZE(&pointfs))
        route_point.x += offset;
      LIST_APPEND(&pointfs2, route_point);
    }
    LIST_SYNC(&pointfs2);
    edge_t *const owner = route_spline_owner(e);
    const size_t prior_spline_count =
        ED_spl(owner) == NULL ? 0 : ED_spl(owner)->size;
    clip_and_install(e, install_head, LIST_FRONT(&pointfs2),
                     LIST_SIZE(&pointfs2), &sinfo);
    align_concentrated_route_tangents(g, e);
    if (et == EDGETYPE_SPLINE)
      record_route_piece(route_pieces, route_junctions, e, prior_spline_count,
                         route_start, route_end, offset);
    align_arrow_tangents(g, e);
  }
  LIST_FREE(&pointfs);
  LIST_FREE(&pointfs2);
  route_spline_metadata_free(&first_route);
  route_spline_metadata_free(&last_route);
}

/* regular edges */

static void completeregularpath(path *P, edge_t *first, edge_t *last,
                                pathend_t *tendp, pathend_t *hendp,
                                const boxes_t *boxes) {
  edge_t *uleft = top_bound(first, -1);
  edge_t *uright = top_bound(first, 1);
  if (uleft) {
    if (getsplinepoints(uleft) == NULL)
      return;
  }
  if (uright) {
    if (getsplinepoints(uright) == NULL)
      return;
  }
  edge_t *lleft = bot_bound(last, -1);
  edge_t *lright = bot_bound(last, 1);
  if (lleft) {
    if (getsplinepoints(lleft) == NULL)
      return;
  }
  if (lright) {
    if (getsplinepoints(lright) == NULL)
      return;
  }
  for (int i = 0; i < tendp->boxn; i++)
    add_box(P, tendp->boxes[i]);
  const size_t fb = P->nbox + 1;
  const size_t lb = fb + LIST_SIZE(boxes) - 3;
  for (size_t i = 0; i < LIST_SIZE(boxes); i++)
    add_box(P, LIST_GET(boxes, i));
  for (int i = hendp->boxn - 1; i >= 0; i--)
    add_box(P, hendp->boxes[i]);
  adjustregularpath(P, fb, lb);
}

/* Add box to fill between node and interrank space. Needed because
 * nodes in a given rank can differ in height.
 * for now, regular edges always go from top to bottom
 */
static boxf makeregularend(boxf b, int side, double y) {
  assert(side == BOTTOM || side == TOP);
  if (side == BOTTOM) {
    return (boxf){{b.LL.x, y}, {b.UR.x, b.LL.y}};
  }
  return (boxf){{b.LL.x, b.UR.y}, {b.UR.x, y}};
}

/* make sure the path is wide enough.
 * the % 2 was so that in rank boxes would only be grown if
 * they were == 0 while inter-rank boxes could be stretched to a min
 * width.
 * The list of boxes has three parts: tail boxes, path boxes, and head
 * boxes. (Note that because of back edges, the tail boxes might actually
 * belong to the head node, and vice versa.) fb is the index of the
 * first interrank path box and lb is the last interrank path box.
 * If fb > lb, there are none.
 *
 * The second for loop was added by ek long ago, and apparently is intended
 * to guarantee an overlap between adjacent boxes of at least MINW.
 * It doesn't do this.
 */
static void adjustregularpath(path *P, size_t fb, size_t lb) {
  boxf *bp1, *bp2;

  for (size_t i = fb - 1; i < lb + 1; i++) {
    bp1 = &P->boxes[i];
    if ((i - fb) % 2 == 0) {
      if (bp1->LL.x >= bp1->UR.x) {
        double x = (bp1->LL.x + bp1->UR.x) / 2;
        bp1->LL.x = x - HALFMINW;
        bp1->UR.x = x + HALFMINW;
      }
    } else {
      if (bp1->LL.x + MINW > bp1->UR.x) {
        double x = (bp1->LL.x + bp1->UR.x) / 2;
        bp1->LL.x = x - HALFMINW;
        bp1->UR.x = x + HALFMINW;
      }
    }
  }
  for (size_t i = 0; i + 1 < P->nbox; i++) {
    bp1 = &P->boxes[i], bp2 = &P->boxes[i + 1];
    if (i >= fb && i <= lb && (i - fb) % 2 == 0) {
      if (bp1->LL.x + MINW > bp2->UR.x)
        bp2->UR.x = bp1->LL.x + MINW;
      if (bp1->UR.x - MINW < bp2->LL.x)
        bp2->LL.x = bp1->UR.x - MINW;
    } else if (i + 1 >= fb && i < lb && (i + 1 - fb) % 2 == 0) {
      if (bp1->LL.x + MINW > bp2->UR.x)
        bp1->LL.x = bp2->UR.x - MINW;
      if (bp1->UR.x - MINW < bp2->LL.x)
        bp1->UR.x = bp2->LL.x + MINW;
    }
  }
}

static boxf rank_box(spline_info_t *sp, graph_t *g, int r) {
  boxf b = sp->Rank_box[r];
  if (b.LL.x == b.UR.x) {
    node_t *const left0 = GD_rank(g)[r].v[0];
    node_t *const left1 = GD_rank(g)[r + 1].v[0];
    b.LL.x = sp->LeftBound;
    b.LL.y = ND_coord(left1).y + GD_rank(g)[r + 1].ht2;
    b.UR.x = sp->RightBound;
    b.UR.y = ND_coord(left0).y - GD_rank(g)[r].ht1;
    sp->Rank_box[r] = b;
  }
  return b;
}

/* returns count of vertically aligned edges starting at n */
static int straight_len(node_t *n) {
  int cnt = 0;
  node_t *v;

  v = n;
  while (1) {
    v = aghead(ND_out(v).list[0]);
    if (ND_node_type(v) != VIRTUAL)
      break;
    if (ND_out(v).size != 1 || ND_in(v).size != 1)
      break;
    if (ND_coord(v).x != ND_coord(n).x)
      break;
    cnt++;
  }
  return cnt;
}

static void erase_points(points_t *points, size_t first, size_t count) {
  const size_t size = LIST_SIZE(points);
  assert(first <= size && count <= size - first);

  for (size_t i = first; i + count < size; i++) {
    LIST_SET(points, i, LIST_GET(points, i + count));
  }
  for (size_t i = 0; i < count; i++) {
    LIST_DROP_BACK(points);
  }
}

static void regularize_straight_bridge(points_t *points,
                                       size_t *pending_straight_bridge) {
  if (*pending_straight_bridge == SIZE_MAX) {
    return;
  }

  const size_t bridge = *pending_straight_bridge;
  assert(bridge + 3 < LIST_SIZE(points));
  const pointf j = LIST_GET(points, bridge);
  const pointf k = LIST_GET(points, bridge + 3);
  if (j.x == k.x && j.y == k.y) {
    // Drop the two placeholders and the next partial's duplicate start.
    erase_points(points, bridge + 1, 3);
  } else {
    const pointf delta = {.x = k.x - j.x, .y = k.y - j.y};
    LIST_SET(points, bridge + 1,
             ((pointf){.x = j.x + delta.x / 3.0, .y = j.y + delta.y / 3.0}));
    LIST_SET(points, bridge + 2,
             ((pointf){.x = j.x + 2.0 * delta.x / 3.0,
                       .y = j.y + 2.0 * delta.y / 3.0}));
  }
  *pending_straight_bridge = SIZE_MAX;
  assert(LIST_SIZE(points) % 3 == 1);
}

static edge_t *straight_path(edge_t *e, int cnt, points_t *plist,
                             size_t *pending_straight_bridge) {
  edge_t *f = e;

  while (cnt--)
    f = ND_out(aghead(f)).list[0];
  assert(!LIST_IS_EMPTY(plist));
  assert(*pending_straight_bridge == SIZE_MAX);
  *pending_straight_bridge = LIST_SIZE(plist) - 1;
  LIST_APPEND(plist, LIST_GET(plist, LIST_SIZE(plist) - 1));
  LIST_APPEND(plist, LIST_GET(plist, LIST_SIZE(plist) - 1));

  return f;
}

static void recover_slack(edge_t *e, path *p) {
  node_t *vn;

  size_t b = 0; // skip first rank box
  for (vn = aghead(e); ND_node_type(vn) == VIRTUAL && !sinfo.splineMerge(vn);
       vn = aghead(ND_out(vn).list[0])) {
    while (b < p->nbox && p->boxes[b].LL.y > ND_coord(vn).y)
      b++;
    if (b >= p->nbox)
      break;
    if (p->boxes[b].UR.y < ND_coord(vn).y)
      continue;
    if (ND_label(vn))
      resize_vn(vn, p->boxes[b].LL.x, p->boxes[b].UR.x,
                p->boxes[b].UR.x + ND_rw(vn));
    else
      resize_vn(vn, p->boxes[b].LL.x, (p->boxes[b].LL.x + p->boxes[b].UR.x) / 2,
                p->boxes[b].UR.x);
  }
}

static void resize_vn(node_t *vn, double lx, double cx, double rx) {
  ND_coord(vn).x = cx;
  ND_lw(vn) = cx - lx, ND_rw(vn) = rx - cx;
}

/* side > 0 means right. side < 0 means left */
static edge_t *top_bound(edge_t *e, int side) {
  edge_t *f, *ans = NULL;
  int i;

  for (i = 0; (f = ND_out(agtail(e)).list[i]); i++) {
    if (side * (ND_order(aghead(f)) - ND_order(aghead(e))) <= 0)
      continue;
    if (ED_spl(f) == NULL &&
        (ED_to_orig(f) == NULL || ED_spl(ED_to_orig(f)) == NULL))
      continue;
    if (ans == NULL || side * (ND_order(aghead(ans)) - ND_order(aghead(f))) > 0)
      ans = f;
  }
  return ans;
}

static edge_t *bot_bound(edge_t *e, int side) {
  edge_t *f, *ans = NULL;
  int i;

  for (i = 0; (f = ND_in(aghead(e)).list[i]); i++) {
    if (side * (ND_order(agtail(f)) - ND_order(agtail(e))) <= 0)
      continue;
    if (ED_spl(f) == NULL &&
        (ED_to_orig(f) == NULL || ED_spl(ED_to_orig(f)) == NULL))
      continue;
    if (ans == NULL || side * (ND_order(agtail(ans)) - ND_order(agtail(f))) > 0)
      ans = f;
  }
  return ans;
}

/* common routines */

static bool cl_vninside(graph_t *cl, node_t *n) {
  return BETWEEN(GD_bb(cl).LL.x, ND_coord(n).x, GD_bb(cl).UR.x) &&
         BETWEEN(GD_bb(cl).LL.y, ND_coord(n).y, GD_bb(cl).UR.y);
}

/* All nodes belong to some cluster, which may be the root graph.
 * For the following, we only want a cluster if it is a real cluster
 * It is not clear this will handle all potential problems. It seems one
 * could have hcl and tcl contained in cl, which would also cause problems.
 */
#define REAL_CLUSTER(n) (ND_clust(n) == g ? NULL : ND_clust(n))

/* returns the cluster of (adj) that interferes with n,
 */
static Agraph_t *cl_bound(graph_t *g, node_t *n, node_t *adj) {
  graph_t *rv, *cl, *tcl, *hcl;
  edge_t *orig;

  rv = NULL;
  if (ND_node_type(n) == NORMAL)
    tcl = hcl = ND_clust(n);
  else {
    orig = ED_to_orig(ND_out(n).list[0]);
    tcl = ND_clust(agtail(orig));
    hcl = ND_clust(aghead(orig));
  }
  if (ND_node_type(adj) == NORMAL) {
    cl = REAL_CLUSTER(adj);
    if (cl && cl != tcl && cl != hcl)
      rv = cl;
  } else {
    orig = ED_to_orig(ND_out(adj).list[0]);
    cl = REAL_CLUSTER(agtail(orig));
    if (cl && cl != tcl && cl != hcl && cl_vninside(cl, adj))
      rv = cl;
    else {
      cl = REAL_CLUSTER(aghead(orig));
      if (cl && cl != tcl && cl != hcl && cl_vninside(cl, adj))
        rv = cl;
    }
  }
  return rv;
}

/* Return an initial bounding box to be used for building the
 * beginning or ending of the path of boxes.
 * Height reflects height of tallest node on rank.
 * The extra space provided by FUDGE allows begin/endpath to create a box
 * FUDGE-2 away from the node, so the routing can avoid the node and the
 * box is at least 2 wide.
 */
#define FUDGE 4

static boxf maximal_bbox(graph_t *g, const spline_info_t sp, node_t *vn,
                         edge_t *ie, edge_t *oe) {
  double b, nb;
  graph_t *left_cl, *right_cl;
  node_t *left, *right;
  boxf rv;

  left_cl = right_cl = NULL;

  /* give this node all the available space up to its neighbors */
  b = (double)(ND_coord(vn).x - ND_lw(vn) - FUDGE);
  if ((left = neighbor(g, vn, ie, oe, -1))) {
    if ((left_cl = cl_bound(g, vn, left)))
      nb = GD_bb(left_cl).UR.x + sp.Splinesep;
    else {
      nb = (double)(ND_coord(left).x + ND_mval(left));
      if (ND_node_type(left) == NORMAL)
        nb += GD_nodesep(g) / 2.;
      else
        nb += sp.Splinesep;
    }
    if (nb < b)
      b = nb;
    rv.LL.x = round(b);
  } else
    rv.LL.x = fmin(round(b), sp.LeftBound);

  /* we have to leave room for our own label! */
  if (ND_node_type(vn) == VIRTUAL && ND_label(vn))
    b = (double)(ND_coord(vn).x + 10);
  else
    b = (double)(ND_coord(vn).x + ND_rw(vn) + FUDGE);
  if ((right = neighbor(g, vn, ie, oe, 1))) {
    if ((right_cl = cl_bound(g, vn, right)))
      nb = GD_bb(right_cl).LL.x - sp.Splinesep;
    else {
      nb = ND_coord(right).x - ND_lw(right);
      if (ND_node_type(right) == NORMAL)
        nb -= GD_nodesep(g) / 2.;
      else
        nb -= sp.Splinesep;
    }
    if (nb > b)
      b = nb;
    rv.UR.x = round(b);
  } else
    rv.UR.x = fmax(round(b), sp.RightBound);

  if (ND_node_type(vn) == VIRTUAL && ND_label(vn)) {
    rv.UR.x -= ND_rw(vn);
    if (rv.UR.x < rv.LL.x)
      rv.UR.x = ND_coord(vn).x;
  }

  rv.LL.y = ND_coord(vn).y - GD_rank(g)[ND_rank(vn)].ht1;
  rv.UR.y = ND_coord(vn).y + GD_rank(g)[ND_rank(vn)].ht2;
  return rv;
}

static node_t *neighbor(graph_t *g, node_t *vn, edge_t *ie, edge_t *oe,
                        int dir) {
  int i;
  node_t *n, *rv = NULL;
  rank_t *rank = &(GD_rank(g)[ND_rank(vn)]);

  for (i = ND_order(vn) + dir; i >= 0 && i < rank->n; i += dir) {
    n = rank->v[i];
    if (ND_node_type(n) == VIRTUAL && ND_label(n)) {
      rv = n;
      break;
    }
    if (ND_node_type(n) == NORMAL) {
      rv = n;
      break;
    }
    if (!pathscross(n, vn, ie, oe)) {
      rv = n;
      break;
    }
  }
  return rv;
}

static bool pathscross(node_t *n0, node_t *n1, edge_t *ie1, edge_t *oe1) {
  edge_t *e0, *e1;
  node_t *na, *nb;
  int order, cnt;

  order = ND_order(n0) > ND_order(n1);
  if (ND_out(n0).size != 1 && ND_out(n1).size != 1)
    return false;
  e1 = oe1;
  if (ND_out(n0).size == 1 && e1) {
    e0 = ND_out(n0).list[0];
    for (cnt = 0; cnt < 2; cnt++) {
      if ((na = aghead(e0)) == (nb = aghead(e1)))
        break;
      if (order != (ND_order(na) > ND_order(nb)))
        return true;
      if (ND_out(na).size != 1 || ND_node_type(na) == NORMAL)
        break;
      e0 = ND_out(na).list[0];
      if (ND_out(nb).size != 1 || ND_node_type(nb) == NORMAL)
        break;
      e1 = ND_out(nb).list[0];
    }
  }
  e1 = ie1;
  if (ND_in(n0).size == 1 && e1) {
    e0 = ND_in(n0).list[0];
    for (cnt = 0; cnt < 2; cnt++) {
      if ((na = agtail(e0)) == (nb = agtail(e1)))
        break;
      if (order != (ND_order(na) > ND_order(nb)))
        return true;
      if (ND_in(na).size != 1 || ND_node_type(na) == NORMAL)
        break;
      e0 = ND_in(na).list[0];
      if (ND_in(nb).size != 1 || ND_node_type(nb) == NORMAL)
        break;
      e1 = ND_in(nb).list[0];
    }
  }
  return false;
}

#ifdef DEBUG
void showpath(path *p) {
  pointf LL, UR;

  fprintf(stderr, "%%!PS\n");
  for (size_t i = 0; i < p->nbox; i++) {
    LL = p->boxes[i].LL;
    UR = p->boxes[i].UR;
    fprintf(stderr,
            "newpath %.04f %.04f moveto %.04f %.04f lineto %.04f %.04f lineto "
            "%.04f %.04f lineto closepath stroke\n",
            LL.x, LL.y, UR.x, LL.y, UR.x, UR.y, LL.x, UR.y);
  }
  fprintf(stderr, "showpage\n");
}
#endif
