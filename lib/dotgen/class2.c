/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

/*
 * dot classifies edges in two different representations. class1() builds the
 * temporary constraint edges used to assign ranks. After those ranks are
 * fixed, class2() materializes the virtual nodes and edge chains consumed by
 * crossing minimization, node positioning, and spline routing.
 *
 * The numbered name is historical: both passes already had these names in the
 * oldest imported Graphviz sources. Keep the name at the external boundary,
 * but describe the post-rank representation explicitly inside this file.
 */

#include "config.h"

#include <common/edgeattr.h>
#include <common/utils.h>
#include <dotgen/dot.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>
#include <util/gv_math.h>

bool edge_attributes_are_equal(edge_t *first_edge, edge_t *second_edge) {
  return gv_edge_attributes_are_equal(first_edge, second_edge);
}

bool opposite_edge_attributes_are_equal(edge_t *first_edge,
                                        edge_t *second_edge) {
  return gv_opposite_edge_attributes_are_equal(first_edge, second_edge);
}

static node_t *make_label_virtual_node(graph_t *graph, edge_t *original_edge) {
  const pointf label_dimensions = ED_label(original_edge)->dimen;
  node_t *const label_node = virtual_node(graph);
  ND_label(label_node) = ED_label(original_edge);
  ND_lw(label_node) = GD_nodesep(agroot(label_node));
  if (!ED_label_ontop(original_edge)) {
    if (GD_flip(agroot(graph))) {
      ND_ht(label_node) = label_dimensions.x;
      ND_rw(label_node) = label_dimensions.y;
    } else {
      ND_ht(label_node) = label_dimensions.y;
      ND_rw(label_node) = label_dimensions.x;
    }
  }
  return label_node;
}

static void widen_virtual_node(graph_t *graph, node_t *virtual_node) {
  const int half_node_separation = GD_nodesep(graph) / 2;
  ND_lw(virtual_node) += half_node_separation;
  ND_rw(virtual_node) += half_node_separation;
}

static node_t *make_plain_virtual_node(graph_t *graph) {
  node_t *const plain_node = virtual_node(graph);
  widen_virtual_node(graph, plain_node);
  return plain_node;
}

static node_t *rank_leader(node_t *node) {
  if (ND_ranktype(node) != CLUSTER) {
    return UF_find(node);
  }

  graph_t *const cluster = ND_clust(node);
  return GD_rankleader(cluster)[ND_rank(node)];
}

/// Create a rank-by-rank chain of virtual edges for an original edge.
static void make_virtual_edge_chain(graph_t *graph, node_t *first_node,
                                    node_t *last_node, edge_t *original_edge) {
  const int label_rank = ED_label(original_edge)
                             ? (ND_rank(first_node) + ND_rank(last_node)) / 2
                             : -1;
  node_t *chain_tail = first_node;

  assert(ED_to_virt(original_edge) == NULL);
  for (int rank = ND_rank(first_node) + 1; rank <= ND_rank(last_node); rank++) {
    node_t *chain_head;
    if (rank < ND_rank(last_node)) {
      chain_head = rank == label_rank
                       ? make_label_virtual_node(graph, original_edge)
                       : make_plain_virtual_node(graph);
      ND_rank(chain_head) = rank;
    } else {
      chain_head = last_node;
    }

    edge_t *const virtual_segment =
        virtual_edge(chain_tail, chain_head, original_edge);
    virtual_weight(virtual_segment);
    chain_tail = chain_head;
  }
  assert(ED_to_virt(original_edge) != NULL);
}

static void represent_intercluster_edge(graph_t *graph, edge_t *original_edge) {
  node_t *tail_leader = rank_leader(agtail(original_edge));
  node_t *head_leader = rank_leader(aghead(original_edge));
  if (ND_rank(tail_leader) > ND_rank(head_leader)) {
    SWAP(&tail_leader, &head_leader);
  }
  if (ND_clust(tail_leader) != ND_clust(head_leader)) {
    edge_t *virtual_edge = find_fast_edge(tail_leader, head_leader);
    if (virtual_edge != NULL) {
      merge_chain(graph, original_edge, virtual_edge, true);
      return;
    }
    if (ND_rank(tail_leader) == ND_rank(head_leader)) {
      return;
    }
    make_virtual_edge_chain(graph, tail_leader, head_leader, original_edge);

    /* The chain remains distinguishable while cluster expansion rewrites it. */
    virtual_edge = ED_to_virt(original_edge);
    while (virtual_edge != NULL &&
           ND_rank(aghead(virtual_edge)) <= ND_rank(head_leader)) {
      ED_edge_type(virtual_edge) = CLUSTER_EDGE;
      virtual_edge = ND_out(aghead(virtual_edge)).list[0];
    }
  }
  /* else ignore intra-cluster edges at this point */
}

