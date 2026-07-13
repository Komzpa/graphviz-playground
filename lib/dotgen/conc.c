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

#include	<dotgen/dot.h>
#include	<stdbool.h>
#include	<stdint.h>
#include	<string.h>
#include	<util/alloc.h>

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
    return ((ND_rank(agtail(f0)) - ND_rank(aghead(f0)))
	    * (ND_rank(agtail(e0)) - ND_rank(aghead(e0))) > 0);
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

typedef struct {
    node_t *endpoint;
    port endpoint_port;
    edge_t *continuation;
} continuation_slot_t;

typedef struct {
    continuation_slot_t *slots;
    size_t slot_count;
} continuation_index_t;

static node_t *continuation_endpoint(edge_t *edge, int dir)
{
    return dir == DOWN ? aghead(edge) : agtail(edge);
}

static port continuation_port(edge_t *edge, int dir)
{
    return dir == DOWN ? ED_head_port(edge) : ED_tail_port(edge);
}

static bool compatible_continuation(edge_t *candidate, edge_t *edge, int dir)
{
    /* Continuations may share a trunk only when they keep the same endpoint port. */
    return continuation_endpoint(candidate, dir) == continuation_endpoint(edge, dir)
	&& portcmp(continuation_port(candidate, dir),
		   continuation_port(edge, dir)) == 0;
}

static uint64_t hash_double(double value)
{
    uint64_t bits = 0;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static size_t continuation_index_size(graph_t *g, int r, int lpos, int rpos, int dir)
{
    size_t continuations = 0;

    for (int i = lpos; i <= rpos; ++i) {
	node_t *const n = GD_rank(g)[r].v[i];
	continuations += dir == DOWN ? ND_out(n).size : ND_in(n).size;
    }

    size_t slot_count = 8;
    while (slot_count < continuations * 4)
	slot_count *= 2;

    return slot_count;
}

static continuation_index_t new_continuation_index(size_t slot_count)
{
    continuation_index_t index = {
	.slots = gv_calloc(slot_count, sizeof(*index.slots)),
	.slot_count = slot_count,
    };
    return index;
}

static void free_continuation_index(continuation_index_t *index)
{
    free(index->slots);
    index->slots = NULL;
    index->slot_count = 0;
}

static continuation_slot_t *continuation_slot(continuation_index_t *index,
					      node_t *endpoint, port p)
{
    uint64_t hash = (uint64_t)(uintptr_t) endpoint;
    if (p.defined) {
	hash ^= hash_double(p.p.x) + UINT64_C(0x9e3779b97f4a7c15);
	hash ^= (hash_double(p.p.y) << 1) | (hash_double(p.p.y) >> 63);
    }

    const size_t mask = index->slot_count - 1;
    size_t pos = (size_t)hash & mask;

    for (;;) {
	continuation_slot_t *const slot = &index->slots[pos];
	if (slot->continuation == NULL)
	    return slot;
	if (slot->endpoint == endpoint && portcmp(slot->endpoint_port, p) == 0)
	    return slot;
	pos = (pos + 1) & mask;
    }
}

static edge_t *find_continuation(continuation_index_t *index, edge_t *edge, int dir)
{
    continuation_slot_t *const slot =
	continuation_slot(index, continuation_endpoint(edge, dir),
			  continuation_port(edge, dir));
    if (slot->continuation == NULL)
	return NULL;
    return compatible_continuation(slot->continuation, edge, dir)
	       ? slot->continuation
	       : NULL;
}

static void remember_continuation(continuation_index_t *index, edge_t *edge, int dir)
{
    continuation_slot_t *const slot =
	continuation_slot(index, continuation_endpoint(edge, dir),
			  continuation_port(edge, dir));
    if (slot->continuation == NULL) {
	slot->endpoint = continuation_endpoint(edge, dir);
	slot->endpoint_port = continuation_port(edge, dir);
	slot->continuation = edge;
    }
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

static void mergevirtual(graph_t * g, int r, int lpos, int rpos, int dir)
{
    int k;
    node_t *left;
    edge_t *e, *f, *e0;
    continuation_index_t continuations =
	new_continuation_index(continuation_index_size(g, r, lpos, rpos, dir));

    left = GD_rank(g)[r].v[lpos];
    if (dir == DOWN) {
	for (k = 0; (f = ND_out(left).list[k]); ++k)
	    remember_continuation(&continuations, f, dir);
    } else {
	for (k = 0; (f = ND_in(left).list[k]); ++k)
	    remember_continuation(&continuations, f, dir);
    }

    /* merge all right nodes into the leftmost one */
    for (int i = lpos + 1; i <= rpos; i++) {
	node_t *const right = GD_rank(g)[r].v[i];
	if (dir == DOWN) {
	    while ((e = ND_out(right).list[0])) {
		f = find_continuation(&continuations, e, dir);
		if (f == NULL)
		    f = virtual_edge(left, aghead(e), e);
		remember_continuation(&continuations, f, dir);
		while ((e0 = ND_in(right).list[0])) {
		    merge_oneway(e0, f);
		    delete_fast_edge(e0);
		}
		delete_fast_edge(e);
	    }
	} else {
	    while ((e = ND_in(right).list[0])) {
		f = find_continuation(&continuations, e, dir);
		if (f == NULL)
		    f = virtual_edge(agtail(e), left, e);
		remember_continuation(&continuations, f, dir);
		while ((e0 = ND_out(right).list[0])) {
		    merge_oneway(e0, f);
		    delete_fast_edge(e0);
		}
		delete_fast_edge(e);
	    }
	}
	assert(ND_in(right).size + ND_out(right).size == 0);
	delete_fast_node(g, right);
    }
    free_continuation_index(&continuations);
    k = lpos + 1;
    for (int i = rpos + 1; i < GD_rank(g)[r].n; ++i) {
	node_t *const n = GD_rank(g)[r].v[k] = GD_rank(g)[r].v[i];
	ND_order(n) = k;
	k++;
    }
    GD_rank(g)[r].n = k;
    GD_rank(g)[r].v[k] = NULL;
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

int dot_concentrate(graph_t *g) {
    int c, r, leftpos, rightpos;
    node_t *left, *right;

    if (GD_maxrank(g) - GD_minrank(g) <= 1)
	return 0;
    /* this is the downward looking pass. r is a candidate rank. */
    for (r = 1; GD_rank(g)[r + 1].n; r++) {
	for (leftpos = 0; leftpos < GD_rank(g)[r].n; leftpos++) {
	    left = GD_rank(g)[r].v[leftpos];
	    if (!downcandidate(left))
		continue;
	    for (rightpos = leftpos + 1; rightpos < GD_rank(g)[r].n;
		 rightpos++) {
		right = GD_rank(g)[r].v[rightpos];
		if (!bothdowncandidates(left, right))
		    break;
	    }
	    if (rightpos - leftpos > 1)
		mergevirtual(g, r, leftpos, rightpos - 1, DOWN);
	}
    }
    /* this is the corresponding upward pass */
    while (r > 0) {
	for (leftpos = 0; leftpos < GD_rank(g)[r].n; leftpos++) {
	    left = GD_rank(g)[r].v[leftpos];
	    if (!upcandidate(left))
		continue;
	    for (rightpos = leftpos + 1; rightpos < GD_rank(g)[r].n;
		 rightpos++) {
		right = GD_rank(g)[r].v[rightpos];
		if (!bothupcandidates(left, right))
		    break;
	    }
	    if (rightpos - leftpos > 1)
		mergevirtual(g, r, leftpos, rightpos - 1, UP);
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
