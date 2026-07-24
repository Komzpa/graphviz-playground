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
 *	build edge_t concentrators for parallel edges with a common endpoint
 */

#include "config.h"

#include <common/edgeattr.h>
#include <dotgen/concentrate_plan.h>
#include <dotgen/dot.h>
#include <stdbool.h>
#include <string.h>

#define UP 0
#define DOWN 1

static edge_t *original_normal_edge(edge_t *edge) {
  while (edge != NULL && ED_edge_type(edge) != NORMAL)
    edge = ED_to_orig(edge);
  return edge;
}

static bool original_edges_have_same_rank_direction(edge_t *e, edge_t *f) {
  e = original_normal_edge(e);
  f = original_normal_edge(f);
  if (e == NULL || f == NULL)
    return false;
  if (ED_conc_opp_flag(e))
    return false;
  if (ED_conc_opp_flag(f))
    return false;
  return (ND_rank(agtail(f)) - ND_rank(aghead(f))) *
             (ND_rank(agtail(e)) - ND_rank(aghead(e))) >
         0;
}

static bool rendered_edges_are_equal(edge_t *edge, edge_t *representative) {
  return edge != NULL && representative != NULL &&
         gv_edge_attributes_are_equal(edge, representative) &&
         same_direction_edge_arrow_decorations_are_mergeable(representative,
                                                             edge);
}

static bool has_labeled_continuation(edge_t *edge) {
  for (edge_t *candidate = agfstout(agraphof(edge), aghead(edge));
       candidate != NULL; candidate = agnxtout(agraphof(edge), candidate)) {
    if (ED_edge_type(candidate) == NORMAL && ED_label(candidate) != NULL)
      return true;
  }
  return false;
}

static bool dense_same_head_labeled_fan(edge_t *edge, edge_t *other) {
  if (ED_label(edge) == NULL || ED_label(other) == NULL ||
      aghead(edge) != aghead(other) || !rendered_edges_are_equal(edge, other) ||
      !has_labeled_continuation(edge))
    return false;

  size_t equivalent_labeled_edges = 0;
  for (edge_t *candidate = agfstin(agraphof(edge), aghead(edge));
       candidate != NULL; candidate = agnxtin(agraphof(edge), candidate)) {
    if (ED_edge_type(candidate) == NORMAL && ED_label(candidate) != NULL &&
        rendered_edges_are_equal(candidate, edge))
      equivalent_labeled_edges++;
  }
  return equivalent_labeled_edges >= 4;
}

static bool dense_same_head_labeled_fan_can_join_at(edge_t *edge, edge_t *other,
                                                    node_t *join) {
  if (!dense_same_head_labeled_fan(edge, other))
    return true;
  if (strchr(ED_label(edge)->text, ' ') == NULL ||
      strchr(ED_label(other)->text, ' ') == NULL)
    return true;

  double lowest_tail_y = HUGE_VAL;
  for (edge_t *candidate = agfstin(agraphof(edge), aghead(edge));
       candidate != NULL; candidate = agnxtin(agraphof(edge), candidate)) {
    if (ED_edge_type(candidate) == NORMAL && ED_label(candidate) != NULL &&
        rendered_edges_are_equal(candidate, edge))
      lowest_tail_y = MIN(lowest_tail_y, ND_coord(agtail(candidate)).y);
  }
  return lowest_tail_y == HUGE_VAL ||
         ND_coord(join).y < lowest_tail_y - MILLIPOINT;
}

static bool other_list_contains(edge_t *edge) {
  if (ND_other(agtail(edge)).list == NULL)
    return false;
  for (size_t i = 0; ND_other(agtail(edge)).list[i] != NULL; ++i) {
    if (ND_other(agtail(edge)).list[i] == edge)
      return true;
  }
  return false;
}

static void
keep_distinct_original_drawn(edge_t *edge, edge_t *representative,
                             gv_concentration_transaction_t *handle) {
  edge_t *const original_edge = original_normal_edge(edge);
  edge_t *const representative_edge = original_normal_edge(representative);

  if (original_edge != NULL && ED_edge_type(original_edge) == NORMAL &&
      !rendered_edges_are_equal(original_edge, representative_edge) &&
      !other_list_contains(original_edge)) {
    gv_concentration_transaction_record_elist(handle,
                                              &ND_other(agtail(original_edge)));
    other_edge(original_edge);
  }
}

