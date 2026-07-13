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
#include <common/utils.h>
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
  bool tail_clip;
  bool head_clip;
  uint32_t tail_arrow;
  uint32_t head_arrow;
  bool has_unmappable_endpoint_attrs;
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
      .headclip = agfindedgeattr(g, "headclip"),
      .tailclip = agfindedgeattr(g, "tailclip"),
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
         attr == state->headclip || attr == state->tailclip ||
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

static bool endpoint_clip(const concentrate_normalized_edge_t *edge,
                          concentrate_endpoint_t endpoint) {
  return endpoint == CONCENTRATE_ENDPOINT_HEAD ? edge->head_clip
                                               : edge->tail_clip;
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

  if (endpoint_clip(lhs, lhs_endpoint) != endpoint_clip(rhs, rhs_endpoint))
    return false;

  if (!same_port(endpoint_port(lhs, lhs_endpoint),
                 endpoint_port(rhs, rhs_endpoint))) {
    return false;
  }

  return arrows_compatible(endpoint_arrow(lhs, lhs_endpoint),
                           endpoint_arrow(rhs, rhs_endpoint));
}

static bool edge_clips(edge_t *edge, Agsym_t *attr) {
  if (attr == NULL)
    return true;

  const char *const value = agxget(edge, attr);
  return value == NULL || value[0] == '\0' || mapbool(value);
}

static bool has_unmappable_endpoint_attrs(edge_t *edge) {
  static char *const endpoint_attrs[] = {
      "headURL", "headhref", "headtarget", "headtooltip",
      "tailURL", "tailhref", "tailtarget", "tailtooltip",
  };

  if (ED_head_label(edge) != NULL || ED_tail_label(edge) != NULL)
    return true;

  for (size_t i = 0; i < sizeof(endpoint_attrs) / sizeof(endpoint_attrs[0]);
       i++) {
    const char *const value = agget(edge, endpoint_attrs[i]);
    if (value != NULL && value[0] != '\0')
      return true;
  }
  return false;
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
  normalized->head_clip = edge_clips(normalized->edge, state->headclip);
  normalized->tail_clip = edge_clips(normalized->edge, state->tailclip);
  normalized->has_unmappable_endpoint_attrs =
      has_unmappable_endpoint_attrs(normalized->edge);
  arrow_flags_with_attrs(normalized->edge, state->dir, state->arrowhead,
                         state->arrowtail, &normalized->tail_arrow,
                         &normalized->head_arrow);
}

static void hash_bytes(uint64_t *hash, const void *bytes, size_t size) {
  const unsigned char *const data = bytes;

  for (size_t i = 0; i < size; i++) {
    *hash ^= data[i];
    *hash *= UINT64_C(1099511628211);
  }
}

static void hash_string(uint64_t *hash, const char *value) {
  const char *const string = value == NULL ? "" : value;

  hash_bytes(hash, string, strlen(string) + 1);
}

static void hash_bool(uint64_t *hash, bool value) {
  const unsigned char byte = value;

  hash_bytes(hash, &byte, sizeof(byte));
}

static void hash_uint32(uint64_t *hash, uint32_t value) {
  hash_bytes(hash, &value, sizeof(value));
}

static void hash_double(uint64_t *hash, double value) {
  if (value == 0.0)
    value = 0.0;
  hash_bytes(hash, &value, sizeof(value));
}

static void hash_port(uint64_t *hash, port value) {
  hash_bool(hash, value.defined);
  if (!value.defined)
    return;
  hash_double(hash, value.p.x);
  hash_double(hash, value.p.y);
}

static bool port_is_indexable(port value) {
  return !value.defined || (value.p.x == value.p.x && value.p.y == value.p.y);
}

static uint64_t
nonendpoint_edge_attrs_hash(const concentrate_compat_state_t *state,
                            edge_t *edge) {
  graph_t *const g = agroot(agraphof(edge));
  uint64_t hash = UINT64_C(1469598103934665603);

  for (Agsym_t *attr = agnxtattr(g, AGEDGE, NULL); attr != NULL;
       attr = agnxtattr(g, AGEDGE, attr)) {
    if (!is_concentrate_endpoint_attr(state, attr))
      hash_string(&hash, agxget(edge, attr));
  }
  return hash;
}

static void hash_endpoint(uint64_t *hash,
                          const concentrate_normalized_edge_t *edge,
                          concentrate_endpoint_t endpoint, bool include_group,
                          bool include_arrow) {
  if (include_group)
    hash_string(hash, endpoint_group(edge, endpoint));
  hash_port(hash, endpoint_port(edge, endpoint));
  hash_bool(hash, endpoint_clip(edge, endpoint));
  if (include_arrow)
    hash_uint32(hash, endpoint_arrow(edge, endpoint));
}

void concentrate_edge_fingerprint_init(
    const concentrate_compat_state_t *state, edge_t *e,
    concentrate_edge_fingerprint_t *fingerprint) {
  concentrate_normalized_edge_t edge;
  concentrate_endpoint_t low_endpoint;
  concentrate_endpoint_t high_endpoint;
  uint64_t hash;

  *fingerprint = (concentrate_edge_fingerprint_t){0};
  normalize_edge(state, e, &edge);
  if (edge.edge == NULL || edge.has_unmappable_endpoint_attrs ||
      !port_is_indexable(edge.tail_port) ||
      !port_is_indexable(edge.head_port)) {
    return;
  }

  hash = nonendpoint_edge_attrs_hash(state, edge.edge);
  hash_endpoint(&hash, &edge, CONCENTRATE_ENDPOINT_TAIL, true, true);
  hash_endpoint(&hash, &edge, CONCENTRATE_ENDPOINT_HEAD, true, true);
  fingerprint->parallel = hash;
  fingerprint->parallel_indexable = true;

  if (agtail(edge.edge) == aghead(edge.edge) || edge.samehead[0] != '\0' ||
      edge.sametail[0] != '\0') {
    return;
  }

  low_endpoint = AGSEQ(agtail(edge.edge)) < AGSEQ(aghead(edge.edge))
                     ? CONCENTRATE_ENDPOINT_TAIL
                     : CONCENTRATE_ENDPOINT_HEAD;
  high_endpoint = low_endpoint == CONCENTRATE_ENDPOINT_TAIL
                      ? CONCENTRATE_ENDPOINT_HEAD
                      : CONCENTRATE_ENDPOINT_TAIL;
  hash = nonendpoint_edge_attrs_hash(state, edge.edge);
  hash_endpoint(&hash, &edge, low_endpoint, false, false);
  hash_endpoint(&hash, &edge, high_endpoint, false, false);
  fingerprint->opposite_base = hash;
  fingerprint->opposite_has_wildcard_arrow =
      endpoint_arrow(&edge, low_endpoint) == 0 ||
      endpoint_arrow(&edge, high_endpoint) == 0;
  hash_endpoint(&hash, &edge, low_endpoint, false, true);
  hash_endpoint(&hash, &edge, high_endpoint, false, true);
  fingerprint->opposite = hash;
  fingerprint->opposite_indexable = true;
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
   * Endpoint labels and hyperlink metadata are anchored to a concrete head or
   * tail. A concentrated edge has one drawing slot per end, so merging would
   * either drop the endpoint data or attach it to the wrong physical end.
   */
  if (lhs.has_unmappable_endpoint_attrs || rhs.has_unmappable_endpoint_attrs)
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
