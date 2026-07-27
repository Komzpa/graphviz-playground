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

#include <common/render.h>
#include <common/utils.h>
#include <dotgen/dot.h>
#include <limits.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>
#include <util/streq.h>

typedef struct junction_edge_s {
  edge_t *arm;
  edge_t *trunk;
  bool draw_trunk;
  bool reverse;
  bool fanout;
} junction_edge_t;

typedef struct {
  node_t *anchor;
  char *attrs[34];
  edge_t **edges;
  size_t size;
  size_t capacity;
} junction_group_t;

typedef enum {
  JUNCTION_FANIN,
  JUNCTION_FANOUT,
} junction_kind_t;

typedef struct {
  bool fanin;
  bool fanout;
} junction_mode_t;

static char *group_attrs[] = {
    "label",       "color",        "style",        "penwidth",    "fontname",
    "fontsize",    "fontcolor",    "dir",          "arrowhead",   "arrowtail",
    "arrowsize",   "decorate",     "labelaligned", "layer",       "URL",
    "href",        "edgeURL",      "edgehref",     "labelURL",    "labelhref",
    "headURL",     "headhref",     "tailURL",      "tailhref",    "tooltip",
    "edgetooltip", "labeltooltip", "headtooltip",  "tailtooltip", "target",
    "edgetarget",  "labeltarget",  "headtarget",   "tailtarget",
};

static char *attr(edge_t *e, attrsym_t *sym, char *fallback) {
  if (sym == NULL) {
    return fallback;
  }
  char *s = agxget(e, sym);
  return s == NULL ? fallback : s;
}

static bool eligible(edge_t *e) {
  if (agtail(e) == aghead(e)) {
    return false;
  }
  if (nonconstraint_edge(e)) {
    return false;
  }
  if (ED_head_label(e) || ED_tail_label(e) || ED_xlabel(e)) {
    return false;
  }
  if (attr(e, agfindedgeattr(agraphof(agtail(e)), "tailport"), "")[0] ||
      attr(e, agfindedgeattr(agraphof(agtail(e)), "headport"), "")[0]) {
    return false;
  }
  const char *dir = attr(e, agfindedgeattr(agraphof(agtail(e)), "dir"), "");
  if (dir[0] != '\0' && !streq(dir, "forward")) {
    return false;
  }
  return true;
}

static node_t *group_anchor(edge_t *e, junction_kind_t kind) {
  return kind == JUNCTION_FANOUT ? agtail(e) : aghead(e);
}

static bool same_group(const junction_group_t *group, edge_t *e,
                       junction_kind_t kind) {
  if (group->anchor != group_anchor(e, kind)) {
    return false;
  }
  for (size_t i = 0; i < ARRAY_SIZE(group_attrs); ++i) {
    attrsym_t *sym = agfindedgeattr(agraphof(agtail(e)), group_attrs[i]);
    if (!streq(group->attrs[i], attr(e, sym, ""))) {
      return false;
    }
  }
  return true;
}

static void append_edge(junction_group_t *group, edge_t *e) {
  if (group->size == group->capacity) {
    group->capacity = group->capacity == 0 ? 4 : group->capacity * 2;
    group->edges = gv_recalloc(group->edges, group->size, group->capacity,
                               sizeof(edge_t *));
  }
  group->edges[group->size++] = e;
}

static const char *directed_dir(bool keep_head_arrow, bool keep_tail_arrow,
                                bool reverse) {
  if (keep_head_arrow && keep_tail_arrow) {
    return "both";
  }
  if (keep_head_arrow) {
    return reverse ? "back" : "forward";
  }
  if (keep_tail_arrow) {
    return reverse ? "forward" : "back";
  }
  return "none";
}

