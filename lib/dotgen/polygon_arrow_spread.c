/*************************************************************************
 * Copyright (c) 2026 Graphviz Authors
 * All rights reserved.
 *
 * This program and the accompanying materials are made available under
 * the terms of the Eclipse Public License v2.0 which accompanies this
 * distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *************************************************************************/

#include "config.h"

#include <common/edgeattr.h>
#include <common/geomprocs.h>
#include <common/globals.h>
#include <common/render.h>
#include <common/utils.h>
#include <dotgen/polygon_arrow_spread.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdlib.h>
#include <util/alloc.h>
#include <util/gv_math.h>

#define COINCIDENT_ARROWHEAD_DISTANCE 2.0
#define MAX_SPREAD_TURN_DEGREES 30.0
#define REGRESSION_TURN_DEGREES 35.0
#define SPREAD_TURN_SAMPLES 24

typedef struct {
  size_t count;
  size_t regression_count;
  double worst;
} turn_score_t;

typedef struct {
  edge_t *edge;
  bezier *spline;
  node_t *node;
  pointf tip;
  pointf base;
  pointf centroid;
  pointf control;
  double spacing;
  double grouping_distance;
  double arrowsize;
  double penwidth;
  uint32_t arrow_flags;
  bool at_start;
  bool curved_outline;
  bool smooth_curved_move;
} arrow_landing_t;

static polygon_t *node_spread_outline(node_t *node) {
  polygon_t *const polygon = ND_shape_info(node);
  if (polygon == NULL || polygon->vertices == NULL || polygon->peripheries == 0)
    return NULL;
  if (polygon->sides < 2)
    return NULL;
  return polygon;
}

static edge_t *normal_edge(edge_t *edge) {
  while (ED_to_orig(edge) != NULL && ED_edge_type(edge) != NORMAL)
    edge = ED_to_orig(edge);
  return edge;
}

static void align_control_arm(pointf *control, pointf endpoint,
                              pointf arrow_tip) {
  const pointf axis = sub_pointf(arrow_tip, endpoint);
  const double axis_length = hypot(axis.x, axis.y);
  const double control_length = DIST(*control, endpoint);
  if (axis_length <= MILLIPOINT || control_length <= MILLIPOINT)
    return;

  *control = sub_pointf(endpoint, scale(control_length / axis_length, axis));
}

static double turn_degrees(pointf incoming, pointf joint, pointf outgoing) {
  const pointf in = sub_pointf(joint, incoming);
  const pointf out = sub_pointf(outgoing, joint);
  const double in_length = hypot(in.x, in.y);
  const double out_length = hypot(out.x, out.y);
  if (in_length <= MILLIPOINT || out_length <= MILLIPOINT)
    return 0.0;

  double cosine = (in.x * out.x + in.y * out.y) / (in_length * out_length);
  cosine = MAX(-1.0, MIN(1.0, cosine));
  return acos(cosine) * 180.0 / M_PI;
}

static pointf cubic_point(pointf p0, pointf p1, pointf p2, pointf p3,
                          double t) {
  const double u = 1.0 - t;
  return add_pointf(
      add_pointf(scale(u * u * u, p0), scale(3.0 * u * u * t, p1)),
      add_pointf(scale(3.0 * u * t * t, p2), scale(t * t * t, p3)));
}

