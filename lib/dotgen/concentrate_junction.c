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

#include <common/concentrate_plan.h>
#include <common/render.h>
#include <common/utils.h>
#include <dotgen/bundle_load.h>
#include <dotgen/dot.h>
#include <limits.h>
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
  char *sameport_id;
  bool sameport;
  char *attrs[34];
  edge_t **edges;
  size_t size;
  size_t capacity;
} junction_group_t;

typedef enum {
  JUNCTION_FANIN,
  JUNCTION_FANOUT,
} junction_kind_t;

enum {
  GROUP_ATTR_COLOR = 1,
};

static node_t *group_anchor(edge_t *e, junction_kind_t kind);

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

static bool node_has_record_endpoint_geometry(node_t *n) {
  return shapeOf(n) == SH_RECORD || (ND_label(n) != NULL && ND_label(n)->html);
}

static bool edge_has_record_endpoint_geometry(edge_t *e) {
  return node_has_record_endpoint_geometry(agtail(e)) ||
         node_has_record_endpoint_geometry(aghead(e));
}

static bool endpoint_has_port(edge_t *e) {
  return ED_tail_port(e).defined || ED_head_port(e).defined ||
         attr(e, agfindedgeattr(agraphof(agtail(e)), "tailport"), "")[0] ||
         attr(e, agfindedgeattr(agraphof(agtail(e)), "headport"), "")[0] ||
         attr(e, agfindedgeattr(agraphof(agtail(e)), "sametail"), "")[0] ||
         attr(e, agfindedgeattr(agraphof(agtail(e)), "samehead"), "")[0];
}

static bool endpoint_has_explicit_port(edge_t *e) {
  return ED_tail_port(e).defined || ED_head_port(e).defined ||
         attr(e, agfindedgeattr(agraphof(agtail(e)), "tailport"), "")[0] ||
         attr(e, agfindedgeattr(agraphof(agtail(e)), "headport"), "")[0];
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
  if (endpoint_has_port(e)) {
    return false;
  }
  const char *dir = attr(e, agfindedgeattr(agraphof(agtail(e)), "dir"), "");
  if (dir[0] != '\0' && !streq(dir, "forward")) {
    return false;
  }
  return true;
}

static bool eligible_sameport(edge_t *e) {
  if (agtail(e) == aghead(e)) {
    return false;
  }
  if (nonconstraint_edge(e)) {
    return false;
  }
  if (ED_label(e) || ED_xlabel(e)) {
    return false;
  }
  if (endpoint_has_explicit_port(e)) {
    return false;
  }
  const char *dir = attr(e, agfindedgeattr(agraphof(agtail(e)), "dir"), "");
  if (dir[0] != '\0' && !streq(dir, "forward") && !streq(dir, "none")) {
    return false;
  }
  return true;
}

static bool samearrow_compatible(edge_t *e, junction_kind_t kind) {
  attrsym_t *sym = agfindedgeattr(agraphof(agtail(e)), kind == JUNCTION_FANOUT
                                                           ? "samearrowtail"
                                                           : "samearrowhead");
  return attr(e, sym, "")[0] != '\0';
}

static bool graph_has_same_rank_edge(graph_t *subg, edge_t *e) {
  const char *rank = agget(subg, "rank");
  if (rank != NULL && streq(rank, "same") && agcontains(subg, agtail(e)) &&
      agcontains(subg, aghead(e))) {
    return true;
  }
  for (graph_t *child = agfstsubg(subg); child; child = agnxtsubg(child)) {
    if (graph_has_same_rank_edge(child, e)) {
      return true;
    }
  }
  return false;
}

static bool graph_uses_curved_splines(graph_t *g) {
  const char *splines_attr = agget(g, "splines");
  return splines_attr != NULL && streq(splines_attr, "curved");
}