static void copy_edge_attrs(edge_t *dst, edge_t *src, bool with_label,
                            bool keep_head_arrow, bool keep_tail_arrow,
                            bool reverse) {
  if (E_color) {
    agxset(dst, E_color, attr(src, E_color, ""));
  }
  if (E_style) {
    agxset(dst, E_style, attr(src, E_style, ""));
  }
  if (E_penwidth) {
    agxset(dst, E_penwidth, attr(src, E_penwidth, ""));
  }
  if (E_fontname) {
    agxset(dst, E_fontname, attr(src, E_fontname, ""));
  }
  if (E_fontsize) {
    agxset(dst, E_fontsize, attr(src, E_fontsize, ""));
  }
  if (E_fontcolor) {
    agxset(dst, E_fontcolor, attr(src, E_fontcolor, ""));
  }
  if (E_label) {
    agxset(dst, E_label, with_label ? attr(src, E_label, "") : "");
  }
  attrsym_t *dir = agfindedgeattr(agraphof(agtail(src)), "dir");
  if (dir) {
    agxset(dst, dir, directed_dir(keep_head_arrow, keep_tail_arrow, reverse));
  }
  attrsym_t *arrowhead = agfindedgeattr(agraphof(agtail(src)), "arrowhead");
  attrsym_t *arrowtail = agfindedgeattr(agraphof(agtail(src)), "arrowtail");
  if (arrowhead &&
      ((keep_head_arrow && !reverse) || (keep_tail_arrow && reverse))) {
    agxset(dst, arrowhead,
           keep_tail_arrow && reverse ? attr(src, arrowtail, "")
                                      : attr(src, arrowhead, ""));
  }
  if (arrowtail &&
      ((keep_head_arrow && reverse) || (keep_tail_arrow && !reverse))) {
    agxset(dst, arrowtail,
           keep_head_arrow && reverse ? attr(src, arrowhead, "")
                                      : attr(src, arrowtail, ""));
  }
  attrsym_t *arrowsize = agfindedgeattr(agraphof(agtail(src)), "arrowsize");
  if (arrowsize && (keep_head_arrow || keep_tail_arrow)) {
    agxset(dst, arrowsize, attr(src, arrowsize, ""));
  }
  attrsym_t *tailclip = agfindedgeattr(agraphof(agtail(src)), "tailclip");
  if (tailclip) {
    agxset(dst, tailclip, attr(src, tailclip, ""));
  }
  attrsym_t *headclip = agfindedgeattr(agraphof(agtail(src)), "headclip");
  if (headclip) {
    agxset(dst, headclip, attr(src, headclip, ""));
  }
}

static node_t *fresh_junction_node(graph_t *g, size_t *index) {
  char name[64];
  do {
    snprintf(name, sizeof(name), "_edgejunction_%zu", (*index)++);
  } while (agnode(g, name, 0) != NULL);
  return agnode(g, name, 1);
}

static void init_added_node(node_t *n) {
  agbindrec(n, "Agnodeinfo_t", sizeof(Agnodeinfo_t), true);
  common_init_node(n);
  gv_nodesize(n, GD_flip(agraphof(n)));
  alloc_elist(4, ND_in(n));
  alloc_elist(4, ND_out(n));
  alloc_elist(2, ND_flat_in(n));
  alloc_elist(2, ND_flat_out(n));
  alloc_elist(2, ND_other(n));
  ND_UF_size(n) = 1;
}

static void init_added_edge(edge_t *e) {
  agbindrec(e, "Agedgeinfo_t", sizeof(Agedgeinfo_t), true);
  common_init_edge(e);
  ED_weight(e) = late_int(e, E_weight, 1, 0);
  ED_count(e) = ED_xpenalty(e) = 1;
  ED_minlen(e) = late_int(e, E_minlen, 1, 0);
  dot_bundle_load_init_original(e);
}