/// Score the drawn Bezier curve, not the control polygon. The corner this pass
/// can introduce is visible on sampled curve points where a moved endpoint-side
/// segment rejoins the untouched spline body.
static turn_score_t sampled_sharp_turns(bezier *spline) {
  if (spline->size < 4)
    return (turn_score_t){0};

  turn_score_t score = {0};
  pointf before = {0};
  pointf joint = {0};
  bool have_before = false;
  bool have_joint = false;
  for (size_t i = 0; i + 3 < spline->size; i += 3) {
    for (int sample = 0; sample <= SPREAD_TURN_SAMPLES; sample++) {
      if (i > 0 && sample == 0)
        continue;
      const pointf current =
          cubic_point(spline->list[i], spline->list[i + 1], spline->list[i + 2],
                      spline->list[i + 3],
                      (double)sample / (double)SPREAD_TURN_SAMPLES);
      if (have_before && have_joint) {
        const double turn = turn_degrees(before, joint, current);
        if (turn > MAX_SPREAD_TURN_DEGREES) {
          score.count++;
          if (turn > REGRESSION_TURN_DEGREES) {
            score.regression_count++;
            score.worst = MAX(score.worst, turn);
          }
        }
      }
      before = joint;
      joint = current;
      if (have_joint)
        have_before = true;
      have_joint = true;
    }
  }
  return score;
}

static bool turn_score_no_worse(turn_score_t after, turn_score_t before) {
  if (after.count != before.count)
    return after.count < before.count;
  if (after.regression_count != before.regression_count)
    return after.regression_count < before.regression_count;
  return after.worst <= before.worst + 1e-9;
}

static bool turn_score_no_regression(turn_score_t after, turn_score_t before) {
  if (after.regression_count != before.regression_count)
    return after.regression_count < before.regression_count;
  return after.worst <= before.worst + 1e-9;
}

static void append_arrow_landing(arrow_landing_t **landings, size_t *count,
                                 size_t *capacity, edge_t *edge, bezier *spline,
                                 bool at_start) {
  if (spline->size < 4)
    return;

  edge_t *const main_edge = normal_edge(edge);
  node_t *const node = at_start ? agtail(edge) : aghead(edge);
  const pointf tip = at_start ? spline->sp : spline->ep;
  const pointf base =
      at_start ? spline->list[0] : spline->list[spline->size - 1];
  const pointf control =
      at_start ? spline->list[1] : spline->list[spline->size - 2];
  polygon_t *const outline = node_spread_outline(node);
  if (outline == NULL)
    return;
  if (at_start && E_sametail != NULL &&
      agxget(main_edge, E_sametail)[0] != '\0')
    return;
  if (!at_start && E_samehead != NULL &&
      agxget(main_edge, E_samehead)[0] != '\0')
    return;

  const double arrow_length = edge_arrow_length(
      main_edge, at_start ? EDGE_ARROW_START : EDGE_ARROW_END);
  const double arrowsize = edge_arrow_arrowsize(
      main_edge, at_start ? EDGE_ARROW_START : EDGE_ARROW_END);
  const double penwidth = late_double(main_edge, E_penwidth, 1.0, 0.0);
  uint32_t start_flags = 0;
  uint32_t end_flags = 0;
  edge_arrow_flags(main_edge, &start_flags, &end_flags);
  const double side_length = hypot(ND_lw(node) + ND_rw(node), ND_ht(node));
  const bool curved_outline = outline->sides < 3;
  const bool requested_concentrate = mapbool(agget(agroot(edge), "concentrate"));
  if (curved_outline && !Concentrate && requested_concentrate)
    return;
  const double spacing =
      curved_outline && !requested_concentrate
          ? MIN(side_length / 4.0, MAX(1.5 * arrow_length, 2.0 * penwidth))
          : curved_outline
                ? MIN(side_length / 6.0, MAX(arrow_length, 2.0 * penwidth))
                : MIN(side_length / 6.0,
                      MAX(arrow_length / 4.0, 2.0 * penwidth));
  if (spacing <= MILLIPOINT)
    return;

  if (*count == *capacity) {
    const size_t new_capacity = *capacity == 0 ? 64 : *capacity * 2;
    *landings =
        gv_recalloc(*landings, *capacity, new_capacity, sizeof(**landings));
    *capacity = new_capacity;
  }
  (*landings)[(*count)++] = (arrow_landing_t){
      .edge = main_edge,
      .spline = spline,
      .node = node,
      .tip = tip,
      .base = base,
      .centroid = scale(1.0 / 3.0, add_pointf(tip, scale(2.0, base))),
      .control = control,
      .spacing = spacing,
      .grouping_distance =
          curved_outline ? arrow_length : COINCIDENT_ARROWHEAD_DISTANCE,
      .arrowsize = arrowsize,
      .penwidth = penwidth,
      .arrow_flags = at_start ? start_flags : end_flags,
      .at_start = at_start,
      .curved_outline = curved_outline,
      .smooth_curved_move = curved_outline && !requested_concentrate,
  };
}