static bool edge_has_copied_junction_attribute(edge_t *e) {
  for (size_t i = 0; i < ARRAY_SIZE(group_attrs); ++i) {
    attrsym_t *sym = agfindedgeattr(agraphof(agtail(e)), group_attrs[i]);
    if (attr(e, sym, "")[0]) {
      return true;
    }
  }
  return false;
}

static bool same_concentration_endpoints(edge_t *a, edge_t *b) {
  return agtail(a) == agtail(b) && aghead(a) == aghead(b);
}

static bool edge_has_true_multiedge_peer(edge_t *e) {
  for (edge_t *other = agfstout(agraphof(e), agtail(e)); other;
       other = agnxtout(agraphof(e), other)) {
    if (other != e && same_concentration_endpoints(e, other) &&
        gv_concentration_edges_have_equal_rendered_identity(
            e, other, GV_CONCENTRATION_SAME_DIRECTION)) {
      return true;
    }
  }
  return false;
}

static bool edge_has_concentrated_peer(edge_t *e) {
  for (node_t *n = agfstnode(agraphof(e)); n; n = agnxtnode(agraphof(e), n)) {
    for (edge_t *other = agfstout(agraphof(e), n); other;
         other = agnxtout(agraphof(e), other)) {
      if (other != e && same_concentration_endpoints(e, other) &&
          gv_concentration_edges_have_equal_rendered_identity(
              e, other, GV_CONCENTRATION_SAME_DIRECTION)) {
        return true;
      }
    }
  }
  return false;
}

static bool graph_has_refused_concentrated_edge(edge_t *e) {
  graph_t *g = agraphof(agtail(e));
  if (!Concentrate) {
    return false;
  }
  /* A labelled fan is what this feature exists to draw: four labels collapsing
   * to one each is the demonstration. Refusing it to avoid a separate kink
   * defect throws away the working half, so the label alone is not grounds to
   * refuse. The kinks are their own class and get their own fix. */
  return edge_has_concentrated_peer(e) && graph_uses_curved_splines(g) &&
         edge_has_copied_junction_attribute(e);
}

static bool refused_edge_kind(graph_t *g, edge_t *e) {
  return agtail(e) == aghead(e) || edge_has_record_endpoint_geometry(e) ||
         endpoint_has_port(e) || edge_has_true_multiedge_peer(e) ||
         graph_has_same_rank_edge(g, e) ||
         graph_has_refused_concentrated_edge(e);
}

static node_t *group_anchor(edge_t *e, junction_kind_t kind) {
  return kind == JUNCTION_FANOUT ? agtail(e) : aghead(e);
}

static node_t *group_outer_endpoint(edge_t *e, junction_kind_t kind) {
  return kind == JUNCTION_FANOUT ? aghead(e) : agtail(e);
}