static void make_group(graph_t *g, const junction_group_t *group, size_t *index,
                       junction_kind_t kind, bool reverse) {
  edge_t *rep = group->edges[0];

  agattr_text(g, AGNODE, "label", "");
  agattr_text(g, AGNODE, "shape", "ellipse");
  agattr_text(g, AGNODE, "style", "");
  agattr_text(g, AGNODE, "width", "");
  agattr_text(g, AGNODE, "height", "");
  agattr_text(g, AGNODE, "_edgejunction_node", "");
  agattr_text(g, AGEDGE, "color", "");
  agattr_text(g, AGEDGE, "style", "");
  agattr_text(g, AGEDGE, "penwidth", "");
  agattr_text(g, AGEDGE, "fontname", "");
  agattr_text(g, AGEDGE, "fontsize", "");
  agattr_text(g, AGEDGE, "fontcolor", "");
  agattr_text(g, AGEDGE, "label", "");
  agattr_text(g, AGEDGE, "dir", "");
  agattr_text(g, AGEDGE, "arrowhead", "");
  agattr_text(g, AGEDGE, "arrowtail", "");
  agattr_text(g, AGEDGE, "arrowsize", "");
  agattr_text(g, AGEDGE, "_edgejunction_original", "");
  agattr_text(g, AGEDGE, "_edgejunction_internal", "");
  agattr_text(g, AGEDGE, "_edgejunction_arm_splines", "");
  agattr_text(g, AGEDGE, "_edgejunction_draw_trunk", "");

  node_t *jn = fresh_junction_node(g, index);
  if (N_label) {
    agxset(jn, N_label, "");
  }
  if (N_shape) {
    agxset(jn, N_shape, "point");
  }
  if (N_style) {
    agxset(jn, N_style, "invis");
  }
  if (N_width) {
    agxset(jn, N_width, "0.02");
  }
  if (N_height) {
    agxset(jn, N_height, "0.02");
  }
  agsafeset(jn, "label", "", "");
  agsafeset(jn, "shape", "point", "");
  agsafeset(jn, "style", "invis", "");
  agsafeset(jn, "width", "0.02", "");
  agsafeset(jn, "height", "0.02", "");
  agsafeset(jn, "_edgejunction_node", "true", "");
  init_added_node(jn);
  ND_edgejunction(jn) = true;
  ND_shape(jn) = bind_shape("point", jn);
  ND_width(jn) = 0.02;
  ND_height(jn) = 0.02;
  gv_nodesize(jn, GD_flip(g));

  int weight = 0;
  edge_t *trunk = NULL;
  if (kind == JUNCTION_FANOUT) {
    trunk = reverse ? agedge(g, jn, group->anchor, NULL, 1)
                    : agedge(g, group->anchor, jn, NULL, 1);
    copy_edge_attrs(trunk, rep, true, false, false, reverse);
  } else {
    trunk = reverse ? agedge(g, group->anchor, jn, NULL, 1)
                    : agedge(g, jn, group->anchor, NULL, 1);
    copy_edge_attrs(trunk, rep, true, true, false, reverse);
  }
  agsafeset(trunk, "_edgejunction_internal", "true", "");
  init_added_edge(trunk);
  ED_edgejunction_internal(trunk) = true;
  for (size_t i = 0; i < group->size; ++i) {
    edge_t *orig = group->edges[i];
    const int orig_weight = ED_weight(orig);
    agsafeset(orig, "_edgejunction_original", "true", "");
    ED_minlen(orig) = 0;
    ED_weight(orig) = 0;
    ED_xpenalty(orig) = 0;
    edge_t *arm = NULL;
    if (kind == JUNCTION_FANOUT) {
      arm = reverse ? agedge(g, aghead(orig), jn, NULL, 1)
                    : agedge(g, jn, aghead(orig), NULL, 1);
      copy_edge_attrs(arm, orig, false, true, false, reverse);
    } else {
      arm = reverse ? agedge(g, jn, agtail(orig), NULL, 1)
                    : agedge(g, agtail(orig), jn, NULL, 1);
      copy_edge_attrs(arm, orig, false, false, false, reverse);
    }
    agsafeset(arm, "_edgejunction_internal", "true", "");
    init_added_edge(arm);
    ED_edgejunction_internal(arm) = true;
    ED_weight(arm) = orig_weight;
    weight += orig_weight;

    junction_edge_t *info = gv_calloc(1, sizeof(junction_edge_t));
    info->arm = arm;
    info->trunk = trunk;
    info->draw_trunk = i == 0;
    info->reverse = reverse;
    info->fanout = kind == JUNCTION_FANOUT;
    ED_edgejunction(orig) = info;
  }

  const int64_t trunk_scale = group->attrs[0][0] ? (int64_t)group->size : 1;
  const int64_t trunk_weight = (int64_t)weight * trunk_scale;
  ED_weight(trunk) = trunk_weight > INT_MAX ? INT_MAX : (int)trunk_weight;
}

