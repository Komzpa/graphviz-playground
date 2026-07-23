/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

/* Build the post-rank edge chains used by later layout phases. */

#include "config.h"

#include <common/edgeattr.h>
#include <common/utils.h>
#include <dotgen/concentrate_plan.h>
#include <dotgen/dot.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>
#include <util/gv_math.h>

static node_t *
transactional_virtual_node(graph_t *graph,
                           gv_concentration_transaction_t *handle) {
  if (handle != NULL) {
    gv_concentration_transaction_record(handle, &GD_nlist(graph),
                                        sizeof(GD_nlist(graph)));
    if (GD_nlist(graph) != NULL) {
      gv_concentration_transaction_record(handle, &ND_prev(GD_nlist(graph)),
                                          sizeof(ND_prev(GD_nlist(graph))));
    }
  }
  node_t *const node = virtual_node(graph);
  if (handle != NULL) {
    gv_concentration_transaction_track_virtual_node(handle, node);
  }
  return node;
}

static edge_t *
transactional_virtual_edge(node_t *tail, node_t *head, edge_t *original_edge,
                           gv_concentration_transaction_t *handle) {
  if (handle != NULL) {
    gv_concentration_transaction_record_elist(handle, &ND_out(tail));
    gv_concentration_transaction_record_elist(handle, &ND_in(head));
    if (original_edge != NULL) {
      gv_concentration_transaction_record(handle, &ED_to_virt(original_edge),
                                          sizeof(ED_to_virt(original_edge)));
    }
  }
  edge_t *const edge = virtual_edge(tail, head, original_edge);
  if (handle != NULL) {
    gv_concentration_transaction_track_virtual_edge(handle, edge);
  }
  return edge;
}

