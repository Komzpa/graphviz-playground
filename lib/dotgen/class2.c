/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

/* classify edges for mincross/nodepos/splines, using given ranks */

#include "config.h"

#include <dotgen/dot.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>
#include <util/gv_math.h>

bool edge_attributes_are_equal(edge_t *first_edge, edge_t *second_edge) {
  graph_t *const root_graph = agroot(agraphof(first_edge));

  /*
   * Attribute symbols belong to the root graph, and their defaults apply even
   * when an edge does not mention the attribute in the DOT source. Walk the
   * root registry so this comparison covers every effective edge attribute.
   *
   * Cgraph also records whether a string is HTML-like separately from its
   * bytes. Plain text "<B>x</B>" and HTML <<B>x</B>> therefore have different
   * rendering semantics even though strcmp() sees the same characters.
   */
  Agsym_t *attribute = agnxtattr(root_graph, AGEDGE, NULL);
  while (attribute != NULL) {
    const char *const first_value = agxget(first_edge, attribute);
    const char *const second_value = agxget(second_edge, attribute);

    const bool same_representation =
        aghtmlstr(first_value) == aghtmlstr(second_value);
    const bool same_text = strcmp(first_value, second_value) == 0;
    if (!same_representation || !same_text) {
      return false;
    }

    attribute = agnxtattr(root_graph, AGEDGE, attribute);
  }
  return true;
}

static node_t *label_vnode(graph_t *g, edge_t *orig) {
  const pointf dimen = ED_label(orig)->dimen;
  node_t *const v = virtual_node(g);
  ND_label(v) = ED_label(orig);
  ND_lw(v) = GD_nodesep(agroot(v));
  if (!ED_label_ontop(orig)) {
    if (GD_flip(agroot(g))) {
      ND_ht(v) = dimen.x;
      ND_rw(v) = dimen.y;
    } else {
      ND_ht(v) = dimen.y;
      ND_rw(v) = dimen.x;
    }
  }
  return v;
}

static void incr_width(graph_t *g, node_t *v) {
  int width = GD_nodesep(g) / 2;
  ND_lw(v) += width;
  ND_rw(v) += width;
}

static node_t *plain_vnode(graph_t *g) {
  node_t *const v = virtual_node(g);
  incr_width(g, v);
  return v;
}

static node_t *leader_of(node_t *v) {
  graph_t *clust;
  node_t *rv;

  if (ND_ranktype(v) != CLUSTER) {
    rv = UF_find(v);
  } else {
    clust = ND_clust(v);
    rv = GD_rankleader(clust)[ND_rank(v)];
  }
  return rv;
}

/// create chain of dummy nodes for edge orig
static void make_chain(graph_t *g, node_t *from, node_t *to, edge_t *orig) {
  int r, label_rank;
  node_t *u, *v;
  edge_t *e;

  u = from;
  if (ED_label(orig))
    label_rank = (ND_rank(from) + ND_rank(to)) / 2;
  else
    label_rank = -1;
  assert(ED_to_virt(orig) == NULL);
  for (r = ND_rank(from) + 1; r <= ND_rank(to); r++) {
    if (r < ND_rank(to)) {
      if (r == label_rank)
        v = label_vnode(g, orig);
      else
        v = plain_vnode(g);
      ND_rank(v) = r;
    } else
      v = to;
    e = virtual_edge(u, v, orig);
    virtual_weight(e);
    u = v;
  }
  assert(ED_to_virt(orig) != NULL);
}

static void interclrep(graph_t *g, edge_t *e) {
  node_t *t, *h;
  edge_t *ve;

  t = leader_of(agtail(e));
  h = leader_of(aghead(e));
  if (ND_rank(t) > ND_rank(h)) {
    SWAP(&t, &h);
  }
  if (ND_clust(t) != ND_clust(h)) {
    if ((ve = find_fast_edge(t, h))) {
      merge_chain(g, e, ve, true);
      return;
    }
    if (ND_rank(t) == ND_rank(h))
      return;
    make_chain(g, t, h, e);

    /* mark as cluster edge */
    for (ve = ED_to_virt(e); ve && ND_rank(aghead(ve)) <= ND_rank(h);
         ve = ND_out(aghead(ve)).list[0])
      ED_edge_type(ve) = CLUSTER_EDGE;
  }
  /* else ignore intra-cluster edges at this point */
}

