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

#include <dotgen/flat_edge_splines.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <util/gv_math.h>

void flat_edge_straighten_port_line(bezier *spline) {
  if (spline->size <= 4)
    return;

  const pointf start = spline->list[0];
  const pointf end = spline->list[spline->size - 1];
  for (size_t i = 1; i + 1 < spline->size; ++i) {
    const double fraction = (double)i / (double)(spline->size - 1);
    spline->list[i] = (pointf){.x = start.x + (end.x - start.x) * fraction,
                               .y = start.y + (end.y - start.y) * fraction};
  }
}

void flat_edge_nudge_around_cluster_border(graph_t *g, pointf *c1, pointf *c2) {
  const double cluster_border_clearance = 8.0;
  const double colinear_epsilon = 0.5;
  const double min_overlap = 4.0;

  if (fabs(c1->y - c2->y) <= colinear_epsilon) {
    const double y = (c1->y + c2->y) / 2.0;
    const double low = MIN(c1->x, c2->x);
    const double high = MAX(c1->x, c2->x);
    for (int c = 1; c <= GD_n_cluster(g); c++) {
      const boxf bb = GD_bb(GD_clust(g)[c]);
      const double overlap = MIN(high, bb.UR.x) - MAX(low, bb.LL.x);
      if (overlap < min_overlap)
        continue;
      if (fabs(y - bb.LL.y) <= colinear_epsilon) {
        c1->y -= cluster_border_clearance;
        c2->y -= cluster_border_clearance;
        return;
      }
      if (fabs(y - bb.UR.y) <= colinear_epsilon) {
        c1->y += cluster_border_clearance;
        c2->y += cluster_border_clearance;
        return;
      }
    }
  }

  if (fabs(c1->x - c2->x) <= colinear_epsilon) {
    const double x = (c1->x + c2->x) / 2.0;
    const double low = MIN(c1->y, c2->y);
    const double high = MAX(c1->y, c2->y);
    for (int c = 1; c <= GD_n_cluster(g); c++) {
      const boxf bb = GD_bb(GD_clust(g)[c]);
      const double overlap = MIN(high, bb.UR.y) - MAX(low, bb.LL.y);
      if (overlap < min_overlap)
        continue;
      if (fabs(x - bb.LL.x) <= colinear_epsilon) {
        c1->x -= cluster_border_clearance;
        c2->x -= cluster_border_clearance;
        return;
      }
      if (fabs(x - bb.UR.x) <= colinear_epsilon) {
        c1->x += cluster_border_clearance;
        c2->x += cluster_border_clearance;
        return;
      }
    }
  }
}