static junction_mode_t edgejunction_mode(graph_t *g) {
  junction_mode_t mode = {0};
  char *value = agget(g, "edgejunction");
  if (value == NULL || streq(value, "") || streq(value, "none") ||
      streq(value, "false")) {
    return mode;
  }
  if (streq(value, "fanin")) {
    mode.fanin = true;
  } else if (streq(value, "fanout")) {
    mode.fanout = true;
  } else if (streq(value, "both") || streq(value, "true")) {
    mode.fanin = true;
    mode.fanout = true;
  }
  return mode;
}

static void make_groups(graph_t *g, junction_kind_t kind, size_t *made) {
  junction_group_t *groups = NULL;
  size_t ngroups = 0;
  size_t capacity = 0;

  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (!eligible(e) || ED_edgejunction(e)) {
        continue;
      }
      size_t i = 0;
      for (; i < ngroups; ++i) {
        if (same_group(&groups[i], e, kind)) {
          break;
        }
      }
      if (i == ngroups) {
        if (ngroups == capacity) {
          capacity = capacity == 0 ? 8 : capacity * 2;
          groups =
              gv_recalloc(groups, ngroups, capacity, sizeof(junction_group_t));
        }
        groups[i].anchor = group_anchor(e, kind);
        for (size_t j = 0; j < ARRAY_SIZE(group_attrs); ++j) {
          attrsym_t *sym = agfindedgeattr(g, group_attrs[j]);
          groups[i].attrs[j] = attr(e, sym, "");
        }
        ngroups++;
      }
      append_edge(&groups[i], e);
    }
  }

  for (size_t i = 0; i < ngroups; ++i) {
    if (groups[i].size >= 2) {
      make_group(g, &groups[i], made, kind, false);
    }
    free(groups[i].edges);
  }
  free(groups);
}

static bool has_cluster_subgraph(graph_t *g) {
  for (graph_t *subg = agfstsubg(g); subg; subg = agnxtsubg(subg)) {
    if (strncmp(agnameof(subg), "cluster", strlen("cluster")) == 0 ||
        has_cluster_subgraph(subg)) {
      return true;
    }
  }
  return false;
}

void dot_edgejunction(graph_t *g) {
  junction_mode_t mode = edgejunction_mode(g);
  if (!mode.fanin && !mode.fanout) {
    return;
  }

  if (!agisdirected(g) || GD_flip(g)) {
    return;
  }
  if (GD_n_cluster(g) != 0 || has_cluster_subgraph(g)) {
    agsafeset(g, "_edgejunction_clustered_fallback", "true", "");
    return;
  }

  // Junction routing replaces legacy concentrate for this graph even when a
  // v1 eligibility restriction makes the transform itself a no-op.
  Concentrate = false;

  size_t made = 0;
  if (mode.fanin) {
    make_groups(g, JUNCTION_FANIN, &made);
  }
  if (mode.fanout) {
    make_groups(g, JUNCTION_FANOUT, &made);
  }
}

void dot_edgejunction_save_rankleader(graph_t *g, int r) {
  if (GD_rank(g)[r].n == 0 &&
      mapbool(agget(dot_root(g), "_edgejunction_clustered_fallback"))) {
    GD_rankleader(g)[r] = NULL;
    return;
  }
  GD_rankleader(g)[r] = GD_rank(g)[r].v[0];
}

static void reverse_bezier(bezier *bz) {
  for (size_t i = 0; i < bz->size / 2; ++i) {
    pointf p = bz->list[i];
    bz->list[i] = bz->list[bz->size - 1 - i];
    bz->list[bz->size - 1 - i] = p;
  }
  uint32_t flag = bz->sflag;
  bz->sflag = bz->eflag;
  bz->eflag = flag;
  pointf p = bz->sp;
  bz->sp = bz->ep;
  bz->ep = p;
}

static pointf bezier_start(const bezier *bz) { return bz->list[0]; }

