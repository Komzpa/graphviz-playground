/// @file
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

#include "config.h"

#include <common/types.h>
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
  CONCENTRATE_RELATION_NONE,
  CONCENTRATE_RELATION_PARALLEL,
  CONCENTRATE_RELATION_OPPOSITE,
} concentrate_edge_relation_t;

typedef struct {
  Agsym_t *samehead;
  Agsym_t *sametail;
  Agsym_t *headport;
  Agsym_t *tailport;
  Agsym_t *headclip;
  Agsym_t *tailclip;
  Agsym_t *arrowhead;
  Agsym_t *arrowtail;
  Agsym_t *dir;
} concentrate_compat_state_t;

/*
 * Hashes for the two relations used by concentrate. They are filters, not
 * proofs. Relation-aware compatibility decides whether an edge may be
 * suppressed.
 */
typedef struct {
  uint64_t parallel;
  uint64_t opposite;
  uint64_t opposite_base;
  bool parallel_indexable;
  bool opposite_indexable;
  bool opposite_has_wildcard_arrow;
} concentrate_edge_fingerprint_t;

void concentrate_compat_state_init(graph_t *g,
                                   concentrate_compat_state_t *state);
bool concentrate_edges_compatible_for_relation(
    const concentrate_compat_state_t *state, edge_t *e, edge_t *f,
    concentrate_edge_relation_t relation);
bool concentrate_edges_mergeable(const concentrate_compat_state_t *state,
                                 edge_t *e, edge_t *f);
void concentrate_edge_fingerprint_init(
    const concentrate_compat_state_t *state, edge_t *e,
    concentrate_edge_fingerprint_t *fingerprint);
concentrate_edge_relation_t concentrate_edge_relation(edge_t *e, edge_t *f);
edge_t *concentrate_normal_edge(edge_t *e);

#ifdef __cplusplus
}
#endif