static int compare_arrow_landings(const void *a, const void *b) {
  const arrow_landing_t *const left = a;
  const arrow_landing_t *const right = b;
  if (left->node < right->node)
    return -1;
  if (left->node > right->node)
    return 1;
  if (left->centroid.x < right->centroid.x)
    return -1;
  if (left->centroid.x > right->centroid.x)
    return 1;
  if (left->centroid.y < right->centroid.y)
    return -1;
  if (left->centroid.y > right->centroid.y)
    return 1;
  if (AGSEQ(left->edge) < AGSEQ(right->edge))
    return -1;
  if (AGSEQ(left->edge) > AGSEQ(right->edge))
    return 1;
  return 0;
}

static void move_arrow_landing(arrow_landing_t *landing, pointf target) {
  const pointf delta = sub_pointf(target, landing->tip);
  bezier *const spline = landing->spline;
  if (landing->at_start) {
    spline->sp = target;
    for (size_t i = 0; i < 3 && i < spline->size; i++)
      spline->list[i] = add_pointf(spline->list[i], delta);
    align_control_arm(&spline->list[1], spline->list[0], spline->sp);
  } else {
    spline->ep = target;
    for (size_t i = spline->size - 3; i < spline->size; i++)
      spline->list[i] = add_pointf(spline->list[i], delta);
    align_control_arm(&spline->list[spline->size - 2],
                      spline->list[spline->size - 1], spline->ep);
  }
}

static void move_arrow_landing_with_falloff(arrow_landing_t *landing,
                                            pointf target) {
  bezier *const spline = landing->spline;
  const pointf delta = sub_pointf(target, landing->tip);
  if (landing->at_start) {
    spline->sp = target;
    for (size_t i = 0; i < spline->size; i++) {
      const double weight =
          (double)(spline->size - 1 - i) / (double)(spline->size - 1);
      spline->list[i] = add_pointf(spline->list[i], scale(weight, delta));
    }
  } else {
    spline->ep = target;
    for (size_t i = 0; i < spline->size; i++) {
      const double weight = (double)i / (double)(spline->size - 1);
      spline->list[i] = add_pointf(spline->list[i], scale(weight, delta));
    }
  }
}

static pointf scaled_target(pointf source, pointf target, double scale_factor) {
  return add_pointf(source, scale(scale_factor, sub_pointf(target, source)));
}

static void move_arrow_landing_by_kind(arrow_landing_t *landing, pointf target) {
  if (landing->smooth_curved_move)
    move_arrow_landing_with_falloff(landing, target);
  else
    move_arrow_landing(landing, target);
}