static node_t *make_label_virtual_node(graph_t *graph, edge_t *original_edge,
                                       gv_concentration_transaction_t *handle) {
  const pointf label_dimensions = ED_label(original_edge)->dimen;
  node_t *const label_node = transactional_virtual_node(graph, handle);
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

static node_t *rank_leader(node_t *node) {
  if (ND_ranktype(node) != CLUSTER) {
    return UF_find(node);
  }

  graph_t *const cluster = ND_clust(node);
  return GD_rankleader(cluster)[ND_rank(node)];
}

/// Create a rank-by-rank chain of virtual edges for an original edge.
static void
make_virtual_edge_chain_impl(graph_t *graph, node_t *first_node,
                             node_t *last_node, edge_t *original_edge,
                             gv_concentration_transaction_t *handle) {
  const int label_rank = ED_label(original_edge)
                             ? (ND_rank(first_node) + ND_rank(last_node)) / 2
                             : -1;
  node_t *chain_tail = first_node;

  assert(ED_to_virt(original_edge) == NULL);
  for (int rank = ND_rank(first_node) + 1; rank <= ND_rank(last_node); rank++) {
    node_t *chain_head;
    if (rank < ND_rank(last_node)) {
      if (rank == label_rank) {
        chain_head = make_label_virtual_node(graph, original_edge, handle);
      } else {
        chain_head = transactional_virtual_node(graph, handle);
        widen_virtual_node(graph, chain_head);
      }
      ND_rank(chain_head) = rank;
    } else {
      chain_head = last_node;
    }

    edge_t *const virtual_segment = transactional_virtual_edge(
        chain_tail, chain_head, original_edge, handle);
    virtual_weight(virtual_segment);
    chain_tail = chain_head;
  }
  assert(ED_to_virt(original_edge) != NULL);
}

static void make_virtual_edge_chain(graph_t *graph, node_t *first_node,
                                    node_t *last_node, edge_t *original_edge) {
  make_virtual_edge_chain_impl(graph, first_node, last_node, original_edge,
                               NULL);
}

static void transactional_merge_chain(gv_concentration_transaction_t *handle,
                                      graph_t *graph, edge_t *original_edge,
                                      edge_t *first_virtual_edge,
                                      dot_bundle_merge_t merge);

static void
represent_intercluster_edge(graph_t *graph, edge_t *original_edge,
                            gv_concentration_transaction_t *handle) {
  node_t *tail_leader = rank_leader(agtail(original_edge));
  node_t *head_leader = rank_leader(aghead(original_edge));
  if (ND_rank(tail_leader) > ND_rank(head_leader)) {
    SWAP(&tail_leader, &head_leader);
  }
  if (ND_clust(tail_leader) != ND_clust(head_leader)) {
    edge_t *virtual_edge = find_fast_edge(tail_leader, head_leader);
    if (virtual_edge != NULL) {
      if (handle == NULL) {
        merge_chain(graph, original_edge, virtual_edge, DOT_BUNDLE_ACCUMULATE);
      } else {
        transactional_merge_chain(handle, graph, original_edge, virtual_edge,
                                  DOT_BUNDLE_ACCUMULATE);
      }
      return;
    }
    if (ND_rank(tail_leader) == ND_rank(head_leader)) {
      return;
    }
    make_virtual_edge_chain_impl(graph, tail_leader, head_leader, original_edge,
                                 handle);

    /* The chain remains distinguishable while cluster expansion rewrites it. */
    virtual_edge = ED_to_virt(original_edge);
    while (virtual_edge != NULL &&
           ND_rank(aghead(virtual_edge)) <= ND_rank(head_leader)) {
      if (handle != NULL) {
        gv_concentration_transaction_record(handle, &ED_edge_type(virtual_edge),
                                            sizeof(ED_edge_type(virtual_edge)));
      }
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

static void merge_bundle_load_chain(graph_t *graph, edge_t *original_edge,
                                    edge_t *first_virtual_edge,
                                    dot_bundle_merge_t merge) {
  assert(first_virtual_edge != NULL);
  if (first_virtual_edge == NULL) {
    return;
  }

  const int last_rank =
      MAX(ND_rank(agtail(original_edge)), ND_rank(aghead(original_edge)));

  edge_t *representative_edge = first_virtual_edge;
  bool is_endpoint_segment = true;
  do {
    const bool add_legacy_load = merge == DOT_BUNDLE_ACCUMULATE;
    const int legacy_count = ED_count(representative_edge) +
                             (add_legacy_load ? ED_count(original_edge) : 0);
    const int legacy_xpenalty =
        ED_xpenalty(representative_edge) +
        (add_legacy_load ? ED_xpenalty(original_edge) : 0);
    int legacy_position = ED_weight(representative_edge);
    const bool reaches_original_endpoint =
        ND_rank(aghead(representative_edge)) == last_rank;
    if (add_legacy_load &&
        (is_endpoint_segment || reaches_original_endpoint)) {
      legacy_position += ED_weight(original_edge);
    }
    dot_bundle_load_merge(representative_edge, original_edge, merge);
    dot_bundle_load_set_legacy_count(representative_edge, legacy_count);
    dot_bundle_load_set_legacy_xpenalty(representative_edge, legacy_xpenalty);
    dot_bundle_load_set_legacy_position(representative_edge, legacy_position);
    if (ND_rank(aghead(representative_edge)) == last_rank) {
      break;
    }
    if (merge == DOT_BUNDLE_ACCUMULATE) {
      widen_virtual_node(graph, aghead(representative_edge));
    }
    representative_edge = ND_out(aghead(representative_edge)).list[0];
    is_endpoint_segment = false;
  } while (representative_edge != NULL);
}

void merge_chain(graph_t *graph, edge_t *original_edge,
                 edge_t *first_virtual_edge, dot_bundle_merge_t merge) {
  assert(ED_to_virt(original_edge) == NULL);
  ED_to_virt(original_edge) = first_virtual_edge;
  merge_bundle_load_chain(graph, original_edge, first_virtual_edge, merge);
}

bool mergeable(edge_t *first_edge, edge_t *second_edge) {
  return first_edge != NULL && second_edge != NULL &&
         agtail(first_edge) == agtail(second_edge) &&
         aghead(first_edge) == aghead(second_edge) &&
         ED_label(first_edge) == ED_label(second_edge) &&
         ED_xlabel(first_edge) == ED_xlabel(second_edge) &&
         ports_eq(first_edge, second_edge);
}

static bool edge_has_no_labels(edge_t *edge) {
  return ED_label(edge) == NULL && ED_xlabel(edge) == NULL;
}

static bool endpoint_labels_are_route_compatible(edge_t *first_edge,
                                                 edge_t *second_edge) {
  const bool first_has_endpoint_label =
      ED_head_label(first_edge) != NULL || ED_tail_label(first_edge) != NULL;
  const bool second_has_endpoint_label =
      ED_head_label(second_edge) != NULL || ED_tail_label(second_edge) != NULL;
  if (!first_has_endpoint_label && !second_has_endpoint_label) {
    return true;
  }
  return gv_edge_attributes_are_equal(first_edge, second_edge);
}

typedef struct {
  graph_t *graph;
  edge_t *edge;
  edge_t *representative;
  bool opposite_direction;
  bool materialize_representative;
  bool update_previous_edge;
  dot_bundle_merge_t merge;
} edge_chain_candidate_payload_t;

static void transactional_record_edge_info(
    gv_concentration_transaction_t *handle, edge_t *edge) {
  gv_concentration_transaction_record(handle, AGDATA(edge),
                                      sizeof(Agedgeinfo_t));
}

static void transactional_merge_chain(gv_concentration_transaction_t *handle,
                                      graph_t *graph, edge_t *original_edge,
                                      edge_t *first_virtual_edge,
                                      dot_bundle_merge_t merge) {
  gv_concentration_transaction_record(handle, &ED_to_virt(original_edge),
                                      sizeof(ED_to_virt(original_edge)));
  if (merge == DOT_BUNDLE_ACCUMULATE || merge == DOT_BUNDLE_COALESCE) {
    const int last_rank =
        MAX(ND_rank(agtail(original_edge)), ND_rank(aghead(original_edge)));
    for (edge_t *representative = first_virtual_edge; representative != NULL;
         representative = ND_out(aghead(representative)).list[0]) {
      transactional_record_edge_info(handle, representative);
      if (ND_rank(aghead(representative)) == last_rank) {
        break;
      }
      if (merge == DOT_BUNDLE_ACCUMULATE) {
        gv_concentration_transaction_record(
            handle, &ND_lw(aghead(representative)),
            sizeof(ND_lw(aghead(representative))));
        gv_concentration_transaction_record(
            handle, &ND_rw(aghead(representative)),
            sizeof(ND_rw(aghead(representative))));
      }
    }
  }
  merge_chain(graph, original_edge, first_virtual_edge, merge);
}

static void transactional_merge_oneway(gv_concentration_transaction_t *handle,
                                       edge_t *edge, edge_t *representative,
                                       dot_bundle_merge_t merge) {
  gv_concentration_transaction_record(handle, &ED_to_virt(edge),
                                      sizeof(ED_to_virt(edge)));
  for (edge_t *segment = representative; segment != NULL;
       segment = ED_to_virt(segment)) {
    transactional_record_edge_info(handle, segment);
  }
  merge_oneway_with_bundle(edge, representative, merge);
}

static void merge_concentrated_edge_load(graph_t *graph, edge_t *edge,
                                         edge_t *representative,
                                         dot_bundle_merge_t merge) {
  if (ED_to_virt(representative) != NULL) {
    merge_bundle_load_chain(graph, edge, ED_to_virt(representative), merge);
  } else {
    dot_bundle_load_merge(representative, edge, merge);
  }
}

static void transactional_merge_concentrated_edge_load(
    gv_concentration_transaction_t *handle, graph_t *graph, edge_t *edge,
    edge_t *representative, dot_bundle_merge_t merge) {
  if (ED_to_virt(representative) != NULL) {
    const int last_rank = MAX(ND_rank(agtail(edge)), ND_rank(aghead(edge)));
    for (edge_t *segment = ED_to_virt(representative); segment != NULL;
         segment = ND_out(aghead(segment)).list[0]) {
      transactional_record_edge_info(handle, segment);
      if (ND_rank(aghead(segment)) == last_rank) {
        break;
      }
    }
  } else {
    transactional_record_edge_info(handle, representative);
  }
  merge_concentrated_edge_load(graph, edge, representative, merge);
}

static void transactional_other_edge(gv_concentration_transaction_t *handle,
                                     edge_t *edge) {
  gv_concentration_transaction_record_elist(handle, &ND_other(agtail(edge)));
  other_edge(edge);
}

static void transactional_fold_arrows(gv_concentration_transaction_t *handle,
                                      edge_t *representative, edge_t *edge,
                                      bool opposite_direction) {
  gv_concentration_transaction_record_arrow(handle, representative);
  fold_concentrated_edge_arrow_decorations(representative, edge,
                                           opposite_direction);
}

static void transactional_suppress_later_same_direction_duplicates(
    graph_t *graph, edge_t *representative,
    gv_concentration_transaction_t *handle);

static bool
execute_edge_chain_candidate(const gv_concentration_candidate_t *candidate,
                             gv_concentration_transaction_t *handle) {
  edge_chain_candidate_payload_t *const payload = candidate->payload;

  if (payload->materialize_representative &&
      ED_to_virt(payload->representative) == NULL) {
    make_virtual_edge_chain_impl(
        payload->graph, agtail(payload->representative),
        aghead(payload->representative), payload->representative, handle);
  }

  switch (candidate->action) {
  case GV_CONCENTRATION_SUPPRESS_PARALLEL:
    transactional_fold_arrows(handle, payload->representative, payload->edge,
                              false);
    transactional_merge_concentrated_edge_load(
        handle, payload->graph, payload->edge, payload->representative,
        DOT_BUNDLE_COALESCE);
    gv_concentration_transaction_record(handle, &ED_edge_type(payload->edge),
                                        sizeof(ED_edge_type(payload->edge)));
    ED_edge_type(payload->edge) = IGNORED;
    break;
  case GV_CONCENTRATION_SHARE_ROUTE:
    transactional_merge_chain(handle, payload->graph, payload->edge,
                              ED_to_virt(payload->representative),
                              payload->merge);
    transactional_other_edge(handle, payload->edge);
    break;
  case GV_CONCENTRATION_SUPPRESS_OPPOSITE:
    transactional_fold_arrows(handle, payload->representative, payload->edge,
                              payload->opposite_direction);
    transactional_merge_concentrated_edge_load(
        handle, payload->graph, payload->edge, payload->representative,
        DOT_BUNDLE_COALESCE);
    gv_concentration_transaction_record(handle, &ED_edge_type(payload->edge),
                                        sizeof(ED_edge_type(payload->edge)));
    ED_edge_type(payload->edge) = IGNORED;
    gv_concentration_transaction_record(
        handle, &ED_conc_opp_flag(payload->representative),
        sizeof(ED_conc_opp_flag(payload->representative)));
    ED_conc_opp_flag(payload->representative) = true;
    transactional_suppress_later_same_direction_duplicates(
        payload->graph, payload->representative, handle);
    break;
  case GV_CONCENTRATION_SHARE_FLAT_ROUTE:
    transactional_merge_oneway(handle, payload->edge, payload->representative,
                               payload->merge);
    transactional_other_edge(handle, payload->edge);
    break;
  case GV_CONCENTRATION_REPRESENT_CLUSTER:
    represent_intercluster_edge(payload->graph, payload->edge, handle);
    break;
  case GV_CONCENTRATION_NO_MERGE:
  case GV_CONCENTRATION_SUPPRESS_FLAT:
  case GV_CONCENTRATION_MERGE_VIRTUAL_PAIR:
  case GV_CONCENTRATION_FINALIZE_RANKS:
    assert(false);
    return false;
  }
  return true;
}

static edge_t *find_prior_concentrated_representative(graph_t *graph,
                                                      edge_t *edge) {
  /*
   * build_edge_chains() visits a node's outgoing edges in Cgraph order. Only
   * an earlier edge can already own the virtual chain that this edge would
   * duplicate, so stop at the current edge and never let later input affect
   * the decision.
   */
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_owns_chain = ED_to_virt(prior_edge) != NULL;
    const bool both_edges_are_unlabeled =
        edge_has_no_labels(prior_edge) && edge_has_no_labels(edge);

    if (same_endpoints && prior_edge_owns_chain && both_edges_are_unlabeled &&
        ports_eq(prior_edge, edge) &&
        gv_edge_attributes_are_equal(prior_edge, edge) &&
        same_direction_edge_arrow_decorations_are_mergeable(prior_edge, edge)) {
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
        edge_has_no_labels(prior_edge) && edge_has_no_labels(edge);

    if (same_endpoints && prior_edge_owns_chain && both_edges_are_unlabeled &&
        ports_eq(prior_edge, edge) &&
        endpoint_labels_are_route_compatible(prior_edge, edge)) {
      return prior_edge;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return NULL;
}

static void transactional_suppress_later_same_direction_duplicates(
    graph_t *graph, edge_t *representative,
    gv_concentration_transaction_t *handle) {
  edge_t *edge = agnxtout(graph, representative);
  while (edge != NULL) {
    const bool same_endpoints = aghead(edge) == aghead(representative);
    const bool both_edges_are_unlabeled =
        edge_has_no_labels(representative) && edge_has_no_labels(edge);

    if (same_endpoints && ED_edge_type(edge) != IGNORED &&
        both_edges_are_unlabeled && ports_eq(representative, edge) &&
        gv_edge_attributes_are_equal(representative, edge) &&
        same_direction_edge_arrow_decorations_are_mergeable(representative,
                                                            edge)) {
      transactional_fold_arrows(handle, representative, edge, false);
      transactional_merge_concentrated_edge_load(handle, graph, edge,
                                                 representative,
                                                 DOT_BUNDLE_COALESCE);
      gv_concentration_transaction_record(handle, &ED_edge_type(edge),
                                          sizeof(ED_edge_type(edge)));
      ED_edge_type(edge) = IGNORED;
    }

    edge = agnxtout(graph, edge);
  }
}

static edge_t *find_prior_flat_concentrated_equivalent(graph_t *graph,
                                                       edge_t *edge) {
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_is_flat =
        ND_rank(agtail(prior_edge)) == ND_rank(aghead(prior_edge));
    const bool both_edges_are_unlabeled =
        edge_has_no_labels(prior_edge) && edge_has_no_labels(edge);

    if (same_endpoints && prior_edge_is_flat &&
        ED_edge_type(prior_edge) == NORMAL && ED_edge_type(edge) == NORMAL &&
        both_edges_are_unlabeled && ports_eq(prior_edge, edge) &&
        gv_edge_attributes_are_equal(prior_edge, edge) &&
        same_direction_edge_arrow_decorations_are_mergeable(prior_edge, edge)) {
      return prior_edge;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return NULL;
}

typedef struct {
  bool enabled;
  int edge_count;
  int accepted;
  int rejected;
} concentrate_oracle_state_t;

static concentrate_oracle_state_t ConcentrateOracle;

static int concentrate_oracle_limit(void) {
  const char *const value = getenv("GV_CONCENTRATE_ORACLE_MAX_EDGES");
  if (value == NULL || value[0] == '\0') {
    return 24;
  }

  char *end = NULL;
  const long parsed = strtol(value, &end, 10);
  if (end == value || parsed < 0 || parsed > 1000000) {
    return 24;
  }
  return (int)parsed;
}

static int graph_edge_count(graph_t *graph) {
  int edge_count = 0;
  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    for (edge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {
      edge_count++;
    }
  }
  return edge_count;
}

static void concentrate_oracle_begin(graph_t *graph) {
  ConcentrateOracle = (concentrate_oracle_state_t){0};

  const char *const enabled = getenv("GV_CONCENTRATE_ORACLE");
  if (enabled == NULL || enabled[0] == '\0' || strcmp(enabled, "0") == 0 ||
      !Concentrate) {
    return;
  }

  const int edge_count = graph_edge_count(graph);
  const int limit = concentrate_oracle_limit();
  if (edge_count > limit) {
    fprintf(stderr, "concentrate-oracle: skipped graph=%s edges=%d limit=%d\n",
            agnameof(graph), edge_count, limit);
    return;
  }

  ConcentrateOracle.enabled = true;
  ConcentrateOracle.edge_count = edge_count;
  fprintf(
      stderr,
      "concentrate-oracle: begin graph=%s edges=%d limit=%d mode=developer\n",
      agnameof(graph), edge_count, limit);
}

static void concentrate_oracle_end(graph_t *graph) {
  if (!ConcentrateOracle.enabled) {
    return;
  }

  fprintf(stderr,
          "concentrate-oracle: end graph=%s edges=%d candidates_accepted=%d "
          "candidates_rejected=%d metrics=mincross_logical:NA "
          "crossings_visible:NA bundle_shared_length:rank-span-proxy "
          "visible_ink:NA junction_count:rank-span-proxy "
          "max_junction_angle:NA max_tangent_discontinuity:NA bbox:NA "
          "label_overlap:NA node_obstacle_intersections:NA\n",
          agnameof(graph), ConcentrateOracle.edge_count,
          ConcentrateOracle.accepted, ConcentrateOracle.rejected);
}

static int edge_rank_span(edge_t *edge) {
  return abs(ND_rank(agtail(edge)) - ND_rank(aghead(edge)));
}

static int oracle_plan_score(int visible_lanes, int shared_length,
                             int junction_count) {
  return visible_lanes * 100 - shared_length * 4 + junction_count * 9;
}

static void choose_plan(const char *name, int score, char best_name[32],
                        int *best_score) {
  if (score < *best_score) {
    snprintf(best_name, 32, "%s", name);
    *best_score = score;
  }
}

static void trace_concentrate_oracle(edge_t *edge, edge_t *representative,
                                     const char *heuristic, bool accepted) {
  if (!ConcentrateOracle.enabled) {
    return;
  }

  if (accepted) {
    ConcentrateOracle.accepted++;
  } else {
    ConcentrateOracle.rejected++;
  }

  const int span = MAX(edge_rank_span(edge), edge_rank_span(representative));
  char best_name[32] = "no-merge";
  int best_score = oracle_plan_score(1, 0, 0);

  choose_plan("full-trunk", oracle_plan_score(0, span, span > 0 ? 1 : 0),
              best_name, &best_score);
  if (span > 1) {
    choose_plan("join-at-rank", oracle_plan_score(1, span - 1, 1), best_name,
                &best_score);
  }
  if (span > 2) {
    choose_plan("sub-bundles", oracle_plan_score(1, span / 2, 2), best_name,
                &best_score);
  }

  fprintf(stderr,
          "concentrate-oracle: edge=%s->%s representative=%s->%s "
          "heuristic=%s best=%s accepted=%s variants=no-merge,join-at-rank,"
          "sub-bundles,full-trunk rank_span=%d score=%d\n",
          agnameof(agtail(edge)), agnameof(aghead(edge)),
          agnameof(agtail(representative)), agnameof(aghead(representative)),
          heuristic, best_name, accepted ? "true" : "false", span, best_score);
}

/*
 * Suppression is a state transition, not merely an equivalence query. Keep the
 * decision and the IGNORED assignment together so callers cannot accidentally
 * recognize a duplicate without removing its redundant virtual chain.
 */
static void
generate_parallel_candidate(gv_concentration_plan_context_t *context,
                            graph_t *graph, edge_t *edge,
                            gv_concentration_candidate_set_t *set,
                            edge_chain_candidate_payload_t *payload) {
  edge_t *representative = find_prior_concentrated_representative(graph, edge);
  gv_concentration_action_t action = GV_CONCENTRATION_SUPPRESS_PARALLEL;
  uint32_t reasons = GV_CONCENTRATION_REASON_NONE;
  bool legacy_accepts = representative != NULL;
  if (!legacy_accepts) {
    representative = find_prior_parallel_route(graph, edge);
    action = GV_CONCENTRATION_SHARE_ROUTE;
    legacy_accepts = representative != NULL;
    if (!legacy_accepts) {
      reasons |= GV_CONCENTRATION_REASON_NO_REPRESENTATIVE;
    } else if (!nonconstraint_edge(edge) &&
               abs(ND_rank(agtail(edge)) - ND_rank(aghead(edge))) > 1) {
      legacy_accepts = false;
      reasons |= GV_CONCENTRATION_REASON_CONSTRAINT_ROUTE;
    }
  }

  *payload = (edge_chain_candidate_payload_t){
      .graph = graph,
      .edge = edge,
      .representative = representative,
      .merge = action == GV_CONCENTRATION_SHARE_ROUTE ? DOT_BUNDLE_SHARE_ROUTE
                                                       : DOT_BUNDLE_COALESCE,
  };
  gv_concentration_candidate_set_init(context, set, "edge-chains-parallel",
                                      action, legacy_accepts, reasons,
                                      execute_edge_chain_candidate, payload);
}

static bool
route_concentrated_parallel_edge(gv_concentration_plan_context_t *context,
                                 graph_t *graph, edge_t *edge) {
  if (!Concentrate) {
    return false;
  }

  gv_concentration_candidate_set_t set;
  edge_chain_candidate_payload_t payload;
  generate_parallel_candidate(context, graph, edge, &set, &payload);
  const gv_concentration_action_t action = set.candidates[1].action;
  const bool accepted = gv_concentration_apply(context, &set);
  if (payload.representative != NULL) {
    const char *heuristic = action == GV_CONCENTRATION_SUPPRESS_PARALLEL
                                ? "suppress-duplicate"
                            : accepted ? "share-route"
                                       : "reject-long-constrained-route";
    trace_concentrate_oracle(edge, payload.representative, heuristic, accepted);
  }
  return accepted;
}

/*
 * A backward edge can share the chain of a previously classified edge running
 * in the other direction. When concentration is enabled, the opposite edge
 * must also be semantically equal because the backward edge will disappear.
 * Without concentration, both edges remain visible and share only their route.
 */
static void
generate_backward_candidate(gv_concentration_plan_context_t *context,
                            graph_t *graph, edge_t *backward_edge,
                            gv_concentration_candidate_set_t *set,
                            edge_chain_candidate_payload_t *payload) {
  edge_t *representative =
      find_prior_concentrated_representative(graph, backward_edge);
  if (representative != NULL) {
    *payload = (edge_chain_candidate_payload_t){
        .graph = graph,
        .edge = backward_edge,
        .representative = representative,
        .merge = DOT_BUNDLE_COALESCE,
    };
    gv_concentration_candidate_set_init(
        context, set, "edge-chains-backward-parallel",
        GV_CONCENTRATION_SUPPRESS_PARALLEL, true, GV_CONCENTRATION_REASON_NONE,
        execute_edge_chain_candidate, payload);
    return;
  }

  edge_t *fallback_route_edge = NULL;
  edge_t *matching_opposite_edge = NULL;
  for (edge_t *opposite_edge = agfstout(graph, aghead(backward_edge));
       opposite_edge != NULL; opposite_edge = agnxtout(graph, opposite_edge)) {
    const bool connects_same_nodes =
        aghead(opposite_edge) == agtail(backward_edge);
    const bool is_self_edge = aghead(opposite_edge) == aghead(backward_edge);
    const bool is_available = ED_edge_type(opposite_edge) != IGNORED;
    const bool compatible_endpoints =
        edge_has_no_labels(backward_edge) &&
        edge_has_no_labels(opposite_edge) &&
        gv_opposite_edge_ports_are_equal(backward_edge, opposite_edge);
    if (!connects_same_nodes || is_self_edge || !is_available ||
        !compatible_endpoints) {
      continue;
    }
    if (fallback_route_edge == NULL) {
      fallback_route_edge = opposite_edge;
    }
    if (gv_opposite_edge_attributes_are_equal(backward_edge, opposite_edge) &&
        opposite_direction_edge_arrow_decorations_are_mergeable(
            opposite_edge, backward_edge)) {
      matching_opposite_edge = opposite_edge;
      break;
    }
  }

  const bool suppress = matching_opposite_edge != NULL;
  representative = suppress ? matching_opposite_edge : fallback_route_edge;
  const bool legacy_accepts = representative != NULL;
  const gv_concentration_action_t action =
      suppress ? GV_CONCENTRATION_SUPPRESS_OPPOSITE
               : GV_CONCENTRATION_SHARE_ROUTE;
  *payload = (edge_chain_candidate_payload_t){
      .graph = graph,
      .edge = backward_edge,
      .representative = representative,
      .opposite_direction = true,
      .materialize_representative =
          representative != NULL && ED_to_virt(representative) == NULL,
      .merge = suppress ? DOT_BUNDLE_COALESCE : DOT_BUNDLE_SHARE_ROUTE,
  };
  gv_concentration_candidate_set_init(
      context, set, "edge-chains-backward", action, legacy_accepts,
      legacy_accepts ? GV_CONCENTRATION_REASON_NONE
                     : GV_CONCENTRATION_REASON_NO_REPRESENTATIVE,
      execute_edge_chain_candidate, payload);
}

static bool
merge_backward_edge_with_opposite(gv_concentration_plan_context_t *context,
                                  graph_t *graph, edge_t *backward_edge) {
  if (!Concentrate) {
    for (edge_t *opposite_edge = agfstout(graph, aghead(backward_edge));
         opposite_edge != NULL;
         opposite_edge = agnxtout(graph, opposite_edge)) {
      const bool connects_same_nodes =
          aghead(opposite_edge) == agtail(backward_edge);
      const bool is_self_edge = aghead(opposite_edge) == aghead(backward_edge);
      const bool is_available = ED_edge_type(opposite_edge) != IGNORED;
      const bool compatible_endpoints =
          edge_has_no_labels(backward_edge) &&
          edge_has_no_labels(opposite_edge) &&
          gv_opposite_edge_ports_are_equal(backward_edge, opposite_edge);
      if (connects_same_nodes && !is_self_edge && is_available &&
          compatible_endpoints) {
        if (ED_to_virt(opposite_edge) == NULL) {
          make_virtual_edge_chain(graph, agtail(opposite_edge),
                                  aghead(opposite_edge), opposite_edge);
        }
        other_edge(backward_edge);
        merge_chain(graph, backward_edge, ED_to_virt(opposite_edge),
                    DOT_BUNDLE_ACCUMULATE);
        return true;
      }
    }
    return false;
  }

  gv_concentration_candidate_set_t set;
  edge_chain_candidate_payload_t payload;
  generate_backward_candidate(context, graph, backward_edge, &set, &payload);
  const gv_concentration_action_t action = set.candidates[1].action;
  const bool accepted = gv_concentration_apply(context, &set);
  if (accepted && payload.representative != NULL) {
    if (action == GV_CONCENTRATION_SUPPRESS_OPPOSITE) {
      trace_concentrate_oracle(backward_edge, payload.representative,
                               "suppress-opposite", true);
    } else if (action == GV_CONCENTRATION_SHARE_ROUTE) {
      trace_concentrate_oracle(backward_edge, payload.representative,
                               "share-opposite-route", true);
    }
  }
  return accepted;
}

static void
generate_cluster_opposite_candidate(gv_concentration_plan_context_t *context,
                                    edge_t *edge,
                                    gv_concentration_candidate_set_t *set,
                                    edge_chain_candidate_payload_t *payload) {
  edge_t *representative = NULL;
  for (edge_t *opposite_edge = agfstout(agraphof(edge), aghead(edge));
       opposite_edge != NULL;
       opposite_edge = agnxtout(agraphof(edge), opposite_edge)) {
    const bool connects_same_nodes = aghead(opposite_edge) == agtail(edge);
    const bool is_available = ED_edge_type(opposite_edge) != IGNORED;
    const bool owns_route = ED_to_virt(opposite_edge) != NULL;
    const bool both_edges_are_unlabeled = edge_has_no_labels(opposite_edge);

    if (edge_has_no_labels(edge) && connects_same_nodes && is_available &&
        owns_route && both_edges_are_unlabeled &&
        gv_opposite_edge_ports_are_equal(edge, opposite_edge) &&
        gv_opposite_edge_attributes_are_equal(edge, opposite_edge) &&
        opposite_direction_edge_arrow_decorations_are_mergeable(opposite_edge,
                                                                edge)) {
      representative = opposite_edge;
      break;
    }
  }

  const bool legacy_accepts = representative != NULL;
  *payload = (edge_chain_candidate_payload_t){
      .graph = agraphof(edge),
      .edge = edge,
      .representative = representative,
      .opposite_direction = true,
      .merge = DOT_BUNDLE_COALESCE,
  };
  gv_concentration_candidate_set_init(
      context, set, "edge-chains-cluster-opposite",
      GV_CONCENTRATION_SUPPRESS_OPPOSITE, legacy_accepts,
      edge_has_no_labels(edge) ? GV_CONCENTRATION_REASON_NO_REPRESENTATIVE
                               : GV_CONCENTRATION_REASON_INCOMPATIBLE_ENDPOINTS,
      execute_edge_chain_candidate, payload);
}

static bool suppress_concentrated_cluster_edge_with_opposite(
    gv_concentration_plan_context_t *context, edge_t *edge) {
  if (!Concentrate) {
    return false;
  }

  gv_concentration_candidate_set_t set;
  edge_chain_candidate_payload_t payload;
  generate_cluster_opposite_candidate(context, edge, &set, &payload);
  return gv_concentration_apply(context, &set);
}

static void generate_cluster_fallback_candidate(
    gv_concentration_plan_context_t *context, graph_t *graph, edge_t *edge,
    edge_t *previous_edge, gv_concentration_candidate_set_t *set,
    edge_chain_candidate_payload_t *payload) {
  const bool edges_merge = mergeable(previous_edge, edge);
  const bool shares_chain = edges_merge && ED_to_virt(previous_edge) != NULL;
  const bool shares_flat_route = edges_merge && !shares_chain &&
                                 ND_rank(agtail(edge)) == ND_rank(aghead(edge));
  const bool represents_edge = !edges_merge;

  gv_concentration_action_t action = GV_CONCENTRATION_REPRESENT_CLUSTER;
  if (shares_chain) {
    action = GV_CONCENTRATION_SHARE_ROUTE;
  } else if (shares_flat_route) {
    action = GV_CONCENTRATION_SHARE_FLAT_ROUTE;
  }
  const dot_bundle_merge_t merge =
      shares_chain ? DOT_BUNDLE_ALIAS : DOT_BUNDLE_ACCUMULATE;

  *payload = (edge_chain_candidate_payload_t){
      .graph = graph,
      .edge = edge,
      .representative = previous_edge,
      .update_previous_edge = represents_edge,
      .merge = merge,
  };
  const bool legacy_accepts =
      shares_chain || shares_flat_route || represents_edge;
  gv_concentration_candidate_set_init(
      context, set, "edge-chains-cluster-fallback", action, legacy_accepts,
      GV_CONCENTRATION_REASON_NO_REPRESENTATIVE, execute_edge_chain_candidate,
      payload);
}

static bool
route_cluster_edge_fallback(gv_concentration_plan_context_t *context,
                            graph_t *graph, edge_t *edge,
                            edge_t *previous_edge) {
  if (!Concentrate) {
    if (mergeable(previous_edge, edge)) {
      if (ED_to_virt(previous_edge) != NULL) {
        merge_chain(graph, edge, ED_to_virt(previous_edge), DOT_BUNDLE_ALIAS);
        other_edge(edge);
      } else if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
        merge_oneway(edge, previous_edge);
        other_edge(edge);
      }
      return false;
    }
    represent_intercluster_edge(graph, edge, NULL);
    return true;
  }

  gv_concentration_candidate_set_t set;
  edge_chain_candidate_payload_t payload;
  generate_cluster_fallback_candidate(context, graph, edge, previous_edge, &set,
                                      &payload);
  (void)gv_concentration_apply(context, &set);
  return payload.update_previous_edge;
}

static void
generate_flat_route_candidate(gv_concentration_plan_context_t *context,
                              graph_t *graph, edge_t *edge,
                              edge_t *representative, bool legacy_accepts,
                              dot_bundle_merge_t merge,
                              gv_concentration_candidate_set_t *set,
                              edge_chain_candidate_payload_t *payload) {
  *payload = (edge_chain_candidate_payload_t){
      .graph = graph,
      .edge = edge,
      .representative = representative,
      .merge = merge,
  };
  gv_concentration_candidate_set_init(
      context, set, "edge-chains-flat-route", GV_CONCENTRATION_SHARE_FLAT_ROUTE,
      legacy_accepts,
      legacy_accepts ? GV_CONCENTRATION_REASON_NONE
                     : GV_CONCENTRATION_REASON_INCOMPATIBLE_ENDPOINTS,
      execute_edge_chain_candidate, payload);
}

static bool
route_concentrated_flat_edge(gv_concentration_plan_context_t *context,
                             graph_t *graph, edge_t *edge,
                             edge_t *representative, bool legacy_accepts,
                             dot_bundle_merge_t merge) {
  gv_concentration_candidate_set_t set;
  edge_chain_candidate_payload_t payload;
  generate_flat_route_candidate(context, graph, edge, representative,
                                legacy_accepts, merge, &set, &payload);
  return gv_concentration_apply(context, &set);
}

void build_edge_chains(graph_t *graph) {
  gv_concentration_plan_context_t concentration_context;
  gv_concentration_plan_context_init(&concentration_context);
  GD_nlist(graph) = NULL;
  concentrate_oracle_begin(graph);

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

      if (ED_edge_type(edge) == IGNORED) {
        continue;
      }

      /* A prior cluster operation has already attached this representation. */
      if (ED_to_virt(edge) != NULL) {
        previous_edge = edge;
        continue;
      }

      /* Edges incident to a collapsed sub-cluster use its rank skeleton. */
      if (is_cluster_edge(edge)) {
        if (suppress_concentrated_cluster_edge_with_opposite(
                &concentration_context, edge)) {
          continue;
        }
        if (route_concentrated_parallel_edge(&concentration_context, graph,
                                             edge)) {
          continue;
        }
        if (route_cluster_edge_fallback(&concentration_context, graph, edge,
                                        previous_edge)) {
          previous_edge = edge;
        }
        continue;
      }

      /* Parallel input edges may share one virtual routing representation. */
      if (previous_edge != NULL && agtail(edge) == agtail(previous_edge) &&
          aghead(edge) == aghead(previous_edge)) {
        if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
          edge_t *representative_edge = previous_edge;
          dot_bundle_merge_t merge = DOT_BUNDLE_ACCUMULATE;
          if (Concentrate) {
            edge_t *const equivalent_edge =
                find_prior_flat_concentrated_equivalent(graph, edge);
            if (equivalent_edge != NULL) {
              representative_edge = equivalent_edge;
              merge = DOT_BUNDLE_COALESCE;
            }
          }
          const bool flat_edges_are_mergeable =
              mergeable(representative_edge, edge) &&
              endpoint_labels_are_route_compatible(representative_edge, edge);
          if (Concentrate) {
            if (route_concentrated_flat_edge(&concentration_context, graph,
                                             edge, representative_edge,
                                             flat_edges_are_mergeable,
                                             merge)) {
              continue;
            }
          } else if (flat_edges_are_mergeable) {
            merge_oneway(edge, representative_edge);
            other_edge(edge);
            continue;
          }
        }
        if (edge_has_no_labels(edge) && edge_has_no_labels(previous_edge) &&
            ports_eq(edge, previous_edge) &&
            endpoint_labels_are_route_compatible(edge, previous_edge)) {
          if (Concentrate) {
            if (route_concentrated_parallel_edge(&concentration_context, graph,
                                                 edge)) {
              continue;
            }
          } else {
            merge_chain(graph, edge, ED_to_virt(previous_edge),
                        DOT_BUNDLE_ACCUMULATE);
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
        if (route_concentrated_parallel_edge(&concentration_context, graph,
                                             edge)) {
          continue;
        }
        make_virtual_edge_chain(graph, agtail(edge), aghead(edge), edge);
        previous_edge = edge;
        continue;
      }

      /* Store every rank-spanning chain from lower rank to higher rank. */
      if (merge_backward_edge_with_opposite(&concentration_context, graph,
                                            edge)) {
        continue;
      }
      if (route_concentrated_parallel_edge(&concentration_context, graph,
                                           edge)) {
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
  concentrate_oracle_end(graph);
}
