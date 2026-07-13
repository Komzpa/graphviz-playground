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

#include <common/concentrate_compat.h>

#include <common/render.h>
#include <string.h>

/*
 * Concentration is safe only when the two original edges describe the same
 * rendered edge after accounting for their relation.  Ordinary parallel edges
 * compare tail with tail and head with head.  Opposite edges compare tail with
 * head, because the same physical route is traversed in reverse.
 *
 * Keep this relation-aware compatibility test in one common helper so the
 * rank-spanning and same-rank concentrate paths do not grow separate, subtly
 * different interpretations of ports, samehead/sametail groups, arrowheads, or
 * non-endpoint attributes.
 */

typedef enum {
  CONCENTRATE_ENDPOINT_TAIL,
  CONCENTRATE_ENDPOINT_HEAD,
} concentrate_endpoint_t;

typedef struct {
  edge_t *edge;
  const char *samehead;
  const char *sametail;
  port tail_port;
  port head_port;
  uint32_t tail_arrow;
  uint32_t head_arrow;
  bool has_head_or_tail_label;
} concentrate_normalized_edge_t;

edge_t *concentrate_normal_edge(edge_t *e) {
  while (e != NULL && ED_edge_type(e) != NORMAL)
    e = ED_to_orig(e);
  return e;
}

void concentrate_compat_state_init(graph_t *g,
                                   concentrate_compat_state_t *state) {
  *state = (concentrate_compat_state_t){
      .samehead = agfindedgeattr(g, "samehead"),
      .sametail = agfindedgeattr(g, "sametail"),
      .headport = agfindedgeattr(g, "headport"),
      .tailport = agfindedgeattr(g, "tailport"),
      .arrowhead = agfindedgeattr(g, "arrowhead"),
      .arrowtail = agfindedgeattr(g, "arrowtail"),
      .dir = agfindedgeattr(g, "dir"),
  };
}

static bool
is_concentrate_endpoint_attr(const concentrate_compat_state_t *state,
                             const Agsym_t *attr) {
  return attr == state->samehead || attr == state->sametail ||
         attr == state->headport || attr == state->tailport ||
         attr == state->arrowhead || attr == state->arrowtail ||
         attr == state->dir;
}

static bool same_nonendpoint_edge_attrs(const concentrate_compat_state_t *state,
                                        edge_t *e, edge_t *f) {
  graph_t *const g = agroot(agraphof(e));

  for (Agsym_t *attr = agnxtattr(g, AGEDGE, NULL); attr != NULL;
       attr = agnxtattr(g, AGEDGE, attr)) {
    if (is_concentrate_endpoint_attr(state, attr))
      continue;
    if (strcmp(agxget(e, attr), agxget(f, attr)) != 0)
      return false;
  }
  return true;
}

static bool same_port(port p0, port p1) {
  return p0.defined == p1.defined &&
         (!p0.defined || (p0.p.x == p1.p.x && p0.p.y == p1.p.y));
}

static const char *endpoint_group(const concentrate_normalized_edge_t *edge,
                                  concentrate_endpoint_t endpoint) {
  return endpoint == CONCENTRATE_ENDPOINT_HEAD ? edge->samehead
                                               : edge->sametail;
}

static port endpoint_port(const concentrate_normalized_edge_t *edge,
                          concentrate_endpoint_t endpoint) {
  return endpoint == CONCENTRATE_ENDPOINT_HEAD ? edge->head_port
                                               : edge->tail_port;
}

static uint32_t endpoint_arrow(const concentrate_normalized_edge_t *edge,
                               concentrate_endpoint_t endpoint) {
  return endpoint == CONCENTRATE_ENDPOINT_HEAD ? edge->head_arrow
                                               : edge->tail_arrow;
}

static bool parallel_endpoint_arrows_compatible(uint32_t lhs, uint32_t rhs) {
  return lhs == rhs;
}

static bool opposite_endpoint_arrows_compatible(uint32_t lhs, uint32_t rhs) {
  return lhs == rhs || lhs == 0 || rhs == 0;
}

static bool endpoint_compatible(const concentrate_normalized_edge_t *lhs,
                                concentrate_endpoint_t lhs_endpoint,
                                const concentrate_normalized_edge_t *rhs,
                                concentrate_endpoint_t rhs_endpoint,
                                bool (*arrows_compatible)(uint32_t, uint32_t)) {
  const char *const lhs_group = endpoint_group(lhs, lhs_endpoint);
  const char *const rhs_group = endpoint_group(rhs, rhs_endpoint);

  if (lhs_endpoint == rhs_endpoint) {
    if (strcmp(lhs_group, rhs_group) != 0)
      return false;
  } else if (lhs_group[0] != '\0' || rhs_group[0] != '\0') {
    return false;
  }

  if (!same_port(endpoint_port(lhs, lhs_endpoint),
                 endpoint_port(rhs, rhs_endpoint))) {
    return false;
  }

  return arrows_compatible(endpoint_arrow(lhs, lhs_endpoint),
                           endpoint_arrow(rhs, rhs_endpoint));
}

