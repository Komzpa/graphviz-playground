/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

#pragma once

#include <common/render.h>
#include <dotgen/dot.h>
#include <stdbool.h>
#include <util/list.h>

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
  route_spline_metadata_t route;
  size_t first_control;
  size_t control_count;
} route_local_plan_t;

typedef LIST(route_local_plan_t) route_local_plans_t;

typedef struct {
  edge_t *edge;
  size_t spline_index;
  route_endpoint_metadata_t start;
  route_endpoint_metadata_t end;
  route_local_plans_t local_plans;
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
  pointf sp;
  pointf ep;
} route_bezier_invariants_t;

typedef enum {
  ROUTE_SEGMENT_DISJOINT,
  ROUTE_SEGMENT_INTERSECTION,
  ROUTE_SEGMENT_AMBIGUOUS,
} route_segment_intersection_t;

#define ROUTE_G1_RESIDUAL_TOLERANCE 1e-9

edge_t *getmainedge(edge_t *);
edge_t *route_spline_owner(edge_t *);
bool spline_merge(node_t *);
bool bezier_intersects_box(const pointf[4], boxf);
bool route_spline_metadata_valid(const route_spline_metadata_t *);
void copy_route_spline_metadata(route_spline_metadata_t *,
                                const route_spline_metadata_t *);
void capture_route_spline_metadata(route_local_plans_t *,
                                   route_spline_metadata_t *, size_t, size_t);
void free_route_local_plans(route_local_plans_t *);
void free_route_piece_metadata(route_pieces_t *);
void record_route_piece(route_pieces_t *, route_junctions_t *, edge_t *, size_t,
                        route_endpoint_metadata_t, route_endpoint_metadata_t,
                        const route_local_plans_t *, double);
void repair_route_junctions(graph_t *, const route_pieces_t *,
                            const route_junctions_t *);
void repair_near_g1_spline_joins(graph_t *);