static bool downcandidate(node_t *v) {
  return ND_node_type(v) == VIRTUAL && ND_in(v).size == 1 &&
         ND_out(v).size == 1 && ND_label(v) == NULL;
}

static bool bothdowncandidates(node_t *u, node_t *v) {
  edge_t *e, *f;
  e = ND_in(u).list[0];
  f = ND_in(v).list[0];
  if (downcandidate(v) && agtail(e) == agtail(f)) {
    edge_t *e0 = original_normal_edge(e);
    edge_t *f0 = original_normal_edge(f);
    return original_edges_have_same_rank_direction(e, f) &&
           rendered_edges_are_equal(f0, e0) && gv_edge_ports_are_equal(e, f) &&
           e0 != NULL && f0 != NULL &&
           dense_same_head_labeled_fan_can_join_at(e0, f0, u) &&
           gv_edge_ports_are_equal(e0, f0);
  }
  return false;
}

static bool upcandidate(node_t *v) {
  return ND_node_type(v) == VIRTUAL && ND_out(v).size == 1 &&
         ND_in(v).size == 1 && ND_label(v) == NULL;
}

static bool bothupcandidates(node_t *u, node_t *v) {
  edge_t *e, *f;
  e = ND_out(u).list[0];
  f = ND_out(v).list[0];
  if (upcandidate(v) && aghead(e) == aghead(f)) {
    edge_t *e0 = original_normal_edge(e);
    edge_t *f0 = original_normal_edge(f);
    return original_edges_have_same_rank_direction(e, f) &&
           rendered_edges_are_equal(f0, e0) && gv_edge_ports_are_equal(e, f) &&
           e0 != NULL && f0 != NULL &&
           dense_same_head_labeled_fan_can_join_at(e0, f0, u) &&
           (agtail(e0) != agtail(f0) || gv_edge_ports_are_equal(e0, f0));
  }
  return false;
}

static edge_t *
transactional_virtual_edge(node_t *tail, node_t *head, edge_t *original_edge,
                           gv_concentration_transaction_t *handle) {
  gv_concentration_transaction_record_elist(handle, &ND_out(tail));
  gv_concentration_transaction_record_elist(handle, &ND_in(head));
  if (original_edge != NULL)
    gv_concentration_transaction_record(handle, &ED_to_virt(original_edge),
                                        sizeof(ED_to_virt(original_edge)));
  edge_t *const edge = virtual_edge(tail, head, original_edge);
  gv_concentration_transaction_track_virtual_edge(handle, edge);
  return edge;
}

static void
transactional_delete_fast_edge(gv_concentration_transaction_t *handle,
                               edge_t *edge) {
  gv_concentration_transaction_record_elist(handle, &ND_out(agtail(edge)));
  gv_concentration_transaction_record_elist(handle, &ND_in(aghead(edge)));
  delete_fast_edge(edge);
}

static void
transactional_delete_fast_node(gv_concentration_transaction_t *handle,
                               graph_t *graph, node_t *node) {
  gv_concentration_transaction_record(handle, &GD_nlist(graph),
                                      sizeof(GD_nlist(graph)));
  if (ND_next(node) != NULL)
    gv_concentration_transaction_record(handle, &ND_prev(ND_next(node)),
                                        sizeof(ND_prev(ND_next(node))));
  if (ND_prev(node) != NULL)
    gv_concentration_transaction_record(handle, &ND_next(ND_prev(node)),
                                        sizeof(ND_next(ND_prev(node))));
  delete_fast_node(graph, node);
}

static void transactional_merge_oneway(gv_concentration_transaction_t *handle,
                                       edge_t *edge, edge_t *representative) {
  gv_concentration_transaction_record(handle, &ED_to_virt(edge),
                                      sizeof(ED_to_virt(edge)));
  gv_concentration_transaction_record(handle, &ED_minlen(representative),
                                      sizeof(ED_minlen(representative)));
  for (edge_t *segment = representative; segment != NULL;
       segment = ED_to_virt(segment)) {
    gv_concentration_transaction_record(handle, &ED_count(segment),
                                        sizeof(ED_count(segment)));
    gv_concentration_transaction_record(handle, &ED_xpenalty(segment),
                                        sizeof(ED_xpenalty(segment)));
    gv_concentration_transaction_record(handle, &ED_weight(segment),
                                        sizeof(ED_weight(segment)));
  }
  merge_oneway(edge, representative);
}

