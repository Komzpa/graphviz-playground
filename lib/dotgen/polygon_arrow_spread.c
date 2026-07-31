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
#define SPREAD_TURN_SAMPLES 24

typedef struct {
  edge_t *edge;
  bezier *spline;
  node_t *node;
  pointf tip;
  pointf centroid;
  pointf control;
  double spacing;
  double grouping_distance;
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
static size_t sampled_sharp_turns(bezier *spline) {
  if (spline->size < 4)
    return 0;

  size_t sharp_turns = 0;
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
      if (have_before && have_joint)
        sharp_turns +=
            turn_degrees(before, joint, current) > MAX_SPREAD_TURN_DEGREES;
      before = joint;
      joint = current;
      if (have_joint)
        have_before = true;
      have_joint = true;
    }
  }
  return sharp_turns;
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
  const double penwidth = late_double(main_edge, E_penwidth, 1.0, 0.0);
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
      .centroid = scale(1.0 / 3.0, add_pointf(tip, scale(2.0, base))),
      .control = control,
      .spacing = spacing,
      .grouping_distance =
          curved_outline ? arrow_length : COINCIDENT_ARROWHEAD_DISTANCE,
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

static void move_arrow_landing_smoothly(arrow_landing_t *landing, pointf target) {
  if (!landing->smooth_curved_move) {
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

  const size_t old_sharp_turns = sampled_sharp_turns(spline);

  move_arrow_landing_with_falloff(landing, target);
  if (sampled_sharp_turns(spline) <= old_sharp_turns) {
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
    move_arrow_landing_with_falloff(landing,
                                    scaled_target(original_tip, target, mid));
    const size_t sharp_turns = sampled_sharp_turns(spline);
    spline->sp = old_sp;
    spline->ep = old_ep;
    for (size_t i = 0; i < spline->size; i++)
      spline->list[i] = old_points[i];
    if (sharp_turns <= old_sharp_turns)
      low = mid;
    else
      high = mid;
  }

  if (low > MILLIPOINT)
    move_arrow_landing_with_falloff(landing,
                                    scaled_target(original_tip, target, low));
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

/// Spread one group of arrowheads that landed on top of each other, and report
/// whether anything actually moved so the caller knows if the drawing settled.
static bool spread_arrow_landing_group(arrow_landing_t *group, size_t count) {
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
    moved = spread_arrow_landing_group(&landings[group_start],
                                       group_end - group_start) ||
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
