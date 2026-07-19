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
#include	<dotgen/dot.h>
#include	<stdbool.h>

#define		UP		0
#define		DOWN	1

static bool samedir(edge_t * e, edge_t * f)
{
    edge_t *e0, *f0;

    for (e0 = e; e0 != NULL && ED_edge_type(e0) != NORMAL; e0 = ED_to_orig(e0));
    if (e0 == NULL)
	return false;
    for (f0 = f; f0 != NULL && ED_edge_type(f0) != NORMAL; f0 = ED_to_orig(f0));
    if (f0 == NULL)
	return false;
    if (ED_conc_opp_flag(e0))
	return false;
    if (ED_conc_opp_flag(f0))
	return false;
    return gv_edge_attributes_are_equal(e0, f0) &&
           same_direction_edge_arrow_decorations_are_equal(e0, f0) &&
           ((ND_rank(agtail(f0)) - ND_rank(aghead(f0))) *
                (ND_rank(agtail(e0)) - ND_rank(aghead(e0))) >
            0);
}

static bool downcandidate(node_t * v)
{
    return ND_node_type(v) == VIRTUAL && ND_in(v).size == 1
	    && ND_out(v).size == 1 && ND_label(v) == NULL;
}

static bool bothdowncandidates(node_t * u, node_t * v)
{
    edge_t *e, *f;
    e = ND_in(u).list[0];
    f = ND_in(v).list[0];
    if (downcandidate(v) && agtail(e) == agtail(f)) {
	return samedir(e, f)
	    && portcmp(ED_tail_port(e), ED_tail_port(f)) == 0;
    }
    return false;
}

static bool upcandidate(node_t * v)
{
    return ND_node_type(v) == VIRTUAL && ND_out(v).size == 1
	    && ND_in(v).size == 1 && ND_label(v) == NULL;
}

static bool bothupcandidates(node_t * u, node_t * v)
{
    edge_t *e, *f;
    e = ND_out(u).list[0];
    f = ND_out(v).list[0];
    if (upcandidate(v) && aghead(e) == aghead(f)) {
	return samedir(e, f)
	    && portcmp(ED_head_port(e), ED_head_port(f)) == 0;
    }
    return false;
}

static void mergevirtual_pair(graph_t * g, int r, int lpos, int rpos, int dir)
{
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
		f = virtual_edge(left, aghead(e), e);
	    while ((e0 = ND_in(right).list[0])) {
		merge_oneway(e0, f);
		delete_fast_edge(e0);
	    }
	    delete_fast_edge(e);
	}
    } else {
	while ((e = ND_in(right).list[0])) {
	    int k;
	    for (k = 0; (f = ND_in(left).list[k]); k++)
		if (agtail(f) == agtail(e))
		    break;
	    if (f == NULL)
		f = virtual_edge(agtail(e), left, e);
	    while ((e0 = ND_out(right).list[0])) {
		merge_oneway(e0, f);
		delete_fast_edge(e0);
	    }
	    delete_fast_edge(e);
	}
    }
    assert(ND_in(right).size + ND_out(right).size == 0);
    delete_fast_node(g, right);

    for (int i = rpos + 1; i < GD_rank(g)[r].n; ++i) {
	node_t *const n = GD_rank(g)[r].v[i - 1] = GD_rank(g)[r].v[i];
	ND_order(n) = i - 1;
    }
    GD_rank(g)[r].n--;
    GD_rank(g)[r].v[GD_rank(g)[r].n] = NULL;
}

static void infuse(graph_t * g, node_t * n)
{
    node_t *lead;

    lead = GD_rankleader(g)[ND_rank(n)];
    if (lead == NULL || ND_order(lead) > ND_order(n))
	GD_rankleader(g)[ND_rank(n)] = n;
}

static int rebuild_vlists(graph_t * g)
{
    int c, i, r, maxi;
    node_t *n, *lead;
    edge_t *rep;

    for (r = GD_minrank(g); r <= GD_maxrank(g); r++)
	GD_rankleader(g)[r] = NULL;
    dot_scan_ranks(g);
    for (n = agfstnode(g); n; n = agnxtnode(g, n)) {
	infuse(g, n);
	for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
	    for (rep = e; ED_to_virt(rep); rep = ED_to_virt(rep));
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
	}
	else if (GD_rank(dot_root(g))[r].v[ND_order(lead)] != lead) {
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
		for (e = ND_in(n).list[0]; e && ED_to_orig(e);
		     e = ED_to_orig(e));
		if (e && agcontains(g, agtail(e))
		    && agcontains(g, aghead(e)))
		    maxi = i;
	    }
	}
	if (maxi == -1)
	    agwarningf("degenerate concentrated rank %s,%d\n", agnameof(g),
		  r);
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
           same_direction_edge_arrow_decorations_are_equal(
               representative_edge, edge);
  }

  return edges_run_in_opposite_directions(edge, representative_edge) &&
         gv_opposite_edge_ports_are_equal(edge, representative_edge) &&
         gv_opposite_edge_attributes_are_equal(edge, representative_edge) &&
         opposite_direction_edge_arrow_decorations_are_mergeable(
             representative_edge, edge);
}

static void concentrate_flat_edges(graph_t *graph) {
  /*
   * The virtual-node passes below require an intermediate rank, so they never
   * visit same-rank edges. flat_breakcycles() and class2() have already placed
   * flat duplicates in ND_other(); ED_to_virt() points from each duplicate to
   * its representative. ND_other() contains both same-direction and reversed
   * edges, so select the matching comparison before suppressing anything.
   */
  for (node_t *node = GD_nlist(graph); node != NULL; node = ND_next(node)) {
    if (ND_other(node).list == NULL) {
      continue;
    }

    size_t edge_index = 0;
    while (ND_other(node).list[edge_index] != NULL) {
      edge_t *const edge = ND_other(node).list[edge_index];
      edge_t *const representative_edge = ED_to_virt(edge);

      if (flat_edges_are_equivalent(edge, representative_edge)) {
        const bool opposite_direction =
            edges_run_in_opposite_directions(edge, representative_edge);
        fold_concentrated_edge_arrow_decorations(representative_edge, edge,
                                                 opposite_direction);
        if (opposite_direction) {
          ED_conc_opp_flag(representative_edge) = true;
        }
        zapinlist(&ND_other(node), edge);
        ED_edge_type(edge) = IGNORED;
        continue;
      }
      edge_index++;
    }
  }
}

int dot_concentrate(graph_t *g) {
    int c, r, leftpos, rightpos;
    node_t *left, *right;

    concentrate_flat_edges(g);
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
		right = GD_rank(g)[r].v[rightpos];
		if (bothdowncandidates(left, right)) {
		    mergevirtual_pair(g, r, leftpos, rightpos, DOWN);
		    continue;
		}
		rightpos++;
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
		right = GD_rank(g)[r].v[rightpos];
		if (bothupcandidates(left, right)) {
		    mergevirtual_pair(g, r, leftpos, rightpos, UP);
		    continue;
		}
		rightpos++;
	    }
	}
	r--;
    }
    for (c = 1; c <= GD_n_cluster(g); c++) {
	if (rebuild_vlists(GD_clust(g)[c]) != 0) {
	    agerr(AGPREV, "concentrate=true may not work correctly.\n");
	    return -1;
	}
    }
    return 0;
}