static void
add_concentrated_segment_weight(edge_t *edge, edge_t *representative,
                                gv_concentration_transaction_t *handle) {
  while (representative != NULL) {
    gv_concentration_transaction_record(handle, &ED_weight(representative),
                                        sizeof(ED_weight(representative)));
    ED_weight(representative) += ED_weight(edge);
    representative = ED_to_virt(representative);
  }
}

static void mergevirtual_pair(graph_t *g, int r, int lpos, int rpos, int dir,
                              gv_concentration_transaction_t *handle) {
  node_t *left;
  edge_t *e, *f, *e0;

  left = GD_rank(g)[r].v[lpos];
  node_t *const right = GD_rank(g)[r].v[rpos];
  if (dir == DOWN) {
    while ((e = ND_out(right).list[0])) {
      int k;
      for (k = 0; (f = ND_out(left).list[k]); k++)
        if (aghead(f) == aghead(e))
          break;
      if (f == NULL)
        f = transactional_virtual_edge(left, aghead(e), e, handle);
      else
        add_concentrated_segment_weight(e, f, handle);
      while ((e0 = ND_in(right).list[0])) {
        keep_distinct_original_drawn(e0, f, handle);
        transactional_merge_oneway(handle, e0, f);
        transactional_delete_fast_edge(handle, e0);
      }
      transactional_delete_fast_edge(handle, e);
    }
  } else {
    while ((e = ND_in(right).list[0])) {
      int k;
      for (k = 0; (f = ND_in(left).list[k]); k++)
        if (agtail(f) == agtail(e))
          break;
      if (f == NULL)
        f = transactional_virtual_edge(agtail(e), left, e, handle);
      else
        add_concentrated_segment_weight(e, f, handle);
      while ((e0 = ND_out(right).list[0])) {
        keep_distinct_original_drawn(e0, f, handle);
        transactional_merge_oneway(handle, e0, f);
        transactional_delete_fast_edge(handle, e0);
      }
      transactional_delete_fast_edge(handle, e);
    }
  }
  assert(ND_in(right).size + ND_out(right).size == 0);
  transactional_delete_fast_node(handle, g, right);

  gv_concentration_transaction_record(handle, GD_rank(g)[r].v,
                                      ((size_t)GD_rank(g)[r].n + 1) *
                                          sizeof(*GD_rank(g)[r].v));
  gv_concentration_transaction_record(handle, &GD_rank(g)[r].n,
                                      sizeof(GD_rank(g)[r].n));
  int k = rpos;
  for (int i = rpos + 1; i < GD_rank(g)[r].n; ++i) {
    node_t *const n = GD_rank(g)[r].v[k] = GD_rank(g)[r].v[i];
    gv_concentration_transaction_record(handle, &ND_order(n),
                                        sizeof(ND_order(n)));
    ND_order(n) = k;
    k++;
  }
  GD_rank(g)[r].n = k;
  GD_rank(g)[r].v[GD_rank(g)[r].n] = NULL;
}

static void infuse(graph_t *g, node_t *n) {
  node_t *lead;

  lead = GD_rankleader(g)[ND_rank(n)];
  if (lead == NULL || ND_order(lead) > ND_order(n))
    GD_rankleader(g)[ND_rank(n)] = n;
}

static int rebuild_vlists(graph_t *g) {
  int c, i, r, maxi;
  node_t *n, *lead;
  edge_t *rep;

  for (r = GD_minrank(g); r <= GD_maxrank(g); r++)
    GD_rankleader(g)[r] = NULL;
  dot_scan_ranks(g);
  for (n = agfstnode(g); n; n = agnxtnode(g, n)) {
    infuse(g, n);
    for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
      for (rep = e; ED_to_virt(rep); rep = ED_to_virt(rep))
        ;
      while (rep != NULL && ND_rank(aghead(rep)) < ND_rank(aghead(e))) {
        infuse(g, aghead(rep));
        rep = ND_out(aghead(rep)).list[0];
      }
    }
  }

  for (r = GD_minrank(g); r <= GD_maxrank(g); r++) {
    lead = GD_rankleader(g)[r];
    if (lead == NULL) {
      agerrorf("rebuild_vlists: lead is null for rank %d\n", r);
      return -1;
    } else if (GD_rank(dot_root(g))[r].v[ND_order(lead)] != lead) {
      agerrorf("rebuild_vlists: rank lead %s not in order %d of rank %d\n",
               agnameof(lead), ND_order(lead), r);
      return -1;
    }
    GD_rank(g)[r].v = GD_rank(dot_root(g))[r].v + ND_order(GD_rankleader(g)[r]);
    maxi = -1;
    for (i = 0; i < GD_rank(g)[r].n; i++) {
      if ((n = GD_rank(g)[r].v[i]) == NULL)
        break;
      if (ND_node_type(n) == NORMAL) {
        if (agcontains(g, n))
          maxi = i;
        else
          break;
      } else {
        edge_t *e;
        for (e = ND_in(n).list[0]; e && ED_to_orig(e); e = ED_to_orig(e))
          ;
        if (e && agcontains(g, agtail(e)) && agcontains(g, aghead(e)))
          maxi = i;
      }
    }
    if (maxi == -1)
      agwarningf("degenerate concentrated rank %s,%d\n", agnameof(g), r);
    GD_rank(g)[r].n = maxi + 1;
  }

  for (c = 1; c <= GD_n_cluster(g); c++) {
    int ret = rebuild_vlists(GD_clust(g)[c]);
    if (ret != 0) {
      return ret;
    }
  }
  return 0;
}

