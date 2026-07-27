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

#include <common/edgejunction.h>

bool edgejunction_skip_node(const node_t *n) { return ND_edgejunction(n); }

bool edgejunction_skip_edge(const edge_t *e) {
  return ED_edgejunction_internal(e);
}

size_t edgejunction_draw_spline_count(const edge_t *e) {
  if (ED_spl(e) == NULL) {
    return 0;
  }
  size_t emit_splines = ED_spl(e)->size;
  if (!ED_edgejunction_draw_trunk(e) && ED_edgejunction_emit_splines(e) > 0) {
    emit_splines = ED_edgejunction_emit_splines(e);
    if (emit_splines > ED_spl(e)->size) {
      emit_splines = ED_spl(e)->size;
    }
  }
  return emit_splines;
}

textlabel_t *edgejunction_label(const edge_t *e) {
  if (ED_edgejunction_emit_splines(e) == 0 || ED_edgejunction_draw_trunk(e)) {
    return ED_label(e);
  }
  return NULL;
}