static bool same_group(const junction_group_t *group, edge_t *e,
                       junction_kind_t kind) {
  if (group->anchor != group_anchor(e, kind)) {
    return false;
  }
  if (group->sameport) {
    attrsym_t *sym = agfindedgeattr(
        agraphof(agtail(e)), kind == JUNCTION_FANOUT ? "sametail" : "samehead");
    return streq(group->sameport_id, attr(e, sym, ""));
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

static bool group_needs_rank_band_slack(graph_t *g,
                                        const junction_group_t *group,
                                        junction_kind_t kind) {
  node_t **stack = gv_calloc((size_t)agnnodes(g), sizeof(node_t *));
  node_t **seen = gv_calloc((size_t)agnnodes(g), sizeof(node_t *));
  for (size_t i = 0; i < group->size; ++i) {
    node_t *anchor = group_anchor(group->edges[i], kind);
    node_t *outer = group_outer_endpoint(group->edges[i], kind);
    node_t *start = kind == JUNCTION_FANIN ? anchor : outer;
    node_t *target = kind == JUNCTION_FANIN ? outer : anchor;
    size_t nstack = 0;
    size_t nseen = 0;
    stack[nstack++] = start;
    seen[nseen++] = start;
    while (nstack > 0) {
      node_t *n = stack[--nstack];
      for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
        if (ED_concentrate_junction_internal(e)) {
          continue;
        }
        node_t *next = aghead(e);
        if (ND_concentrate_junction(next)) {
          continue;
        }
        if (next == target) {
          free(stack);
          free(seen);
          return true;
        }
        bool already_seen = false;
        for (size_t j = 0; j < nseen; ++j) {
          if (seen[j] == next) {
            already_seen = true;
            break;
          }
        }
        if (!already_seen) {
          seen[nseen++] = next;
          stack[nstack++] = next;
        }
      }
    }
  }
  free(stack);
  free(seen);
  return false;
}

static void snapshot_group_attrs(graph_t *g, junction_group_t *group,
                                 edge_t *e) {
  for (size_t j = 0; j < ARRAY_SIZE(group_attrs); ++j) {
    attrsym_t *sym = agfindedgeattr(g, group_attrs[j]);
    group->attrs[j] = attr(e, sym, "");
  }
}

static textlabel_t **endpoint_label_slot(edge_t *e, junction_kind_t kind) {
  return kind == JUNCTION_FANOUT ? &ED_tail_label(e) : &ED_head_label(e);
}

static char *endpoint_label_text(edge_t *e, junction_kind_t kind) {
  textlabel_t *const label = *endpoint_label_slot(e, kind);
  return label == NULL ? "" : label->text;
}

static attrsym_t *ensure_edge_attr(graph_t *g, char *name) {
  return agattr_text(g, AGEDGE, name, "");
}

static void copy_endpoint_label_attrs(edge_t *dst, edge_t *src,
                                      junction_kind_t kind) {
  graph_t *g = agraphof(agtail(src));
  attrsym_t *label =
      ensure_edge_attr(g, kind == JUNCTION_FANOUT ? "taillabel" : "headlabel");
  attrsym_t *sameport =
      ensure_edge_attr(g, kind == JUNCTION_FANOUT ? "sametail" : "samehead");
  attrsym_t *src_sameport =
      agfindedgeattr(g, kind == JUNCTION_FANOUT ? "sametail" : "samehead");
  agxset(dst, label, endpoint_label_text(src, kind));
  agxset(dst, sameport, attr(src, src_sameport, ""));
  for (size_t i = 0; i < ARRAY_SIZE(group_attrs); ++i) {
    attrsym_t *sym = agfindedgeattr(g, group_attrs[i]);
    if (sym != NULL) {
      attrsym_t *dst_sym = ensure_edge_attr(g, group_attrs[i]);
      agxset(dst, dst_sym, attr(src, sym, ""));
    }
  }
  for (size_t i = 0; i < 5; ++i) {
    static char *label_attrs[] = {"labelfontsize", "labelfontname",
                                  "labelfontcolor", "labeldistance",
                                  "labelangle"};
    attrsym_t *sym = agfindedgeattr(g, label_attrs[i]);
    if (sym != NULL) {
      attrsym_t *dst_sym = ensure_edge_attr(g, label_attrs[i]);
      agxset(dst, dst_sym, attr(src, sym, ""));
    }
  }
}

static void suppress_endpoint_label(edge_t *e, junction_kind_t kind) {
  textlabel_t **slot = endpoint_label_slot(e, kind);
  if (*slot != NULL) {
    free_label(*slot);
    *slot = NULL;
  }
  attrsym_t *sym = agfindedgeattr(
      agraphof(agtail(e)), kind == JUNCTION_FANOUT ? "taillabel" : "headlabel");
  if (sym != NULL) {
    agxset(e, sym, "");
  }
}

static void transfer_endpoint_label(edge_t *dst, edge_t *src,
                                    junction_kind_t kind) {
  textlabel_t **dst_label = endpoint_label_slot(dst, kind);
  textlabel_t **src_label = endpoint_label_slot(src, kind);
  if (*dst_label == NULL && *src_label != NULL && (*src_label)->set) {
    *dst_label = *src_label;
    *src_label = NULL;
  }
}

static boxf trunk_label_box(graph_t *g, const textlabel_t *label, pointf pos) {
  pointf dimen = label->dimen;
  const double padding = 1.0;
  if (GD_flip(g)) {
    const double tmp = dimen.x;
    dimen.x = dimen.y;
    dimen.y = tmp;
  }
  return (boxf){.LL = {.x = pos.x - dimen.x / 2.0 - padding,
                       .y = pos.y - dimen.y / 2.0 - padding},
                .UR = {.x = pos.x + dimen.x / 2.0 + padding,
                       .y = pos.y + dimen.y / 2.0 + padding}};
}

static bool trunk_boxes_overlap(boxf a, boxf b) {
  return MAX(a.LL.x, b.LL.x) < MIN(a.UR.x, b.UR.x) &&
         MAX(a.LL.y, b.LL.y) < MIN(a.UR.y, b.UR.y);
}

static double trunk_point_segment_distance(pointf p, pointf a, pointf b) {
  const double dx = b.x - a.x;
  const double dy = b.y - a.y;
  if (dx == 0.0 && dy == 0.0) {
    return DIST(p, a);
  }
  double t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy);
  t = MAX(0.0, MIN(1.0, t));
  return hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

static double trunk_distance_to_edge(pointf p, edge_t *edge) {
  const splines *spl = ED_spl(edge);
  double best = HUGE_VAL;
  if (spl == NULL) {
    return best;
  }
  for (size_t i = 0; i < spl->size; ++i) {
    const bezier *bz = &spl->list[i];
    for (size_t j = 0; j + 3 < bz->size; j += 3) {
      pointf prev = bz->list[j];
      for (size_t step = 1; step <= 40; ++step) {
        pointf cur = Bezier(&bz->list[j], (double)step / 40.0, NULL, NULL);
        best = MIN(best, trunk_point_segment_distance(p, prev, cur));
        prev = cur;
      }
    }
  }
  return best;
}

static bool trunk_label_has_single_owner(graph_t *g, edge_t *owner,
                                         pointf pos) {
  double nearest = HUGE_VAL;
  edge_t *nearest_edge = NULL;
  size_t candidate_count = 0;
  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (ED_spl(e) == NULL) {
        continue;
      }
      const double distance = trunk_distance_to_edge(pos, e);
      if (distance < nearest) {
        nearest = distance;
        nearest_edge = e;
      }
    }
  }
  if (nearest_edge != owner) {
    return false;
  }
  const double limit = 2.0 * nearest + 1.0;
  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (ED_spl(e) != NULL && trunk_distance_to_edge(pos, e) < limit) {
        candidate_count++;
      }
    }
  }
  return candidate_count == 1;
}