static bool edges_run_in_opposite_directions(edge_t *first_edge,
                                             edge_t *second_edge) {
  return agtail(first_edge) == aghead(second_edge) &&
         aghead(first_edge) == agtail(second_edge) &&
         agtail(first_edge) != aghead(first_edge);
}

static bool flat_edges_are_equivalent(edge_t *edge,
                                      edge_t *representative_edge) {
  if (representative_edge == NULL) {
    return false;
  }
  if (ED_edge_type(edge) != NORMAL ||
      ED_edge_type(representative_edge) != NORMAL) {
    /*
     * flat_breakcycles() can represent a same-rank cycle by a manually
     * allocated REVERSED edge. That route artifact is not a Cgraph edge, so it
     * must not be used for agxget()/agbindrec()-based semantic comparison or
     * as the retained original edge for arrow recovery.
     */
    return false;
  }

  const bool edge_is_flat = ND_rank(agtail(edge)) == ND_rank(aghead(edge));
  if (!edge_is_flat) {
    return false;
  }

  /*
   * Main edge labels are routed separately. Endpoint labels, URLs, targets, and
   * tooltips remain in the attribute comparison below so reverse edges can
   * concentrate only when they match at the same physical endpoint.
   */
  if (ED_label(edge) != NULL || ED_label(representative_edge) != NULL ||
      ED_xlabel(edge) != NULL || ED_xlabel(representative_edge) != NULL) {
    return false;
  }

  const bool same_direction = agtail(edge) == agtail(representative_edge) &&
                              aghead(edge) == aghead(representative_edge);
  if (same_direction) {
    return ports_eq(edge, representative_edge) &&
           gv_edge_attributes_are_equal(edge, representative_edge) &&
           same_direction_edge_arrow_decorations_are_mergeable(
               representative_edge, edge);
  }

  return edges_run_in_opposite_directions(edge, representative_edge) &&
         gv_opposite_edge_ports_are_equal(edge, representative_edge) &&
         gv_opposite_edge_attributes_are_equal(edge, representative_edge) &&
         opposite_direction_edge_arrow_decorations_are_mergeable(
             representative_edge, edge);
}

typedef struct {
  graph_t *graph;
  edge_t *edge;
  edge_t *representative;
  int rank;
  int left_position;
  int right_position;
  int direction;
} concentration_candidate_payload_t;

static void record_rebuild_vlists_state(gv_concentration_transaction_t *handle,
                                        graph_t *graph) {
  gv_concentration_transaction_record(handle, &GD_minrank(graph),
                                      sizeof(GD_minrank(graph)));
  gv_concentration_transaction_record(handle, &GD_maxrank(graph),
                                      sizeof(GD_maxrank(graph)));
  gv_concentration_transaction_record(handle, &GD_leader(graph),
                                      sizeof(GD_leader(graph)));
  if (GD_rankleader(graph) != NULL) {
    gv_concentration_transaction_record(handle, GD_rankleader(graph),
                                        ((size_t)GD_maxrank(graph) + 2) *
                                            sizeof(*GD_rankleader(graph)));
  }
  if (GD_rank(graph) != NULL) {
    for (int rank = GD_minrank(graph); rank <= GD_maxrank(graph); rank++) {
      gv_concentration_transaction_record(handle, &GD_rank(graph)[rank].v,
                                          sizeof(GD_rank(graph)[rank].v));
      gv_concentration_transaction_record(handle, &GD_rank(graph)[rank].n,
                                          sizeof(GD_rank(graph)[rank].n));
    }
  }
  for (int cluster = 1; cluster <= GD_n_cluster(graph); cluster++) {
    record_rebuild_vlists_state(handle, GD_clust(graph)[cluster]);
  }
}