static void move_arrow_landing_smoothly(arrow_landing_t *landing, pointf target) {
  if (Concentrate) {
    move_arrow_landing(landing, target);
    return;
  }

  bezier *const spline = landing->spline;
  const pointf original_tip = landing->tip;
  const pointf old_sp = spline->sp;
  const pointf old_ep = spline->ep;
  pointf *const old_points = gv_calloc(spline->size, sizeof(*old_points));
  if (old_points == NULL)
    return;
  for (size_t i = 0; i < spline->size; i++)
    old_points[i] = spline->list[i];

  const turn_score_t old_sharp_turns = sampled_sharp_turns(spline);

  if (!landing->smooth_curved_move) {
    move_arrow_landing(landing, target);
    if (!turn_score_no_regression(sampled_sharp_turns(spline), old_sharp_turns)) {
      spline->sp = old_sp;
      spline->ep = old_ep;
      for (size_t i = 0; i < spline->size; i++)
        spline->list[i] = old_points[i];
    }
    free(old_points);
    return;
  }

  move_arrow_landing_by_kind(landing, target);
  if (turn_score_no_worse(sampled_sharp_turns(spline), old_sharp_turns)) {
    free(old_points);
    return;
  }

  spline->sp = old_sp;
  spline->ep = old_ep;
  for (size_t i = 0; i < spline->size; i++)
    spline->list[i] = old_points[i];

  double low = 0.0;
  double high = 1.0;
  for (int step = 0; step < 12; step++) {
    const double mid = (low + high) / 2.0;
    move_arrow_landing_by_kind(landing, scaled_target(original_tip, target, mid));
    const turn_score_t sharp_turns = sampled_sharp_turns(spline);
    spline->sp = old_sp;
    spline->ep = old_ep;
    for (size_t i = 0; i < spline->size; i++)
      spline->list[i] = old_points[i];
    if (turn_score_no_worse(sharp_turns, old_sharp_turns))
      low = mid;
    else
      high = mid;
  }

  if (low > MILLIPOINT)
    move_arrow_landing_by_kind(landing, scaled_target(original_tip, target, low));
  free(old_points);
}

static pointf project_to_ellipse_outline(node_t *node, pointf target) {
  const pointf center = ND_coord(node);
  const double rx = MAX((ND_lw(node) + ND_rw(node)) / 2.0, MILLIPOINT);
  const double ry = MAX(ND_ht(node) / 2.0, MILLIPOINT);
  const pointf ray = sub_pointf(target, center);
  const double scale_factor = hypot(ray.x / rx, ray.y / ry);
  if (scale_factor <= MILLIPOINT)
    return (pointf){.x = center.x + rx, .y = center.y};
  return (pointf){.x = center.x + ray.x / scale_factor,
                  .y = center.y + ray.y / scale_factor};
}

static double signed_polygon_area(const pointf *poly, size_t count) {
  double area = 0.0;
  for (size_t i = 0; i < count; i++) {
    const pointf a = poly[i];
    const pointf b = poly[(i + 1) % count];
    area += a.x * b.y - b.x * a.y;
  }
  return area / 2.0;
}

static bool inside_halfplane(pointf p, pointf start, pointf end, bool clockwise) {
  const double cross =
      (end.x - start.x) * (p.y - start.y) - (end.y - start.y) * (p.x - start.x);
  return clockwise ? cross <= 1e-9 : cross >= -1e-9;
}

static pointf segment_intersection(pointf a, pointf b, pointf c, pointf d) {
  const double den = (a.x - b.x) * (c.y - d.y) - (a.y - b.y) * (c.x - d.x);
  if (fabs(den) < 1e-9)
    return b;
  const double left = a.x * b.y - a.y * b.x;
  const double right = c.x * d.y - c.y * d.x;
  return (pointf){.x = (left * (c.x - d.x) - (a.x - b.x) * right) / den,
                  .y = (left * (c.y - d.y) - (a.y - b.y) * right) / den};
}

static double convex_intersection_area(const pointf *subject, size_t subject_count,
                                       const pointf *clip, size_t clip_count) {
  if (subject_count < 3 || clip_count < 3)
    return 0.0;

  pointf in[16];
  pointf out[16];
  if (subject_count > ARRAY_SIZE(in) || clip_count > ARRAY_SIZE(out))
    return 0.0;

  size_t in_count = subject_count;
  for (size_t i = 0; i < subject_count; i++)
    in[i] = subject[i];

  const bool clockwise = signed_polygon_area(clip, clip_count) < 0.0;
  for (size_t i = 0; i < clip_count; i++) {
    const pointf start = clip[i];
    const pointf end = clip[(i + 1) % clip_count];
    if (in_count == 0)
      return 0.0;

    size_t out_count = 0;
    pointf prev = in[in_count - 1];
    bool prev_inside = inside_halfplane(prev, start, end, clockwise);
    for (size_t j = 0; j < in_count; j++) {
      const pointf current = in[j];
      const bool current_inside = inside_halfplane(current, start, end, clockwise);
      if (current_inside) {
        if (!prev_inside)
          out[out_count++] = segment_intersection(prev, current, start, end);
        out[out_count++] = current;
      } else if (prev_inside) {
        out[out_count++] = segment_intersection(prev, current, start, end);
      }
      if (out_count >= ARRAY_SIZE(out))
        return 0.0;
      prev = current;
      prev_inside = current_inside;
    }

    in_count = out_count;
    for (size_t j = 0; j < in_count; j++)
      in[j] = out[j];
  }

  return fabs(signed_polygon_area(in, in_count));
}

