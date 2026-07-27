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

#include <common/concentrate_junction.h>

bool concentrate_junction_skip_node(const node_t *n) {
  return ND_concentrate_junction(n);
}

bool concentrate_junction_skip_edge(const edge_t *e) {
  return ED_concentrate_junction_internal(e);
}

size_t concentrate_junction_draw_spline_count(const edge_t *e) {
  if (ED_spl(e) == NULL) {
    return 0;
  }
  size_t emit_splines = ED_spl(e)->size;
  if (!ED_concentrate_junction_draw_trunk(e) &&
      ED_concentrate_junction_emit_splines(e) > 0) {
    emit_splines = ED_concentrate_junction_emit_splines(e);
    if (emit_splines > ED_spl(e)->size) {
      emit_splines = ED_spl(e)->size;
    }
  }
  return emit_splines;
}

textlabel_t *concentrate_junction_label(const edge_t *e) {
  if (ED_concentrate_junction_emit_splines(e) == 0 ||
      ED_concentrate_junction_draw_trunk(e)) {
    return ED_label(e);
  }
  return NULL;
}