static void normalize_edge(const concentrate_compat_state_t *state, edge_t *e,
                           concentrate_normalized_edge_t *normalized) {
  *normalized = (concentrate_normalized_edge_t){0};
  normalized->edge = concentrate_normal_edge(e);
  if (normalized->edge == NULL)
    return;

  normalized->samehead =
      state->samehead == NULL ? "" : agxget(normalized->edge, state->samehead);
  normalized->sametail =
      state->sametail == NULL ? "" : agxget(normalized->edge, state->sametail);
  normalized->head_port = ED_head_port(normalized->edge);
  normalized->tail_port = ED_tail_port(normalized->edge);
  normalized->has_head_or_tail_label =
      ED_head_label(normalized->edge) != NULL ||
      ED_tail_label(normalized->edge) != NULL;
  arrow_flags_with_attrs(normalized->edge, state->dir, state->arrowhead,
                         state->arrowtail, &normalized->tail_arrow,
                         &normalized->head_arrow);
}

void concentrate_edge_pair_compat_init(const concentrate_compat_state_t *state,
                                       edge_t *e, edge_t *f,
                                       concentrate_edge_pair_compat_t *compat) {
  concentrate_normalized_edge_t lhs;
  concentrate_normalized_edge_t rhs;

  *compat = (concentrate_edge_pair_compat_t){0};
  normalize_edge(state, e, &lhs);
  normalize_edge(state, f, &rhs);
  if (lhs.edge == NULL || rhs.edge == NULL)
    return;
  /*
   * Endpoint labels are anchored to a concrete head or tail. A concentrated
   * edge has only one head-label and one tail-label drawing slot, so merging
   * labeled endpoint pairs would either drop a label or attach it to the
   * wrong physical end of the shared route.
   */
  if (lhs.has_head_or_tail_label || rhs.has_head_or_tail_label)
    return;

  compat->same_nonendpoint_attrs =
      same_nonendpoint_edge_attrs(state, lhs.edge, rhs.edge);
  compat->parallel_endpoints_compatible =
      endpoint_compatible(&lhs, CONCENTRATE_ENDPOINT_TAIL, &rhs,
                          CONCENTRATE_ENDPOINT_TAIL,
                          parallel_endpoint_arrows_compatible) &&
      endpoint_compatible(&lhs, CONCENTRATE_ENDPOINT_HEAD, &rhs,
                          CONCENTRATE_ENDPOINT_HEAD,
                          parallel_endpoint_arrows_compatible);
  compat->opposite_endpoints_compatible =
      endpoint_compatible(&lhs, CONCENTRATE_ENDPOINT_TAIL, &rhs,
                          CONCENTRATE_ENDPOINT_HEAD,
                          opposite_endpoint_arrows_compatible) &&
      endpoint_compatible(&lhs, CONCENTRATE_ENDPOINT_HEAD, &rhs,
                          CONCENTRATE_ENDPOINT_TAIL,
                          opposite_endpoint_arrows_compatible);
  compat->parallel_mergeable =
      compat->same_nonendpoint_attrs && compat->parallel_endpoints_compatible;
  compat->opposite_mergeable =
      compat->same_nonendpoint_attrs && compat->opposite_endpoints_compatible;
}

bool concentrate_edge_pair_mergeable(
    const concentrate_edge_pair_compat_t *compat,
    concentrate_edge_relation_t relation) {
  switch (relation) {
  case CONCENTRATE_RELATION_PARALLEL:
    return compat->parallel_mergeable;
  case CONCENTRATE_RELATION_OPPOSITE:
    return compat->opposite_mergeable;
  case CONCENTRATE_RELATION_NONE:
    return false;
  }
  return false;
}

bool concentrate_edges_mergeable(const concentrate_compat_state_t *state,
                                 edge_t *e, edge_t *f) {
  concentrate_edge_pair_compat_t compat;

  concentrate_edge_pair_compat_init(state, e, f, &compat);
  return concentrate_edge_pair_mergeable(&compat,
                                         concentrate_edge_relation(e, f));
}

concentrate_edge_relation_t concentrate_edge_relation(edge_t *e, edge_t *f) {
  if (e == NULL || f == NULL)
    return CONCENTRATE_RELATION_NONE;
  if (agtail(e) == agtail(f) && aghead(e) == aghead(f))
    return CONCENTRATE_RELATION_PARALLEL;
  if (agtail(e) == aghead(f) && aghead(e) == agtail(f))
    return CONCENTRATE_RELATION_OPPOSITE;
  return CONCENTRATE_RELATION_NONE;
}