static size_t landing_arrow_polygons(const arrow_landing_t *landing, pointf tip,
                                     pointf polygons[8][9],
                                     size_t polygon_counts[8]) {
  arrow_geometry_t geometry;
  const pointf delta = sub_pointf(tip, landing->tip);
  const pointf base = add_pointf(landing->base, delta);
  arrow_geometry(tip, base, landing->arrowsize, landing->penwidth,
                 landing->arrow_flags, &geometry);

  size_t count = 0;
  for (size_t i = 0; i < geometry.nprimitives && count < 8; i++) {
    const arrow_primitive_t *const primitive = &geometry.primitives[i];
    if (primitive->kind != ARROW_PRIMITIVE_POLYGON || primitive->npoints < 3)
      continue;
    polygon_counts[count] = primitive->npoints;
    for (size_t j = 0; j < primitive->npoints; j++)
      polygons[count][j] = primitive->points[j];
    count++;
  }
  return count;
}

static bool arrow_polygons_overlap(const arrow_landing_t *left, pointf left_tip,
                                   const arrow_landing_t *right,
                                   pointf right_tip) {
  pointf left_polygons[8][9];
  pointf right_polygons[8][9];
  size_t left_counts[8];
  size_t right_counts[8];
  const size_t left_count =
      landing_arrow_polygons(left, left_tip, left_polygons, left_counts);
  const size_t right_count =
      landing_arrow_polygons(right, right_tip, right_polygons, right_counts);

  for (size_t i = 0; i < left_count; i++) {
    for (size_t j = 0; j < right_count; j++) {
      if (convex_intersection_area(left_polygons[i], left_counts[i],
                                   right_polygons[j], right_counts[j]) > 1e-6)
        return true;
    }
  }
  return false;
}

static bool target_would_create_neighbor_overlap(const arrow_landing_t *landings,
                                                 size_t landing_count,
                                                 size_t group_start,
                                                 size_t group_end,
                                                 size_t group_index,
                                                 pointf target) {
  const arrow_landing_t *const landing = &landings[group_start + group_index];
  for (size_t i = 0; i < landing_count; i++) {
    if (i >= group_start && i < group_end)
      continue;
    if (landings[i].node != landing->node)
      continue;
    if (arrow_polygons_overlap(landing, target, &landings[i], landings[i].tip) &&
        !arrow_polygons_overlap(landing, landing->tip, &landings[i],
                                landings[i].tip))
      return true;
  }
  return false;
}