static textlabel_t *edge_endpoint_label(edge_t *e, bool head_p) {
  return head_p ? ED_head_label(e) : ED_tail_label(e);
}

static bool same_label_endpoint(edge_t *a, edge_t *b, bool head_p) {
  return head_p ? aghead(a) == aghead(b) : agtail(a) == agtail(b);
}

static bool trunk_label_overlaps_endpoint_label(graph_t *g, edge_t *owner,
                                                textlabel_t *label, bool head_p,
                                                pointf pos) {
  const boxf label_bounds = trunk_label_box(g, label, pos);
  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (e == owner || !same_label_endpoint(owner, e, head_p)) {
        continue;
      }
      textlabel_t *other = edge_endpoint_label(e, head_p);
      if (other != NULL &&
          trunk_boxes_overlap(label_bounds,
                              trunk_label_box(g, other, other->pos))) {
        return true;
      }
    }
  }
  return false;
}

static bool try_trunk_label_pos(graph_t *g, edge_t *owner, textlabel_t *label,
                                bool head_p, pointf pos) {
  return !trunk_label_overlaps_endpoint_label(g, owner, label, head_p, pos) &&
         trunk_label_has_single_owner(g, owner, pos);
}

static void clear_transferred_endpoint_label(graph_t *g, edge_t *owner,
                                             junction_kind_t kind) {
  const bool head_p = kind == JUNCTION_FANIN;
  textlabel_t *label = edge_endpoint_label(owner, head_p);
  if (label == NULL) {
    return;
  }
  if (!GD_flip(g) && !trunk_label_overlaps_endpoint_label(g, owner, label,
                                                          head_p, label->pos)) {
    return;
  }
  if (GD_flip(g)) {
    label->pos.x -= 1.5;
    updateBB(g, label);
    return;
  }
  const boxf label_bounds = trunk_label_box(g, label, label->pos);
  const double dx = label_bounds.UR.x - label_bounds.LL.x + 0.25;
  const double dy = label_bounds.UR.y - label_bounds.LL.y + 0.25;
  const double ystep = MAX(dy, label->fontsize / 2.0);
  const pointf moves[] = {{0, -ystep},    {0, ystep},      {-dx, 0},
                          {dx, 0},        {-0.75 * dx, 0}, {0.75 * dx, 0},
                          {-dx / 2.0, 0}, {dx / 2.0, 0},   {0, -dy / 4.0},
                          {0, dy / 4.0},  {-dx / 4.0, 0},  {dx / 4.0, 0}};
  for (size_t i = 0; i < ARRAY_SIZE(moves); ++i) {
    pointf pos = {.x = label->pos.x + moves[i].x,
                  .y = label->pos.y + moves[i].y};
    if (try_trunk_label_pos(g, owner, label, head_p, pos)) {
      if (GD_flip(g)) {
        pos.x += moves[i].x / 2.0;
        pos.y += moves[i].y / 2.0;
        pos.y -= 0.75;
      }
      label->pos = pos;
      updateBB(g, label);
      return;
    }
  }
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

static void set_no_arrows(edge_t *e) {
  attrsym_t *arrowhead = agfindedgeattr(agraphof(agtail(e)), "arrowhead");
  if (arrowhead) {
    agxset(e, arrowhead, "none");
  }
  attrsym_t *arrowtail = agfindedgeattr(agraphof(agtail(e)), "arrowtail");
  if (arrowtail) {
    agxset(e, arrowtail, "none");
  }
}

static node_t *fresh_junction_node(graph_t *g, size_t *index) {
  char name[64];
  do {
    snprintf(name, sizeof(name), "_concentrate_junction_%zu", (*index)++);
  } while (agnode(g, name, 0) != NULL);
  return agnode(g, name, 1);
}

static const char *junction_marker_color(const junction_group_t *group) {
  const char *color = group->attrs[GROUP_ATTR_COLOR];
  if (color == NULL || color[0] == '\0' || strchr(color, ':') ||
      strchr(color, ';')) {
    return "black";
  }
  return color;
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
                       junction_kind_t kind, bool reverse,
                       bool rank_band_slack) {
  edge_t *rep = group->edges[0];

  N_label = agattr_text(g, AGNODE, "label", "");
  N_color = agattr_text(g, AGNODE, "color", "");
  N_fillcolor = agattr_text(g, AGNODE, "fillcolor", "");
  N_shape = agattr_text(g, AGNODE, "shape", "ellipse");
  N_style = agattr_text(g, AGNODE, "style", "");
  N_width = agattr_text(g, AGNODE, "width", "");
  N_height = agattr_text(g, AGNODE, "height", "");
  agattr_text(g, AGNODE, "_concentrate_junction_node", "");
  agattr_text(g, AGEDGE, "color", "");
  agattr_text(g, AGEDGE, "style", "");
  agattr_text(g, AGEDGE, "penwidth", "");
  agattr_text(g, AGEDGE, "fontname", "");
  agattr_text(g, AGEDGE, "fontsize", "");
  agattr_text(g, AGEDGE, "fontcolor", "");
  agattr_text(g, AGEDGE, "label", "");
  agattr_text(g, AGEDGE, "headlabel", "");
  agattr_text(g, AGEDGE, "taillabel", "");
  agattr_text(g, AGEDGE, "labelfontsize", "");
  agattr_text(g, AGEDGE, "labelfontname", "");
  agattr_text(g, AGEDGE, "labelfontcolor", "");
  agattr_text(g, AGEDGE, "labeldistance", "");
  agattr_text(g, AGEDGE, "labelangle", "");
  agattr_text(g, AGEDGE, "dir", "");
  agattr_text(g, AGEDGE, "arrowhead", "");
  agattr_text(g, AGEDGE, "arrowtail", "");
  agattr_text(g, AGEDGE, "arrowsize", "");
  agattr_text(g, AGEDGE, "_concentrate_junction_original", "");
  agattr_text(g, AGEDGE, "_concentrate_junction_internal", "");
  agattr_text(g, AGEDGE, "_concentrate_junction_arm_splines", "");
  agattr_text(g, AGEDGE, "_concentrate_junction_draw_trunk", "");

  node_t *jn = fresh_junction_node(g, index);
  const char *marker_color = junction_marker_color(group);
  agxset(jn, N_label, "");
  agxset(jn, N_color, marker_color);
  agxset(jn, N_fillcolor, marker_color);
  agxset(jn, N_shape, "point");
  agxset(jn, N_style, "");
  agxset(jn, N_width, "0.02");
  agxset(jn, N_height, "0.02");
  agsafeset(jn, "label", "", "");
  agsafeset(jn, "color", marker_color, "");
  agsafeset(jn, "fillcolor", marker_color, "");
  agsafeset(jn, "shape", "point", "");
  agsafeset(jn, "style", "", "");
  agsafeset(jn, "width", "0.02", "");
  agsafeset(jn, "height", "0.02", "");
  agsafeset(jn, "_concentrate_junction_node", "true", "");
  init_added_node(jn);
  ND_concentrate_junction(jn) = true;

  int weight = 0;
  edge_t *trunk = NULL;
  if (kind == JUNCTION_FANOUT) {
    trunk = reverse ? agedge(g, jn, group->anchor, NULL, 1)
                    : agedge(g, group->anchor, jn, NULL, 1);
    copy_edge_attrs(trunk, rep, true, false, false, reverse);
    set_no_arrows(trunk);
  } else {
    trunk = reverse ? agedge(g, group->anchor, jn, NULL, 1)
                    : agedge(g, jn, group->anchor, NULL, 1);
    copy_edge_attrs(trunk, rep, true, true, false, reverse);
  }
  if (group->sameport && endpoint_label_text(rep, kind)[0]) {
    copy_endpoint_label_attrs(trunk, rep, kind);
  }
  agsafeset(trunk, "_concentrate_junction_internal", "true", "");
  init_added_edge(trunk);
  if (rank_band_slack) {
    ED_minlen(trunk) = 0;
  }
  ED_concentrate_junction_internal(trunk) = true;
  for (size_t i = 0; i < group->size; ++i) {
    edge_t *orig = group->edges[i];
    const int orig_weight = ED_weight(orig);
    if (group->sameport) {
      suppress_endpoint_label(orig, kind);
    }
    agsafeset(orig, "_concentrate_junction_original", "true", "");
    ED_minlen(orig) = 0;
    dot_bundle_load_set_legacy_position(orig, 0);
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
      set_no_arrows(arm);
    }
    agsafeset(arm, "_concentrate_junction_internal", "true", "");
    init_added_edge(arm);
    if (rank_band_slack) {
      ED_minlen(arm) = 0;
    }
    ED_concentrate_junction_internal(arm) = true;
    dot_bundle_load_set_legacy_position(arm, orig_weight);
    weight += orig_weight;

    junction_edge_t *info = gv_calloc(1, sizeof(junction_edge_t));
    info->arm = arm;
    info->trunk = trunk;
    info->draw_trunk = i == 0;
    info->reverse = reverse;
    info->fanout = kind == JUNCTION_FANOUT;
    ED_concentrate_junction(orig) = info;
  }

  const int64_t trunk_scale = ED_label(trunk) ? (int64_t)group->size : 1;
  const int64_t trunk_weight = (int64_t)weight * trunk_scale;
  dot_bundle_load_set_legacy_position(
      trunk, trunk_weight > INT_MAX ? INT_MAX : (int)trunk_weight);
}