static pointf bezier_end(const bezier *bz) { return bz->list[bz->size - 1]; }

static pointf spline_range_midpoint(const splines *spl, size_t begin,
                                    size_t end) {
  if (spl == NULL || spl->size == 0) {
    return (pointf){0, 0};
  }
  if (begin >= spl->size) {
    begin = spl->size - 1;
  }
  if (end <= begin || end > spl->size) {
    end = spl->size;
  }

  const pointf start = bezier_start(&spl->list[begin]);
  const pointf finish = bezier_end(&spl->list[end - 1]);
  return (pointf){(start.x + finish.x) / 2, (start.y + finish.y) / 2};
}

static double point_distance(pointf a, pointf b) {
  const double dx = a.x - b.x;
  const double dy = a.y - b.y;
  return hypot(dx, dy);
}

static bezier line_bezier(pointf start, pointf end) {
  bezier bz = {0};
  bz.size = 4;
  bz.list = gv_calloc(bz.size, sizeof(pointf));
  bz.list[0] = start;
  bz.list[1] = (pointf){(2 * start.x + end.x) / 3, (2 * start.y + end.y) / 3};
  bz.list[2] = (pointf){(start.x + 2 * end.x) / 3, (start.y + 2 * end.y) / 3};
  bz.list[3] = end;
  return bz;
}

static bezier copy_bezier(const splines *part, size_t part_index,
                          bool reverse) {
  const size_t source_index =
      reverse ? part->size - 1 - part_index : part_index;
  const bezier *src = &part->list[source_index];
  bezier dst = *src;
  dst.list = gv_calloc(src->size, sizeof(pointf));
  memcpy(dst.list, src->list, src->size * sizeof(pointf));
  if (reverse) {
    reverse_bezier(&dst);
  }
  return dst;
}

static splines *copy_joined_splines(const splines *arm, const splines *trunk,
                                    bool reverse, size_t *arm_size) {
  bezier *arm_list = gv_calloc(arm->size, sizeof(bezier));
  bezier *trunk_list = gv_calloc(trunk->size, sizeof(bezier));

  for (size_t i = 0; i < arm->size; ++i) {
    arm_list[i] = copy_bezier(arm, i, reverse);
    arm_list[i].sflag = 0;
    arm_list[i].eflag = 0;
  }
  for (size_t i = 0; i < trunk->size; ++i) {
    trunk_list[i] = copy_bezier(trunk, i, reverse);
  }

  const pointf arm_end = bezier_end(&arm_list[arm->size - 1]);
  const pointf trunk_start = bezier_start(&trunk_list[0]);
  const bool needs_join = point_distance(arm_end, trunk_start) > 0.01;
  *arm_size = arm->size + (needs_join ? 1 : 0);

  splines *joined = gv_calloc(1, sizeof(splines));
  joined->size = *arm_size + trunk->size;
  joined->list = gv_calloc(joined->size, sizeof(bezier));

  size_t out = 0;
  for (size_t i = 0; i < arm->size; ++i) {
    joined->list[out++] = arm_list[i];
  }
  if (needs_join) {
    joined->list[out++] = line_bezier(arm_end, trunk_start);
  }
  for (size_t i = 0; i < trunk->size; ++i) {
    joined->list[out++] = trunk_list[i];
  }

  joined->bb = arm->bb;
  EXPANDBB(&joined->bb, trunk->bb);
  if (needs_join) {
    expandbp(&joined->bb, arm_end);
    expandbp(&joined->bb, trunk_start);
  }
  free(arm_list);
  free(trunk_list);
  return joined;
}

static splines *copy_splines(const splines *part, bool reverse) {
  splines *copy = gv_calloc(1, sizeof(splines));
  copy->size = part->size;
  copy->list = gv_calloc(copy->size, sizeof(bezier));
  for (size_t i = 0; i < part->size; ++i) {
    copy->list[i] = copy_bezier(part, i, reverse);
  }
  copy->bb = part->bb;
  return copy;
}