/// Spread one group of arrowheads that landed on top of each other, and report
/// whether anything actually moved so the caller knows if the drawing settled.
static bool spread_arrow_landing_group(arrow_landing_t *landings,
                                       size_t landing_count, size_t group_start,
                                       size_t group_end) {
  arrow_landing_t *const group = &landings[group_start];
  const size_t count = group_end - group_start;
  bool spread_pair = false;
  bool curved_group = false;
  for (size_t i = 0; i < count; i++)
    curved_group = curved_group || group[i].curved_outline;
  spread_pair = curved_group;
  if (count <= 1 || (count == 2 && !spread_pair))
    return false;

  double spacing = HUGE_VAL;
  for (size_t i = 0; i < count; i++) {
    spacing = MIN(spacing, group[i].spacing);
  }

  pointf direction = sub_pointf(group[count - 1].tip, group[0].tip);
  double direction_length = hypot(direction.x, direction.y);
  if (direction_length <= MILLIPOINT) {
    pointf axis = {0};
    for (size_t i = 0; i < count; i++)
      axis = add_pointf(axis, sub_pointf(group[i].tip, group[i].control));
    direction = (pointf){.x = -axis.y, .y = axis.x};
    direction_length = hypot(direction.x, direction.y);
  }
  if (direction_length <= MILLIPOINT)
    return false;
  direction = scale(1.0 / direction_length, direction);

  const double center_offset =
      curved_group ? ((double)count - 1.0) * spacing / 2.0 : 0.0;
  bool moved = false;
  for (size_t i = 0; i < count; i++) {
    pointf target = add_pointf(
        group[0].tip, scale((double)i * spacing - center_offset, direction));
    if (group[i].curved_outline)
      target = project_to_ellipse_outline(group[i].node, target);
    if (!Concentrate && group[i].smooth_curved_move &&
        target_would_create_neighbor_overlap(landings, landing_count,
                                             group_start, group_end, i, target))
      continue;
    moved = moved || DIST(group[i].tip, target) > MILLIPOINT;
    move_arrow_landing_smoothly(&group[i], target);
  }
  return moved;
}

/// One sweep over every arrowhead landing in the graph. Returns true when it
/// moved something: splitting a visible blob can uncover the remaining adjacent
/// half of a clamped fan, which is only reachable on a later sweep.
static bool spread_coincident_arrowheads_once(graph_t *g) {
  arrow_landing_t *landings = NULL;
  size_t landing_count = 0;
  size_t landing_capacity = 0;

  for (node_t *node = agfstnode(g); node != NULL; node = agnxtnode(g, node)) {
    for (edge_t *edge = agfstout(g, node); edge != NULL;
         edge = agnxtout(g, edge)) {
      splines *const edge_splines = ED_spl(edge);
      if (edge_splines == NULL)
        continue;
      for (size_t i = 0; i < edge_splines->size; i++) {
        bezier *const spline = &edge_splines->list[i];
        if (spline->sflag != ARR_NONE)
          append_arrow_landing(&landings, &landing_count, &landing_capacity,
                               edge, spline, true);
        if (spline->eflag != ARR_NONE)
          append_arrow_landing(&landings, &landing_count, &landing_capacity,
                               edge, spline, false);
      }
    }
  }

  if (landing_count > 1)
    qsort(landings, landing_count, sizeof(*landings), compare_arrow_landings);

  size_t group_start = 0;
  bool moved = false;
  while (group_start < landing_count) {
    size_t group_end = group_start + 1;
    while (
        group_end < landing_count &&
        landings[group_end].node == landings[group_start].node &&
        DIST(landings[group_end].centroid, landings[group_end - 1].centroid) <=
            MIN(landings[group_end].grouping_distance,
                landings[group_end - 1].grouping_distance)) {
      group_end++;
    }
    moved = spread_arrow_landing_group(landings, landing_count, group_start,
                                       group_end) ||
            moved;
    group_start = group_end;
  }

  free(landings);
  return moved;
}

/// Two sweeps, and deliberately not "until nothing moves".
///
/// The second sweep earns its keep: splitting a visible blob uncovers the
/// remaining adjacent half of a clamped fan, worth 3 fewer coincident
/// arrowheads on graphs/directed/pgram.gv (9 -> 6). A third does not — each
/// sweep re-anchors a group on its own first landing, so on self-edges the
/// group keeps drifting and pushes duplicate self-edge labels into the node
/// they belong to (measured on tests/graphs/sb_circle_dbl.gv). This is a
/// bounded refinement, not a fixpoint, and the bound is the measurement.
void dot_spread_coincident_polygon_arrowheads(graph_t *g) {
  enum { SPREAD_SWEEPS = 2 };
  for (int sweep = 0; sweep < SPREAD_SWEEPS; sweep++) {
    if (!spread_coincident_arrowheads_once(g))
      return;
  }
}