static void make_groups(graph_t *g, junction_kind_t kind, size_t *made) {
  junction_group_t *groups = NULL;
  size_t ngroups = 0;
  size_t capacity = 0;

  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (refused_edge_kind(g, e)) {
        continue;
      }
      if (!eligible(e) || ED_concentrate_junction(e)) {
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
        snapshot_group_attrs(g, &groups[i], e);
        ngroups++;
      }
      append_edge(&groups[i], e);
    }
  }

  for (size_t i = 0; i < ngroups; ++i) {
    size_t distinct_outer = 0;
    for (size_t j = 0; j < groups[i].size; ++j) {
      node_t *outer = group_outer_endpoint(groups[i].edges[j], kind);
      bool seen = false;
      for (size_t k = 0; k < j; ++k) {
        if (group_outer_endpoint(groups[i].edges[k], kind) == outer) {
          seen = true;
          break;
        }
      }
      if (!seen) {
        distinct_outer++;
      }
    }
    if (distinct_outer >= 2) {
      make_group(g, &groups[i], made, kind, false,
                 group_needs_rank_band_slack(g, &groups[i], kind));
    }
    free(groups[i].edges);
  }
  free(groups);
}

static bool sameport_group_label_is_compatible(junction_group_t *group,
                                               junction_kind_t kind) {
  const char *label = "";
  size_t representative = 0;
  for (size_t i = 0; i < group->size; ++i) {
    const char *candidate = endpoint_label_text(group->edges[i], kind);
    if (candidate[0] == '\0') {
      continue;
    }
    if (label[0] != '\0' && !streq(label, candidate)) {
      return false;
    }
    label = candidate;
    representative = i;
  }
  if (representative != 0) {
    edge_t *tmp = group->edges[0];
    group->edges[0] = group->edges[representative];
    group->edges[representative] = tmp;
  }
  snapshot_group_attrs(agraphof(agtail(group->edges[0])), group,
                       group->edges[0]);
  return true;
}