static bool is_cluster_edge(edge_t *e) {
  return ND_ranktype(agtail(e)) == CLUSTER || ND_ranktype(aghead(e)) == CLUSTER;
}

void merge_chain(graph_t *g, edge_t *e, edge_t *f, bool update_count) {
  edge_t *rep;
  int lastrank = MAX(ND_rank(agtail(e)), ND_rank(aghead(e)));

  assert(ED_to_virt(e) == NULL);
  ED_to_virt(e) = f;
  rep = f;
  do {
    /* interclust multi-edges are not counted now */
    if (update_count)
      ED_count(rep) += ED_count(e);
    ED_xpenalty(rep) += ED_xpenalty(e);
    ED_weight(rep) += ED_weight(e);
    if (ND_rank(aghead(rep)) == lastrank)
      break;
    incr_width(g, aghead(rep));
    rep = ND_out(aghead(rep)).list[0];
  } while (rep);
}

bool mergeable(edge_t *e, edge_t *f) {
  return e && f && agtail(e) == agtail(f) && aghead(e) == aghead(f) &&
         ED_label(e) == ED_label(f) && ports_eq(e, f);
}

static bool has_prior_concentrated_equivalent(graph_t *graph, edge_t *edge) {
  /*
   * class2() visits a node's outgoing edges in Cgraph order. Only an earlier
   * edge can already own the virtual chain that this edge would duplicate, so
   * stop at the current edge and never let later input affect the decision.
   */
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_owns_chain = ED_to_virt(prior_edge) != NULL;
    const bool both_edges_are_unlabeled =
        ED_label(prior_edge) == NULL && ED_label(edge) == NULL;

    if (same_endpoints && prior_edge_owns_chain && both_edges_are_unlabeled &&
        ports_eq(prior_edge, edge) &&
        edge_attributes_are_equal(prior_edge, edge)) {
      return true;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return false;
}

/*
 * Suppression is a state transition, not merely an equivalence query. Keep the
 * decision and the IGNORED assignment together so callers cannot accidentally
 * recognize a duplicate without removing its redundant virtual chain.
 */
static bool ignore_concentrated_parallel_edge(graph_t *graph, edge_t *edge) {
  if (!Concentrate || !has_prior_concentrated_equivalent(graph, edge)) {
    return false;
  }

  ED_edge_type(edge) = IGNORED;
  return true;
}

/*
 * A backward edge can share the chain of a previously classified edge running
 * in the other direction. When concentration is enabled, the opposite edge
 * must also be semantically equal because the backward edge will disappear.
 * Without concentration, both edges remain visible and share only their route.
 */
static bool merge_backward_edge_with_opposite(graph_t *graph,
                                              edge_t *backward_edge) {
  edge_t *opposite_edge = agfstout(graph, aghead(backward_edge));

  while (opposite_edge != NULL) {
    const bool connects_same_nodes =
        aghead(opposite_edge) == agtail(backward_edge);
    const bool is_self_edge = aghead(opposite_edge) == aghead(backward_edge);
    const bool is_available = ED_edge_type(opposite_edge) != IGNORED;

    if (connects_same_nodes && !is_self_edge && is_available) {
      /* The retained opposite edge must own a chain we can share. */
      if (ED_to_virt(opposite_edge) == NULL) {
        make_chain(graph, agtail(opposite_edge), aghead(opposite_edge),
                   opposite_edge);
      }

      const bool compatible_endpoints = ED_label(backward_edge) == NULL &&
                                        ED_label(opposite_edge) == NULL &&
                                        ports_eq(backward_edge, opposite_edge);
      const bool compatible_attributes =
          !Concentrate ||
          edge_attributes_are_equal(backward_edge, opposite_edge);

      if (compatible_endpoints && compatible_attributes) {
        if (Concentrate) {
          /*
           * Preserve the suppressed edge's identity so arrow rendering can
           * recover the endpoint arrows for this exact pair.
           */
          ED_edge_type(backward_edge) = IGNORED;
          ED_conc_opp_flag(opposite_edge) = true;
          remember_suppressed_opposite_edge(opposite_edge, backward_edge);
        } else {
          other_edge(backward_edge);
          merge_chain(graph, backward_edge, ED_to_virt(opposite_edge), true);
        }
        return true;
      }
    }

    opposite_edge = agnxtout(graph, opposite_edge);
  }
  return false;
}

void class2(graph_t *g) {
  int c;
  node_t *n, *t, *h;
  edge_t *e, *prev;

  GD_nlist(g) = NULL;

  mark_clusters(g);
  for (c = 1; c <= GD_n_cluster(g); c++)
    build_skeleton(g, GD_clust(g)[c]);
  for (n = agfstnode(g); n; n = agnxtnode(g, n))
    for (e = agfstout(g, n); e; e = agnxtout(g, e)) {
      if (ND_weight_class(aghead(e)) <= 2)
        ND_weight_class(aghead(e))++;
      if (ND_weight_class(agtail(e)) <= 2)
        ND_weight_class(agtail(e))++;
    }

  for (n = agfstnode(g); n; n = agnxtnode(g, n)) {
    if (ND_clust(n) == NULL && n == UF_find(n)) {
      fast_node(g, n);
    }
    prev = NULL;
    for (e = agfstout(g, n); e; e = agnxtout(g, e)) {

      /* already processed */
      if (ED_to_virt(e)) {
        prev = e;
        continue;
      }

      /* edges involving sub-clusters of g */
      if (is_cluster_edge(e)) {
        /* following is new cluster multi-edge code */
        if (mergeable(prev, e)) {
          if (ED_to_virt(prev)) {
            merge_chain(g, e, ED_to_virt(prev), false);
            other_edge(e);
          } else if (ND_rank(agtail(e)) == ND_rank(aghead(e))) {
            merge_oneway(e, prev);
            other_edge(e);
          }
          /* else is an intra-cluster edge */
          continue;
        }
        interclrep(g, e);
        prev = e;
        continue;
      }
      /* merge multi-edges */
      if (prev && agtail(e) == agtail(prev) && aghead(e) == aghead(prev)) {
        if (ND_rank(agtail(e)) == ND_rank(aghead(e))) {
          merge_oneway(e, prev);
          other_edge(e);
          continue;
        }
        if (ED_label(e) == NULL && ED_label(prev) == NULL &&
            ports_eq(e, prev)) {
          if (Concentrate) {
            if (edge_attributes_are_equal(e, prev)) {
              ED_edge_type(e) = IGNORED;
              continue;
            }
          } else {
            merge_chain(g, e, ED_to_virt(prev), true);
            other_edge(e);
            continue;
          }
        }
        /* parallel edges with different labels fall through here */
      }

      /* self edges */
      if (agtail(e) == aghead(e)) {
        other_edge(e);
        prev = e;
        continue;
      }

      t = UF_find(agtail(e));
      h = UF_find(aghead(e));

      /* non-leader leaf nodes */
      if (agtail(e) != t || aghead(e) != h) {
        /* FIX need to merge stuff */
        continue;
      }

      /* flat edges */
      if (ND_rank(agtail(e)) == ND_rank(aghead(e))) {
        flat_edge(g, e);
        prev = e;
        continue;
      }

      /* forward edges */
      if (ND_rank(aghead(e)) > ND_rank(agtail(e))) {
        if (ignore_concentrated_parallel_edge(g, e))
          continue;
        make_chain(g, agtail(e), aghead(e), e);
        prev = e;
        continue;
      }

      /* backward edges */
      if (merge_backward_edge_with_opposite(g, e)) {
        continue;
      }
      if (ignore_concentrated_parallel_edge(g, e))
        continue;
      make_chain(g, aghead(e), agtail(e), e);
      prev = e;
    }
  }
  /* since decompose() is not called on subgraphs */
  if (g != dot_root(g)) {
    free(GD_comp(g).list);
    GD_comp(g).list = gv_alloc(sizeof(node_t *));
    GD_comp(g).list[0] = GD_nlist(g);
  }
}