static bool
execute_concentration_candidate(const gv_concentration_candidate_t *candidate,
                                gv_concentration_transaction_t *handle) {
  concentration_candidate_payload_t *const payload = candidate->payload;

  switch (candidate->action) {
  case GV_CONCENTRATION_SUPPRESS_FLAT: {
    gv_concentration_transaction_record_arrow(handle, payload->representative);
    const bool opposite_direction = edges_run_in_opposite_directions(
        payload->edge, payload->representative);
    fold_concentrated_edge_arrow_decorations(payload->representative,
                                             payload->edge, opposite_direction);
    if (opposite_direction) {
      gv_concentration_transaction_record(
          handle, &ED_conc_opp_flag(payload->representative),
          sizeof(ED_conc_opp_flag(payload->representative)));
      ED_conc_opp_flag(payload->representative) = true;
    }
    gv_concentration_transaction_record_elist(handle,
                                              &ND_other(agtail(payload->edge)));
    zapinlist(&ND_other(agtail(payload->edge)), payload->edge);
    gv_concentration_transaction_record(handle, &ED_edge_type(payload->edge),
                                        sizeof(ED_edge_type(payload->edge)));
    ED_edge_type(payload->edge) = IGNORED;
    break;
  }
  case GV_CONCENTRATION_MERGE_VIRTUAL_PAIR:
    mergevirtual_pair(payload->graph, payload->rank, payload->left_position,
                      payload->right_position, payload->direction, handle);
    break;
  case GV_CONCENTRATION_FINALIZE_RANKS:
    record_rebuild_vlists_state(handle, payload->graph);
    return rebuild_vlists(payload->graph) == 0;
  case GV_CONCENTRATION_NO_MERGE:
  case GV_CONCENTRATION_SUPPRESS_PARALLEL:
  case GV_CONCENTRATION_SHARE_ROUTE:
  case GV_CONCENTRATION_SUPPRESS_OPPOSITE:
  case GV_CONCENTRATION_SHARE_FLAT_ROUTE:
  case GV_CONCENTRATION_REPRESENT_CLUSTER:
    assert(false);
    return false;
  }
  return true;
}

static void
generate_flat_candidate(gv_concentration_plan_context_t *context,
                        graph_t *graph, edge_t *edge, edge_t *representative,
                        gv_concentration_candidate_set_t *set,
                        concentration_candidate_payload_t *payload) {
  const bool legacy_accepts = flat_edges_are_equivalent(edge, representative);
  *payload = (concentration_candidate_payload_t){
      .graph = graph,
      .edge = edge,
      .representative = representative,
  };
  gv_concentration_candidate_set_init(
      context, set, "concentrate-flat", GV_CONCENTRATION_SUPPRESS_FLAT,
      legacy_accepts,
      legacy_accepts ? GV_CONCENTRATION_REASON_NONE
                     : GV_CONCENTRATION_REASON_INCOMPATIBLE_ATTRIBUTES,
      execute_concentration_candidate, payload);
}

static void concentrate_flat_edges(gv_concentration_plan_context_t *context,
                                   graph_t *graph) {
  /*
   * The virtual-node passes below require an intermediate rank, so they never
   * visit same-rank edges. flat_breakcycles() and build_edge_chains() have
   * already placed flat duplicates in ND_other(); ED_to_virt() points from
   * each duplicate to its representative. ND_other() contains both
   * same-direction and reversed edges, so select the matching comparison
   * before suppressing anything.
   */
  for (node_t *node = GD_nlist(graph); node != NULL; node = ND_next(node)) {
    if (ND_other(node).list == NULL) {
      continue;
    }

    size_t edge_index = 0;
    while (ND_other(node).list[edge_index] != NULL) {
      edge_t *const edge = ND_other(node).list[edge_index];
      edge_t *const representative_edge = ED_to_virt(edge);

      gv_concentration_candidate_set_t set;
      concentration_candidate_payload_t payload;
      generate_flat_candidate(context, graph, edge, representative_edge, &set,
                              &payload);
      if (gv_concentration_apply(context, &set)) {
        continue;
      }
      edge_index++;
    }
  }
}