static void make_sameport_groups(graph_t *g, junction_kind_t kind,
                                 size_t *made) {
  junction_group_t *groups = NULL;
  size_t ngroups = 0;
  size_t capacity = 0;
  attrsym_t *sameport =
      agfindedgeattr(g, kind == JUNCTION_FANOUT ? "sametail" : "samehead");
  if (sameport == NULL) {
    return;
  }

  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      const char *id = attr(e, sameport, "");
      if (id[0] == '\0' || agtail(e) == aghead(e) ||
          edge_has_record_endpoint_geometry(e) ||
          endpoint_has_explicit_port(e) || edge_has_true_multiedge_peer(e) ||
          graph_has_same_rank_edge(g, e) ||
          graph_has_refused_concentrated_edge(e) || !eligible_sameport(e) ||
          !samearrow_compatible(e, kind) || ED_concentrate_junction(e)) {
        continue;
      }
      if (group_anchor(e, kind) !=
          (kind == JUNCTION_FANOUT ? agtail(e) : aghead(e))) {
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
        groups[i].sameport_id = (char *)id;
        groups[i].sameport = true;
        snapshot_group_attrs(g, &groups[i], e);
        ngroups++;
      }
      append_edge(&groups[i], e);
    }
  }

  for (size_t i = 0; i < ngroups; ++i) {
    if (groups[i].size >= 2 &&
        sameport_group_label_is_compatible(&groups[i], kind)) {
      make_group(g, &groups[i], made, kind, false,
                 group_needs_rank_band_slack(g, &groups[i], kind));
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

void dot_concentrate_junction(graph_t *g) {
  if (!Concentrate) {
    return;
  }

  if (!agisdirected(g)) {
    return;
  }
  if (GD_n_cluster(g) != 0 || has_cluster_subgraph(g)) {
    agsafeset(g, "_concentrate_junction_clustered_fallback", "true", "");
    return;
  }

  size_t made = 0;
  make_sameport_groups(g, JUNCTION_FANIN, &made);
  make_sameport_groups(g, JUNCTION_FANOUT, &made);
  if (!GD_flip(g)) {
    make_groups(g, JUNCTION_FANIN, &made);
    make_groups(g, JUNCTION_FANOUT, &made);
  }
  if (made > 0) {
    agsafeset(g, "_concentrate_junction_active", "true", "");
    Concentrate = false;
  }
}

void dot_concentrate_junction_save_rankleader(graph_t *g, int r) {
  if (GD_rank(g)[r].n == 0 &&
      mapbool(agget(dot_root(g), "_concentrate_junction_clustered_fallback"))) {
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

static void straighten_cubic_handles(bezier *bz) {
  if (bz->size % 3 != 1) {
    return;
  }
  for (size_t i = 0; i + 3 < bz->size; i += 3) {
    const pointf start = bz->list[i];
    const pointf end = bz->list[i + 3];
    bz->list[i + 1] = (pointf){.x = start.x + (end.x - start.x) / 3.0,
                               .y = start.y + (end.y - start.y) / 3.0};
    bz->list[i + 2] = (pointf){.x = start.x + 2.0 * (end.x - start.x) / 3.0,
                               .y = start.y + 2.0 * (end.y - start.y) / 3.0};
  }
}

static void straighten_spline_handles(splines *spl) {
  for (size_t i = 0; i < spl->size; ++i) {
    straighten_cubic_handles(&spl->list[i]);
  }
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

  *arm_size = arm->size;

  splines *joined = gv_calloc(1, sizeof(splines));
  joined->size = arm->size + trunk->size;
  joined->list = gv_calloc(joined->size, sizeof(bezier));

  size_t out = 0;
  for (size_t i = 0; i < arm->size; ++i) {
    joined->list[out++] = arm_list[i];
  }
  for (size_t i = 0; i < trunk->size; ++i) {
    joined->list[out++] = trunk_list[i];
  }

  joined->bb = arm->bb;
  EXPANDBB(&joined->bb, trunk->bb);
  straighten_spline_handles(joined);
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
  splines *arm_copy = copy_splines(arm, reverse);
  (void)trunk;
  straighten_spline_handles(arm_copy);
  return arm_copy;
}

void dot_concentrate_junction_splines(graph_t *g) {
  if (!mapbool(agget(g, "_concentrate_junction_active"))) {
    return;
  }

  for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (!mapbool(agget(e, "_concentrate_junction_original"))) {
        continue;
      }
      junction_edge_t *info = ED_concentrate_junction(e);
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
      ED_concentrate_junction_draw_trunk(e) = info->draw_trunk;
      ED_concentrate_junction_emit_splines(e) = arm_size;
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
      agsafeset(e, "_concentrate_junction_arm_splines", nbuf, "");
      agsafeset(e, "_concentrate_junction_draw_trunk",
                info->draw_trunk ? "true" : "false", "");

      if (ED_label(e) && ED_label(info->trunk) && ED_label(info->trunk)->set) {
        ED_label(e)->pos = ED_label(info->trunk)->pos;
        ED_label(e)->set = true;
      }
      if (info->draw_trunk) {
        const junction_kind_t kind =
            info->fanout ? JUNCTION_FANOUT : JUNCTION_FANIN;
        transfer_endpoint_label(e, info->trunk, kind);
        clear_transferred_endpoint_label(g, e, kind);
      }

      ED_edge_type(info->arm) = IGNORED;
      ED_edge_type(info->trunk) = IGNORED;
      if (E_label) {
        agxset(info->trunk, E_label, "");
      }
      free(info);
      ED_concentrate_junction(e) = NULL;
    }
  }
}
