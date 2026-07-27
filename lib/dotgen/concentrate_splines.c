/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

#include "config.h"

#include <assert.h>
#include <common/geomprocs.h>
#include <common/globals.h>
#include <common/render.h>
#include <dotgen/concentrate_splines.h>
#include <dotgen/spline_tuning.h>
#include <math.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>
#include <util/gv_math.h>

static bool boxes_overlap(boxf a, boxf b) {
  return MAX(a.LL.x, b.LL.x) <= MIN(a.UR.x, b.UR.x) &&
         MAX(a.LL.y, b.LL.y) <= MIN(a.UR.y, b.UR.y);
}

bool route_spline_metadata_valid(const route_spline_metadata_t *metadata) {
  return metadata->barriers != NULL && metadata->barrier_count >= 3 &&
         metadata->corridor != NULL && metadata->corridor_count > 0 &&
         metadata->portal_count + 1 == metadata->corridor_count &&
         (metadata->portal_count == 0 || metadata->portals != NULL) &&
         metadata->template_points != NULL &&
         metadata->template_point_count >= 2;
}

void copy_route_spline_metadata(route_spline_metadata_t *destination,
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

void capture_route_spline_metadata(route_local_plans_t *plans,
                                   route_spline_metadata_t *current,
                                   size_t first_control, size_t control_count) {
  if (!route_spline_metadata_valid(current)) {
    route_spline_metadata_free(current);
    return;
  }
  LIST_APPEND(plans, ((route_local_plan_t){
                         .route = *current,
                         .first_control = first_control,
                         .control_count = control_count,
                     }));
  *current = (route_spline_metadata_t){0};
}

void free_route_local_plans(route_local_plans_t *plans) {
  for (size_t i = 0; i < LIST_SIZE(plans); i++)
    route_spline_metadata_free(&LIST_AT(plans, i)->route);
  LIST_FREE(plans);
}

static void copy_route_local_plans(route_local_plans_t *destination,
                                   const route_local_plans_t *source) {
  *destination = (route_local_plans_t){0};
  for (size_t i = 0; i < LIST_SIZE(source); i++) {
    const route_local_plan_t *const plan = LIST_AT(source, i);
    route_local_plan_t copy = {
        .first_control = plan->first_control,
        .control_count = plan->control_count,
    };
    copy_route_spline_metadata(&copy.route, &plan->route);
    LIST_APPEND(destination, copy);
  }
}

void free_route_piece_metadata(route_pieces_t *pieces) {
  for (size_t i = 0; i < LIST_SIZE(pieces); i++) {
    route_piece_metadata_t *const piece = LIST_AT(pieces, i);
    route_spline_metadata_free(&piece->start.route);
    route_spline_metadata_free(&piece->end.route);
    free_route_local_plans(&piece->local_plans);
  }
  LIST_FREE(pieces);
}

edge_t *route_spline_owner(edge_t *edge) {
  while (ED_to_orig(edge) != NULL && ED_edge_type(edge) != NORMAL)
    edge = ED_to_orig(edge);
  return edge;
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

void record_route_piece(route_pieces_t *pieces, route_junctions_t *junctions,
                        edge_t *routed_edge, size_t prior_spline_count,
                        route_endpoint_metadata_t start,
                        route_endpoint_metadata_t end,
                        const route_local_plans_t *local_plans, double offset) {
  if (!Concentrate || fabs(offset) > MILLIPOINT ||
      !route_spline_metadata_valid(&start.route) ||
      !route_spline_metadata_valid(&end.route) || LIST_IS_EMPTY(local_plans))
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
  route_piece_metadata_t piece = {
      .edge = owner,
      .spline_index = prior_spline_count,
      .start = start,
      .end = end,
  };
  copy_route_local_plans(&piece.local_plans, local_plans);

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

bool bezier_intersects_box(const pointf control[4], boxf obstacle) {
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

typedef struct {
  const splines *edge_splines;
  const pointf *control;
  route_flat_points_t flat;
  boxf bounds;
  size_t edge_index;
  size_t spline_index;
  size_t cubic;
  bool flat_attempted;
  bool flat_valid;
} route_cubic_ref_t;

typedef struct {
  route_cubic_ref_t *items;
  size_t size;
  size_t capacity;
} route_cubic_index_t;

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

static void route_cubic_index_free(route_cubic_index_t *index) {
  for (size_t i = 0; i < index->size; i++)
    LIST_FREE(&index->items[i].flat);
  free(index->items);
  *index = (route_cubic_index_t){0};
}

static void route_cubic_index_append(route_cubic_index_t *index,
                                     const splines *edge_splines,
                                     const pointf *control, size_t edge_index,
                                     size_t spline_index, size_t cubic) {
  if (index->size == index->capacity) {
    const size_t capacity = index->capacity == 0 ? 64 : index->capacity * 2;
    index->items = gv_recalloc(index->items, index->capacity, capacity,
                               sizeof(*index->items));
    index->capacity = capacity;
  }
  route_cubic_ref_t *const item = &index->items[index->size++];
  *item = (route_cubic_ref_t){
      .edge_splines = edge_splines,
      .control = control,
      .bounds = route_cubic_bounds(control),
      .edge_index = edge_index,
      .spline_index = spline_index,
      .cubic = cubic,
  };
}

static int compare_route_cubic_bounds(const void *a, const void *b) {
  const route_cubic_ref_t *const left = a;
  const route_cubic_ref_t *const right = b;
  if (left->bounds.LL.x < right->bounds.LL.x)
    return -1;
  if (left->bounds.LL.x > right->bounds.LL.x)
    return 1;
  if (left->bounds.UR.x < right->bounds.UR.x)
    return -1;
  if (left->bounds.UR.x > right->bounds.UR.x)
    return 1;
  if (left->edge_index < right->edge_index)
    return -1;
  if (left->edge_index > right->edge_index)
    return 1;
  if (left->spline_index < right->spline_index)
    return -1;
  if (left->spline_index > right->spline_index)
    return 1;
  if (left->cubic < right->cubic)
    return -1;
  if (left->cubic > right->cubic)
    return 1;
  return 0;
}

static void route_cubic_index_build(graph_t *graph,
                                    route_cubic_index_t *index) {
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
          route_cubic_index_append(index, edge_splines,
                                   &spline->list[cubic_start], edge_index,
                                   spline_index, cubic_start / 3);
        }
      }
    }
  }
  if (index->size > 1) {
    qsort(index->items, index->size, sizeof(*index->items),
          compare_route_cubic_bounds);
  }
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

static bool route_graph_crossing_signature(route_cubic_index_t *index,
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
    for (size_t item_index = 0; item_index < index->size; item_index++) {
      route_cubic_ref_t *const item = &index->items[item_index];
      if (item->bounds.LL.x > affected_bounds.UR.x)
        break;
      if (item->bounds.UR.x < affected_bounds.LL.x)
        continue;
      const pointf *const other_control = item->control;
      const size_t other_cubic = item->cubic;
      const bool same_route = item->edge_splines == affected_splines;
      const bool adjacent_in_spline =
          same_route &&
          item->spline_index == affected_spline_indices[affected_index] &&
          (other_cubic + 1 == affected_cubic_indices[affected_index] ||
           affected_cubic_indices[affected_index] + 1 == other_cubic);
      // The two cubics intentionally meet at this tagged seam. Their shared
      // endpoint is not an edge crossing; G1 is checked separately below.
      const bool artificial_joint_neighbor =
          same_route && ((affected_index == 0 &&
                          item->spline_index == junction->right_spline &&
                          other_cubic == junction->right_cubic) ||
                         (affected_index == 1 &&
                          item->spline_index == junction->left_spline &&
                          other_cubic == junction->left_cubic));
      if (other_control == affected_controls[affected_index] ||
          adjacent_in_spline || artificial_joint_neighbor ||
          !boxf_overlap(affected_bounds, item->bounds))
        continue;
      if (!item->flat_attempted) {
        item->flat_valid = flatten_route_cubic(other_control, &item->flat);
        item->flat_attempted = true;
      }
      if (!item->flat_valid) {
        LIST_FREE(&affected);
        return false;
      }
      const bool unambiguous = append_route_crossings(
          &affected, &item->flat, affected_index, item->edge_index,
          item->spline_index, other_cubic, crossings);
      if (!unambiguous) {
        LIST_FREE(&affected);
        return false;
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

static bool averaged_existing_g1_candidate(const route_junction_t *junction,
                                           pointf left_control,
                                           pointf right_control,
                                           pointf *candidate_left,
                                           pointf *candidate_right) {
  const pointf incoming = sub_pointf(junction->joint, left_control);
  const pointf outgoing = sub_pointf(right_control, junction->joint);
  const double incoming_length = hypot(incoming.x, incoming.y);
  const double outgoing_length = hypot(outgoing.x, outgoing.y);
  if (!isfinite(incoming_length) || !isfinite(outgoing_length) ||
      incoming_length <= MILLIPOINT || outgoing_length <= MILLIPOINT ||
      incoming.x * outgoing.x + incoming.y * outgoing.y <= 0.0)
    return false;

  pointf direction = {
      .x = incoming.x / incoming_length + outgoing.x / outgoing_length,
      .y = incoming.y / incoming_length + outgoing.y / outgoing_length,
  };
  const double direction_length = hypot(direction.x, direction.y);
  if (!isfinite(direction_length) || direction_length <= MILLIPOINT)
    return false;
  direction.x /= direction_length;
  direction.y /= direction_length;

  *candidate_left =
      sub_pointf(junction->joint, scale(incoming_length, direction));
  *candidate_right =
      add_pointf(junction->joint, scale(outgoing_length, direction));
  return fixed_template_g1_valid(junction, *candidate_left, *candidate_right);
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

static double controls_angular_residual(pointf joint, pointf left_control,
                                        pointf right_control) {
  const pointf incoming = sub_pointf(joint, left_control);
  const pointf outgoing = sub_pointf(right_control, joint);
  const double cross = fabs(route_cross_product(incoming, outgoing));
  const double product = incoming.x * outgoing.x + incoming.y * outgoing.y;
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

static bool repair_route_junction(route_cubic_index_t *index, graph_t *graph,
                                  const route_pieces_t *pieces,
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
  bool candidate_valid =
      fixed_template_g1_candidate(junction, &candidate_left,
                                  &candidate_right) &&
      fixed_template_g1_candidate(junction, &repeated_left, &repeated_right) &&
      DIST(candidate_left, repeated_left) <= 1e-12 &&
      DIST(candidate_right, repeated_right) <= 1e-12 &&
      fixed_template_g1_valid(junction, candidate_left, candidate_right);
  if (!candidate_valid)
    candidate_valid = averaged_existing_g1_candidate(
        junction, saved_left, saved_right, &candidate_left, &candidate_right);
  if (!candidate_valid ||
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
      route_corridor_signature(junction, pieces, &original_corridor) &&
      route_barrier_side_signature(junction, pieces, &original_sides) &&
      route_barrier_signature(junction, pieces, &original_barriers) &&
      LIST_IS_EMPTY(&original_barriers) &&
      route_graph_crossing_signature(index, junction, &original_crossings);
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
      route_corridor_signature(junction, pieces, &candidate_corridor) &&
      route_barrier_side_signature(junction, pieces, &candidate_sides) &&
      route_barrier_signature(junction, pieces, &candidate_barriers) &&
      LIST_IS_EMPTY(&candidate_barriers) &&
      route_graph_crossing_signature(index, junction, &candidate_crossings);

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

void repair_route_junctions(graph_t *graph, const route_pieces_t *pieces,
                            const route_junctions_t *junctions) {
  if (LIST_IS_EMPTY(junctions))
    return;

  route_cubic_index_t index = {0};
  route_cubic_index_build(graph, &index);
  for (size_t i = 0; i < LIST_SIZE(junctions); i++)
    repair_route_junction(&index, graph, pieces, LIST_AT(junctions, i));
  route_cubic_index_free(&index);
}

static bool near_g1_spline_join_candidate(bezier *left, bezier *right,
                                          pointf *candidate_left,
                                          pointf *candidate_right) {
  if (left->size < 4 || right->size < 4 || left->size % 3 != 1 ||
      right->size % 3 != 1)
    return false;
  const pointf joint = left->list[left->size - 1];
  if (DIST(joint, right->list[0]) > MILLIPOINT)
    return false;

  const pointf saved_left = left->list[left->size - 2];
  const pointf saved_right = right->list[1];
  const pointf incoming = sub_pointf(joint, saved_left);
  const pointf outgoing = sub_pointf(saved_right, joint);
  const double incoming_length = hypot(incoming.x, incoming.y);
  const double outgoing_length = hypot(outgoing.x, outgoing.y);
  const double product = incoming.x * outgoing.x + incoming.y * outgoing.y;
  if (!isfinite(incoming_length) || !isfinite(outgoing_length) ||
      incoming_length <= MILLIPOINT || outgoing_length <= MILLIPOINT ||
      product <= 0.0)
    return false;

  const double saved_residual =
      controls_angular_residual(joint, saved_left, saved_right);
  if (!isfinite(saved_residual) ||
      saved_residual <= ROUTE_G1_RESIDUAL_TOLERANCE || saved_residual > 0.05)
    return false;

  pointf direction = {
      .x = incoming.x / incoming_length + outgoing.x / outgoing_length,
      .y = incoming.y / incoming_length + outgoing.y / outgoing_length,
  };
  const double direction_length = hypot(direction.x, direction.y);
  if (!isfinite(direction_length) || direction_length <= MILLIPOINT)
    return false;
  direction.x /= direction_length;
  direction.y /= direction_length;

  *candidate_left = sub_pointf(joint, scale(incoming_length, direction));
  *candidate_right = add_pointf(joint, scale(outgoing_length, direction));
  return controls_angular_residual(joint, *candidate_left, *candidate_right) <=
         ROUTE_G1_RESIDUAL_TOLERANCE;
}

void repair_near_g1_spline_joins(graph_t *graph) {
  if (!Concentrate)
    return;
  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    for (edge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {
      splines *const edge_splines = ED_spl(edge);
      if (edge_splines == NULL)
        continue;
      for (size_t i = 0; i + 1 < edge_splines->size; i++) {
        bezier *const left = &edge_splines->list[i];
        bezier *const right = &edge_splines->list[i + 1];
        pointf candidate_left;
        pointf candidate_right;
        if (!near_g1_spline_join_candidate(left, right, &candidate_left,
                                           &candidate_right))
          continue;
        const pointf saved_left = left->list[left->size - 2];
        const pointf saved_right = right->list[1];
        left->list[left->size - 2] = candidate_left;
        right->list[1] = candidate_right;
        if (!route_cubic_clears_nodes(graph, edge,
                                      &left->list[left->size - 4]) ||
            !route_cubic_clears_nodes(graph, edge, &right->list[0])) {
          left->list[left->size - 2] = saved_left;
          right->list[1] = saved_right;
        } else {
          update_bb_bz(&GD_bb(graph), &left->list[left->size - 4]);
          update_bb_bz(&GD_bb(graph), &right->list[0]);
        }
      }
    }
  }
}