static void
generate_virtual_pair_candidate(gv_concentration_plan_context_t *context,
                                graph_t *graph, int rank, int left_position,
                                int right_position, int direction,
                                gv_concentration_candidate_set_t *set,
                                concentration_candidate_payload_t *payload) {
  node_t *const left = GD_rank(graph)[rank].v[left_position];
  node_t *const right = GD_rank(graph)[rank].v[right_position];
  const bool legacy_accepts = direction == DOWN
                                  ? bothdowncandidates(left, right)
                                  : bothupcandidates(left, right);
  *payload = (concentration_candidate_payload_t){
      .graph = graph,
      .rank = rank,
      .left_position = left_position,
      .right_position = right_position,
      .direction = direction,
  };
  gv_concentration_candidate_set_init(
      context, set,
      direction == DOWN ? "concentrate-virtual-down" : "concentrate-virtual-up",
      GV_CONCENTRATION_MERGE_VIRTUAL_PAIR, legacy_accepts,
      legacy_accepts ? GV_CONCENTRATION_REASON_NONE
                     : GV_CONCENTRATION_REASON_LEGACY_REJECTED,
      execute_concentration_candidate, payload);
}

static bool try_virtual_pair_candidate(gv_concentration_plan_context_t *context,
                                       graph_t *graph, int rank,
                                       int left_position, int right_position,
                                       int direction) {
  gv_concentration_candidate_set_t set;
  concentration_candidate_payload_t payload;
  generate_virtual_pair_candidate(context, graph, rank, left_position,
                                  right_position, direction, &set, &payload);
  return gv_concentration_apply(context, &set);
}

static void
generate_finalize_candidate(gv_concentration_plan_context_t *context,
                            graph_t *graph,
                            gv_concentration_candidate_set_t *set,
                            concentration_candidate_payload_t *payload) {
  *payload = (concentration_candidate_payload_t){
      .graph = graph,
  };
  gv_concentration_candidate_set_init(
      context, set, "concentrate-finalize-ranks",
      GV_CONCENTRATION_FINALIZE_RANKS, true, GV_CONCENTRATION_REASON_NONE,
      execute_concentration_candidate, payload);
}

int dot_concentrate(graph_t *g) {
  int c, r, leftpos, rightpos;
  node_t *left;

  gv_concentration_plan_context_t concentration_context;
  gv_concentration_plan_context_init(&concentration_context);
  concentrate_flat_edges(&concentration_context, g);
  if (GD_maxrank(g) - GD_minrank(g) <= 1) {
    return 0;
  }
  /* this is the downward looking pass. r is a candidate rank. */
  for (r = 1; GD_rank(g)[r + 1].n; r++) {
    for (leftpos = 0; leftpos < GD_rank(g)[r].n; leftpos++) {
      left = GD_rank(g)[r].v[leftpos];
      if (!downcandidate(left))
        continue;
      for (rightpos = leftpos + 1; rightpos < GD_rank(g)[r].n;) {
        if (!try_virtual_pair_candidate(&concentration_context, g, r, leftpos,
                                        rightpos, DOWN)) {
          if (!agisdirected(g))
            break;
          rightpos++;
          continue;
        }
      }
    }
  }
  /* this is the corresponding upward pass */
  while (r > 0) {
    for (leftpos = 0; leftpos < GD_rank(g)[r].n; leftpos++) {
      left = GD_rank(g)[r].v[leftpos];
      if (!upcandidate(left))
        continue;
      for (rightpos = leftpos + 1; rightpos < GD_rank(g)[r].n;) {
        if (!try_virtual_pair_candidate(&concentration_context, g, r, leftpos,
                                        rightpos, UP)) {
          if (!agisdirected(g))
            break;
          rightpos++;
          continue;
        }
      }
    }
    r--;
  }
  for (c = 1; c <= GD_n_cluster(g); c++) {
    gv_concentration_candidate_set_t set;
    concentration_candidate_payload_t payload;
    generate_finalize_candidate(&concentration_context, GD_clust(g)[c], &set,
                                &payload);
    if (!gv_concentration_apply(&concentration_context, &set)) {
      agerr(AGPREV, "concentrate=true may not work correctly.\n");
      return -1;
    }
  }
  return 0;
}