static splines *copy_connected_arm_splines(const splines *trunk,
                                           const splines *arm, bool reverse) {
  splines *trunk_copy = copy_splines(trunk, reverse);
  splines *arm_copy = copy_splines(arm, reverse);
  const pointf trunk_end = bezier_end(&trunk_copy->list[trunk_copy->size - 1]);
  const pointf arm_start = bezier_start(&arm_copy->list[0]);
  const bool needs_join = point_distance(trunk_end, arm_start) > 0.01;

  splines *connected = gv_calloc(1, sizeof(splines));
  connected->size = arm_copy->size + (needs_join ? 1 : 0);
  connected->list = gv_calloc(connected->size, sizeof(bezier));

  size_t out = 0;
  if (needs_join) {
    connected->list[out++] = line_bezier(trunk_end, arm_start);
  }
  for (size_t i = 0; i < arm_copy->size; ++i) {
    connected->list[out++] = arm_copy->list[i];
  }

  connected->bb = arm_copy->bb;
  if (needs_join) {
    expandbp(&connected->bb, trunk_end);
    expandbp(&connected->bb, arm_start);
  }

  for (size_t i = 0; i < trunk_copy->size; ++i) {
    free(trunk_copy->list[i].list);
  }
  free(trunk_copy->list);
  free(trunk_copy);
  free(arm_copy->list);
  free(arm_copy);
  return connected;
}

void dot_edgejunction_splines(graph_t *g) {
  junction_mode_t mode = edgejunction_mode(g);
  if (!mode.fanin && !mode.fanout) {
    return;
  }

  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (!mapbool(agget(e, "_edgejunction_original"))) {
        continue;
      }
      junction_edge_t *info = ED_edgejunction(e);
      if (info == NULL) {
        continue;
      }
      if (ED_spl(info->arm) == NULL || ED_spl(info->trunk) == NULL) {
        continue;
      }

      size_t arm_size = 0;
      gv_free_splines(e);
      if (info->fanout) {
        if (info->draw_trunk) {
          ED_spl(e) = copy_joined_splines(
              ED_spl(info->trunk), ED_spl(info->arm), info->reverse, &arm_size);
        } else {
          ED_spl(e) = copy_connected_arm_splines(
              ED_spl(info->trunk), ED_spl(info->arm), info->reverse);
          arm_size = ED_spl(e)->size;
        }
      } else {
        ED_spl(e) = copy_joined_splines(ED_spl(info->arm), ED_spl(info->trunk),
                                        info->reverse, &arm_size);
      }
      ED_edgejunction_draw_trunk(e) = info->draw_trunk;
      ED_edgejunction_emit_splines(e) = arm_size;
      uint32_t sflag = 0;
      uint32_t eflag = 0;
      arrow_flags(e, &sflag, &eflag);
      for (size_t i = 0; i < ED_spl(e)->size; ++i) {
        ED_spl(e)->list[i].sflag = 0;
        ED_spl(e)->list[i].eflag = 0;
      }
      ED_spl(e)->list[0].sflag = sflag;
      ED_spl(e)->list[ED_spl(e)->size - 1].eflag = eflag;
      if (info->reverse && eflag != 0) {
        bezier *last = &ED_spl(e)->list[ED_spl(e)->size - 1];
        last->ep = last->list[last->size - 1];
      }
      char nbuf[32];
      snprintf(nbuf, sizeof(nbuf), "%zu", arm_size);
      agsafeset(e, "_edgejunction_arm_splines", nbuf, "");
      agsafeset(e, "_edgejunction_draw_trunk",
                info->draw_trunk ? "true" : "false", "");

      if (ED_label(e) && ED_label(info->trunk)) {
        if (info->fanout) {
          ED_label(e)->pos = spline_range_midpoint(ED_spl(e), 0, arm_size);
        } else {
          ED_label(e)->pos =
              spline_range_midpoint(ED_spl(e), arm_size, ED_spl(e)->size);
        }
        ED_label(e)->set = true;
      }

      ED_edge_type(info->arm) = IGNORED;
      ED_edge_type(info->trunk) = IGNORED;
      if (E_label) {
        agxset(info->trunk, E_label, "");
      }
      free(info);
      ED_edgejunction(e) = NULL;
    }
  }
}