static bool is_cluster_edge(edge_t *edge) {
  return ND_ranktype(agtail(edge)) == CLUSTER ||
         ND_ranktype(aghead(edge)) == CLUSTER;
}

void merge_chain(graph_t *graph, edge_t *original_edge,
                 edge_t *first_virtual_edge, bool update_count) {
  const int last_rank =
      MAX(ND_rank(agtail(original_edge)), ND_rank(aghead(original_edge)));

  assert(ED_to_virt(original_edge) == NULL);
  ED_to_virt(original_edge) = first_virtual_edge;
  edge_t *representative_edge = first_virtual_edge;
  do {
    /* interclust multi-edges are not counted now */
    if (update_count) {
      ED_count(representative_edge) += ED_count(original_edge);
    }
    ED_xpenalty(representative_edge) += ED_xpenalty(original_edge);
    ED_weight(representative_edge) += ED_weight(original_edge);
    if (ND_rank(aghead(representative_edge)) == last_rank) {
      break;
    }
    widen_virtual_node(graph, aghead(representative_edge));
    representative_edge = ND_out(aghead(representative_edge)).list[0];
  } while (representative_edge != NULL);
}

bool mergeable(edge_t *first_edge, edge_t *second_edge) {
  return first_edge != NULL && second_edge != NULL &&
         agtail(first_edge) == agtail(second_edge) &&
         aghead(first_edge) == aghead(second_edge) &&
         ED_label(first_edge) == ED_label(second_edge) &&
         ports_eq(first_edge, second_edge);
}

static edge_t *find_prior_concentrated_representative(graph_t *graph,
                                                      edge_t *edge) {
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
        edge_attributes_are_equal(prior_edge, edge) &&
        same_direction_edge_arrow_decorations_are_equal(prior_edge, edge)) {
      return prior_edge;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return NULL;
}

static edge_t *find_prior_parallel_route(graph_t *graph, edge_t *edge) {
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_owns_chain = ED_to_virt(prior_edge) != NULL;
    const bool both_edges_are_unlabeled =
        ED_label(prior_edge) == NULL && ED_label(edge) == NULL;

    if (same_endpoints && prior_edge_owns_chain && both_edges_are_unlabeled &&
        ports_eq(prior_edge, edge)) {
      return prior_edge;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return NULL;
}

static edge_t *find_prior_flat_concentrated_equivalent(graph_t *graph,
                                                       edge_t *edge) {
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_is_flat =
        ND_rank(agtail(prior_edge)) == ND_rank(aghead(prior_edge));
    const bool both_edges_are_unlabeled =
        ED_label(prior_edge) == NULL && ED_label(edge) == NULL;

    if (same_endpoints && prior_edge_is_flat &&
        ED_edge_type(prior_edge) == NORMAL && ED_edge_type(edge) == NORMAL &&
        both_edges_are_unlabeled && ports_eq(prior_edge, edge) &&
        edge_attributes_are_equal(prior_edge, edge) &&
        same_direction_edge_arrow_decorations_are_equal(prior_edge, edge)) {
      return prior_edge;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return NULL;
}

/*
 * Suppression is a state transition, not merely an equivalence query. Keep the
 * decision and the IGNORED assignment together so callers cannot accidentally
 * recognize a duplicate without removing its redundant virtual chain.
 */
static bool route_concentrated_parallel_edge(graph_t *graph, edge_t *edge) {
  if (!Concentrate) {
    return false;
  }

  edge_t *const concentrated_representative =
      find_prior_concentrated_representative(graph, edge);
  if (concentrated_representative != NULL) {
    fold_concentrated_edge_arrow_decorations(concentrated_representative, edge,
                                             false);
    ED_edge_type(edge) = IGNORED;
    return true;
  }

  edge_t *const representative_edge = find_prior_parallel_route(graph, edge);
  if (representative_edge == NULL) {
    return false;
  }

  /*
   * A distinct edge still needs the ordinary multi-edge route. Giving it a
   * separate main virtual chain makes the spline router treat it as a separate
   * concentrated path and the rendered edges collapse onto the same centerline.
   */
  merge_chain(graph, edge, ED_to_virt(representative_edge), true);
  other_edge(edge);
  return true;
}

bool opposite_edge_ports_are_equal(edge_t *edge, edge_t *opposite_edge) {
  return gv_opposite_edge_ports_are_equal(edge, opposite_edge);
}

/*
 * A backward edge can share the chain of a previously classified edge running
 * in the other direction. When concentration is enabled, the opposite edge
 * must also be semantically equal because the backward edge will disappear.
 * Without concentration, both edges remain visible and share only their route.
 */
static bool merge_backward_edge_with_opposite(graph_t *graph,
                                              edge_t *backward_edge) {
  if (Concentrate) {
    edge_t *const concentrated_representative =
        find_prior_concentrated_representative(graph, backward_edge);
    if (concentrated_representative != NULL) {
      fold_concentrated_edge_arrow_decorations(
          concentrated_representative, backward_edge, false);
      ED_edge_type(backward_edge) = IGNORED;
      return true;
    }
  }

  edge_t *opposite_edge = agfstout(graph, aghead(backward_edge));
  edge_t *route_candidate = NULL;

  while (opposite_edge != NULL) {
    const bool connects_same_nodes =
        aghead(opposite_edge) == agtail(backward_edge);
    const bool is_self_edge = aghead(opposite_edge) == aghead(backward_edge);
    const bool is_available = ED_edge_type(opposite_edge) != IGNORED;

    if (connects_same_nodes && !is_self_edge && is_available) {
      const bool compatible_endpoints =
          ED_label(backward_edge) == NULL && ED_label(opposite_edge) == NULL &&
          opposite_edge_ports_are_equal(backward_edge, opposite_edge);
      if (compatible_endpoints) {
        if (Concentrate &&
            opposite_edge_attributes_are_equal(backward_edge, opposite_edge) &&
            opposite_direction_edge_arrow_decorations_are_mergeable(
                opposite_edge, backward_edge)) {
          /*
           * Materialize only the selected representative ahead of its normal
           * turn. Creating chains for every candidate while scanning would
           * pre-classify later parallel edges before duplicate suppression can
           * compare them.
           */
          if (ED_to_virt(opposite_edge) == NULL) {
            make_virtual_edge_chain(graph, agtail(opposite_edge),
                                    aghead(opposite_edge), opposite_edge);
          }
          fold_concentrated_edge_arrow_decorations(opposite_edge, backward_edge,
                                                   true);
          ED_edge_type(backward_edge) = IGNORED;
          ED_conc_opp_flag(opposite_edge) = true;
          return true;
        }
        if (!Concentrate) {
          if (ED_to_virt(opposite_edge) == NULL) {
            make_virtual_edge_chain(graph, agtail(opposite_edge),
                                    aghead(opposite_edge), opposite_edge);
          }
          other_edge(backward_edge);
          merge_chain(graph, backward_edge, ED_to_virt(opposite_edge), true);
          return true;
        }
        if (route_candidate == NULL) {
          route_candidate = opposite_edge;
        } else {
          /*
           * Keep scanning in concentrate mode. A later opposite edge may be
           * the semantic mate that should be suppressed rather than drawn.
           */
        }
      }
    }

    opposite_edge = agnxtout(graph, opposite_edge);
  }
  if (route_candidate != NULL) {
    if (ED_to_virt(route_candidate) == NULL) {
      make_virtual_edge_chain(graph, agtail(route_candidate),
                              aghead(route_candidate), route_candidate);
    }
    other_edge(backward_edge);
    merge_chain(graph, backward_edge, ED_to_virt(route_candidate), true);
    return true;
  }
  return false;
}

static bool suppress_concentrated_cluster_edge_with_opposite(edge_t *edge) {
  if (!Concentrate || ED_label(edge) != NULL) {
    return false;
  }

  edge_t *opposite_edge = agfstout(agraphof(edge), aghead(edge));
  while (opposite_edge != NULL) {
    const bool connects_same_nodes = aghead(opposite_edge) == agtail(edge);
    const bool is_available = ED_edge_type(opposite_edge) != IGNORED;
    const bool owns_route = ED_to_virt(opposite_edge) != NULL;
    const bool both_edges_are_unlabeled = ED_label(opposite_edge) == NULL;

    if (connects_same_nodes && is_available && owns_route &&
        both_edges_are_unlabeled && opposite_edge_ports_are_equal(edge, opposite_edge) &&
        opposite_edge_attributes_are_equal(edge, opposite_edge) &&
        opposite_direction_edge_arrow_decorations_are_mergeable(opposite_edge,
                                                                 edge)) {
      fold_concentrated_edge_arrow_decorations(opposite_edge, edge, true);
      ED_edge_type(edge) = IGNORED;
      ED_conc_opp_flag(opposite_edge) = true;
      return true;
    }

    opposite_edge = agnxtout(agraphof(edge), opposite_edge);
  }
  return false;
}

void class2(graph_t *graph) {
  GD_nlist(graph) = NULL;

  /* Cluster skeletons stand in for collapsed cluster contents in this pass. */
  mark_clusters(graph);
  for (int cluster_index = 1; cluster_index <= GD_n_cluster(graph);
       cluster_index++) {
    build_skeleton(graph, GD_clust(graph)[cluster_index]);
  }

  /*
   * The weight class is a saturated incident-edge count. Exact high degrees
   * are not useful to the later low-degree heuristic, so stop incrementing
   * after the value has reached its three-or-more bucket.
   */
  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    for (edge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {
      if (ND_weight_class(aghead(edge)) <= 2) {
        ND_weight_class(aghead(edge))++;
      }
      if (ND_weight_class(agtail(edge)) <= 2) {
        ND_weight_class(agtail(edge))++;
      }
    }
  }

  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    if (ND_clust(node) == NULL && node == UF_find(node)) {
      fast_node(graph, node);
    }
    edge_t *previous_edge = NULL;
    for (edge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {

      /* A prior cluster operation has already attached this representation. */
      if (ED_to_virt(edge) != NULL) {
        previous_edge = edge;
        continue;
      }

      /* Edges incident to a collapsed sub-cluster use its rank skeleton. */
      if (is_cluster_edge(edge)) {
        if (suppress_concentrated_cluster_edge_with_opposite(edge)) {
          continue;
        }
        if (mergeable(previous_edge, edge)) {
          if (ED_to_virt(previous_edge) != NULL) {
            merge_chain(graph, edge, ED_to_virt(previous_edge), false);
            other_edge(edge);
          } else if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
            merge_oneway(edge, previous_edge);
            other_edge(edge);
          }
          /* An intra-cluster edge needs no representation at this level. */
          continue;
        }
        represent_intercluster_edge(graph, edge);
        previous_edge = edge;
        continue;
      }

      /* Parallel input edges may share one virtual routing representation. */
      if (previous_edge != NULL && agtail(edge) == agtail(previous_edge) &&
          aghead(edge) == aghead(previous_edge)) {
        if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
          edge_t *representative_edge = previous_edge;
          if (Concentrate) {
            edge_t *const equivalent_edge =
                find_prior_flat_concentrated_equivalent(graph, edge);
            if (equivalent_edge != NULL) {
              representative_edge = equivalent_edge;
            }
          }
          merge_oneway(edge, representative_edge);
          other_edge(edge);
          continue;
        }
        if (ED_label(edge) == NULL && ED_label(previous_edge) == NULL &&
            ports_eq(edge, previous_edge)) {
          if (Concentrate) {
            if (route_concentrated_parallel_edge(graph, edge)) {
              continue;
            }
          } else {
            merge_chain(graph, edge, ED_to_virt(previous_edge), true);
            other_edge(edge);
            continue;
          }
        }
        /* A semantically distinct parallel edge gets its own chain below. */
      }

      /* Self edges bypass rank-spanning chains and are routed from ND_other. */
      if (agtail(edge) == aghead(edge)) {
        other_edge(edge);
        previous_edge = edge;
        continue;
      }

      node_t *const tail_leader = UF_find(agtail(edge));
      node_t *const head_leader = UF_find(aghead(edge));

      /* Leaf-set members are represented through their union-find leaders. */
      if (agtail(edge) != tail_leader || aghead(edge) != head_leader) {
        /* FIX need to merge stuff */
        continue;
      }

      /* A flat edge stays within one rank and uses the flat-edge lists. */
      if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
        flat_edge(graph, edge);
        previous_edge = edge;
        continue;
      }

      if (ND_rank(aghead(edge)) > ND_rank(agtail(edge))) {
        if (route_concentrated_parallel_edge(graph, edge)) {
          continue;
        }
        make_virtual_edge_chain(graph, agtail(edge), aghead(edge), edge);
        previous_edge = edge;
        continue;
      }

      /* Store every rank-spanning chain from lower rank to higher rank. */
      if (merge_backward_edge_with_opposite(graph, edge)) {
        continue;
      }
      if (route_concentrated_parallel_edge(graph, edge)) {
        continue;
      }
      make_virtual_edge_chain(graph, aghead(edge), agtail(edge), edge);
      previous_edge = edge;
    }
  }

  /* decompose() is not called on subgraphs, so publish their sole component. */
  if (graph != dot_root(graph)) {
    free(GD_comp(graph).list);
    GD_comp(graph).list = gv_alloc(sizeof(node_t *));
    GD_comp(graph).list[0] = GD_nlist(graph);
  }
}
