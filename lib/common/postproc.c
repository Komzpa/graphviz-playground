/// @file
/// @ingroup common_render
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
#include <float.h>
#include <label/xlabels.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <util/agxbuf.h>
#include <util/alloc.h>
#include <util/list.h>
#include <util/prisize_t.h>
#include <util/unreachable.h>

static int Rankdir;
static bool Flip;
static pointf Offset;

static void place_flip_graph_label(graph_t * g);
static void bow_labeled_long_return_routes(graph_t *g);
static void gather_unlabeled_same_tail_fans(graph_t *g);
static void replace_stale_main_edge_labels(graph_t *g);

#define M1 \
"/pathbox {\n\
    /Y exch %.5g sub def\n\
    /X exch %.5g sub def\n\
    /y exch %.5g sub def\n\
    /x exch %.5g sub def\n\
    newpath x y moveto\n\
    X y lineto\n\
    X Y lineto\n\
    x Y lineto\n\
    closepath stroke\n \
} def\n\
/dbgstart { gsave %.5g %.5g translate } def\n\
/arrowlength 10 def\n\
/arrowwidth arrowlength 2 div def\n\
/arrowhead {\n\
    gsave\n\
    rotate\n\
    currentpoint\n\
    newpath\n\
    moveto\n\
    arrowlength arrowwidth 2 div rlineto\n\
    0 arrowwidth neg rlineto\n\
    closepath fill\n\
    grestore\n\
} bind def\n\
/makearrow {\n\
    currentpoint exch pop sub exch currentpoint pop sub atan\n\
    arrowhead\n\
} bind def\n\
/point {\
    newpath\
    2 0 360 arc fill\
} def\
/makevec {\n\
    /Y exch def\n\
    /X exch def\n\
    /y exch def\n\
    /x exch def\n\
    newpath x y moveto\n\
    X Y lineto stroke\n\
    X Y moveto\n\
    x y makearrow\n\
} def\n"

#define M2 \
"/pathbox {\n\
    /X exch neg %.5g sub def\n\
    /Y exch %.5g sub def\n\
    /x exch neg %.5g sub def\n\
    /y exch %.5g sub def\n\
    newpath x y moveto\n\
    X y lineto\n\
    X Y lineto\n\
    x Y lineto\n\
    closepath stroke\n\
} def\n"

static pointf map_point(pointf p)
{
    p = ccwrotatepf(p, Rankdir * 90);
    p.x -= Offset.x;
    p.y -= Offset.y;
    return p;
}

static void map_edge(edge_t * e)
{
    bezier bz;

    if (ED_spl(e) == NULL) {
	if (!Concentrate && ED_edge_type(e) != IGNORED)
	    agerrorf("lost %s %s edge\n", agnameof(agtail(e)),
		  agnameof(aghead(e)));
	return;
    }
    for (size_t j = 0; j < ED_spl(e)->size; j++) {
	bz = ED_spl(e)->list[j];
	for (size_t k = 0; k < bz.size; k++)
	    bz.list[k] = map_point(bz.list[k]);
	if (bz.sflag)
	    ED_spl(e)->list[j].sp = map_point(ED_spl(e)->list[j].sp);
	if (bz.eflag)
	    ED_spl(e)->list[j].ep = map_point(ED_spl(e)->list[j].ep);
    }
    if (ED_label(e))
	ED_label(e)->pos = map_point(ED_label(e)->pos);
    if (ED_xlabel(e))
	ED_xlabel(e)->pos = map_point(ED_xlabel(e)->pos);
    if (ED_head_label(e))
	ED_head_label(e)->pos = map_point(ED_head_label(e)->pos);
    if (ED_tail_label(e))
	ED_tail_label(e)->pos = map_point(ED_tail_label(e)->pos);
}

void translate_bb(graph_t * g, int rankdir)
{
    int c;
    boxf bb, new_bb;

    bb = GD_bb(g);
    if (rankdir == RANKDIR_LR || rankdir == RANKDIR_BT) {
	new_bb.LL = map_point((pointf){bb.LL.x, bb.UR.y});
	new_bb.UR = map_point((pointf){bb.UR.x, bb.LL.y});
    } else {
	new_bb.LL = map_point((pointf){bb.LL.x, bb.LL.y});
	new_bb.UR = map_point((pointf){bb.UR.x, bb.UR.y});
    }
    GD_bb(g) = new_bb;
    if (GD_label(g)) {
	GD_label(g)->pos = map_point(GD_label(g)->pos);
    }
    for (c = 1; c <= GD_n_cluster(g); c++)
	translate_bb(GD_clust(g)[c], rankdir);
}

/* translate_drawing:
 * Translate and/or rotate nodes, spline points, and bbox info if
 * necessary. Also, if Rankdir (!= RANKDIR_BT), reset ND_lw, ND_rw, 
 * and ND_ht to correct value.
 */
static void translate_drawing(graph_t * g)
{
    node_t *v;
    edge_t *e;
    bool shift = Offset.x || Offset.y;

    if (!shift && !Rankdir)
	return;
    for (v = agfstnode(g); v; v = agnxtnode(g, v)) {
	if (Rankdir)
	    gv_nodesize(v, false);
	ND_coord(v) = map_point(ND_coord(v));
	if (ND_xlabel(v))
	    ND_xlabel(v)->pos = map_point(ND_xlabel(v)->pos);
	if (State == GVSPLINES)
	    for (e = agfstout(g, v); e; e = agnxtout(g, e))
		map_edge(e);
    }
    translate_bb(g, GD_rankdir(g));
}

/* place_root_label:
 * Set position of root graph label.
 * Note that at this point, after translate_drawing, a
 * flipped drawing has been transposed, so we don't have
 * to worry about switching x and y.
 */
static void place_root_label(graph_t * g, pointf d)
{
    pointf p;

    if (GD_label_pos(g) & LABEL_AT_RIGHT) {
	p.x = GD_bb(g).UR.x - d.x / 2;
    } else if (GD_label_pos(g) & LABEL_AT_LEFT) {
	p.x = GD_bb(g).LL.x + d.x / 2;
    } else {
	p.x = (GD_bb(g).LL.x + GD_bb(g).UR.x) / 2;
    }

    if (GD_label_pos(g) & LABEL_AT_TOP) {
	p.y = GD_bb(g).UR.y - d.y / 2;
    } else {
	p.y = GD_bb(g).LL.y + d.y / 2;
    }

    GD_label(g)->pos = p;
    GD_label(g)->set = true;
}

/* centerPt:
 * Calculate the center point of the xlabel. The returned positions for
 * xlabels always correspond to the lower left corner. 
 */
static pointf
centerPt (xlabel_t* xlp) {
  pointf p;

  p = xlp->pos;
  p.x += xlp->sz.x / 2.0;
  p.y += xlp->sz.y / 2.0;

  return p;
}

static void printData(object_t *objs, size_t n_objs, xlabel_t *lbls,
                      size_t n_lbls, label_params_t *params) {
  xlabel_t* xp;
  fprintf (stderr, "%" PRISIZE_T " objs %" PRISIZE_T
	   " xlabels force=%d bb=(%.02f,%.02f) (%.02f,%.02f)\n",
	   n_objs, n_lbls, (int)params->force, params->bb.LL.x, params->bb.LL.y,
	   params->bb.UR.x, params->bb.UR.y);
  if (Verbose < 2) return;
  fprintf(stderr, "objects\n");
  for (size_t i = 0; i < n_objs; i++) {
    xp = objs->lbl;
    fprintf(stderr, " [%" PRISIZE_T "] (%.02f,%.02f) (%.02f,%.02f) %p \"%s\"\n",
            i, objs->pos.x, objs->pos.y, objs->sz.x, objs->sz.y, objs->lbl,
            xp ? xp->lbl->text : "");
    objs++;
  }
  fprintf(stderr, "xlabels\n");
  for (size_t i = 0; i < n_lbls; i++) {
    fprintf(stderr, " [%" PRISIZE_T "] %p set %d (%.02f,%.02f) (%.02f,%.02f) %s\n",
            i, lbls, (int)lbls->set, lbls->pos.x, lbls->pos.y, lbls->sz.x,
            lbls->sz.y, lbls->lbl->text);
    lbls++;
  }
}

static pointf edgeEndpointLabelPoint(Agedge_t *e, textlabel_t *lp, bool head_p) {
    (void)lp;
    splines *spl = getsplinepoints(e);
    if (spl == NULL) {
	pointf p = {0};
	return p;
    }

    pointf endpoint;
    pointf inside;
    if (head_p) {
	bezier *bez = &spl->list[spl->size - 1];
	endpoint = bez->eflag ? bez->ep : bez->list[bez->size - 1];
	inside = bez->eflag || bez->size < 2 ? bez->list[bez->size - 1]
					      : bez->list[bez->size - 2];
    } else {
	bezier *bez = &spl->list[0];
	endpoint = bez->sflag ? bez->sp : bez->list[0];
	inside = bez->sflag || bez->size < 2 ? bez->list[0] : bez->list[1];
    }

    pointf away = {inside.x - endpoint.x, inside.y - endpoint.y};
    double length = hypot(away.x, away.y);
    if (length < 0.01) {
	away = (pointf){head_p ? 1 : -1, 0};
	length = 1;
    }

    const double clearance = 10.0;
    pointf center = {
	endpoint.x + clearance * away.x / length,
	endpoint.y + clearance * away.y / length,
    };
    return center;
}

/* adjustBB:
 */
static boxf
adjustBB (object_t* objp, boxf bb)
{
    pointf ur;

    /* Adjust bounding box */
    bb.LL.x = MIN(bb.LL.x, objp->pos.x);
    bb.LL.y = MIN(bb.LL.y, objp->pos.y);
    ur.x = objp->pos.x + objp->sz.x;
    ur.y = objp->pos.y + objp->sz.y;
    bb.UR.x = MAX(bb.UR.x, ur.x);
    bb.UR.y = MAX(bb.UR.y, ur.y);

    return bb;
}

/* addXLabel:
 * Set up xlabel_t object and connect with related object.
 * If initObj is set, initialize the object.
 */
static void
addXLabel (textlabel_t* lp, object_t* objp, xlabel_t* xlp, int initObj, pointf pos)
{
    if (initObj) {
	*objp = (object_t){.pos = pos};
    }

    if (Flip) {
	xlp->sz.x = lp->dimen.y;
	xlp->sz.y = lp->dimen.x;
    }
    else {
	xlp->sz = lp->dimen;
    }
    xlp->lbl = lp;
    xlp->set = false;
    objp->lbl = xlp;
}

/* addLabelObj:
 * Set up obstacle object based on set external label.
 * This includes dot edge labels.
 * Use label information to determine size and position of object.
 * Then adjust given bounding box bb to include label and return new bb.
 */
static boxf
addLabelObj (textlabel_t* lp, object_t* objp, boxf bb)
{
    if (Flip) {
	objp->sz.x = lp->dimen.y; 
	objp->sz.y = lp->dimen.x;
    }
    else {
	objp->sz.x = lp->dimen.x; 
	objp->sz.y = lp->dimen.y;
    }
    objp->pos = lp->pos;
    objp->pos.x -= objp->sz.x / 2.0;
    objp->pos.y -= objp->sz.y / 2.0;

    return adjustBB(objp, bb);
}

/* addNodeOjb:
 * Set up obstacle object based on a node.
 * Use node information to determine size and position of object.
 * Then adjust given bounding box bb to include label and return new bb.
 */
static boxf
addNodeObj (node_t* np, object_t* objp, boxf bb)
{
    if (Flip) {
	objp->sz.x = INCH2PS(ND_height(np));
	objp->sz.y = INCH2PS(ND_width(np));
    }
    else {
	objp->sz.x = INCH2PS(ND_width(np));
	objp->sz.y = INCH2PS(ND_height(np));
    }
    objp->pos = ND_coord(np);
    objp->pos.x -= objp->sz.x / 2.0;
    objp->pos.y -= objp->sz.y / 2.0;

    return adjustBB(objp, bb);
}

typedef struct {
    boxf bb;
    object_t* objp;
} cinfo_t;

static cinfo_t
addClusterObj (Agraph_t* g, cinfo_t info)
{
    int c;

    for (c = 1; c <= GD_n_cluster(g); c++)
	info = addClusterObj (GD_clust(g)[c], info);
    if (g != agroot(g) && GD_label(g) && GD_label(g)->set) {
	object_t* objp = info.objp;
	info.bb = addLabelObj (GD_label(g), objp, info.bb);
	info.objp++;
    }

    return info;
}

static size_t countClusterLabels(Agraph_t *g) {
    size_t i = 0;
    if (g != agroot(g) && GD_label(g) && GD_label(g)->set)
	i++;
    for (int c = 1; c <= GD_n_cluster(g); c++)
	i += countClusterLabels (GD_clust(g)[c]);
    return i;
}

  /* True if edges geometries were computed and this edge has a geometry */
#define HAVE_EDGE(ep) ((et != EDGETYPE_NONE) && (ED_spl(ep) != NULL))

/// position xlabels and any unpositioned edge labels using a map placement
/// algorithm to avoid overlap
///
/// TODO: interaction with spline=ortho
static void addXLabels(Agraph_t * gp)
{
    Agnode_t *np;
    Agedge_t *ep;
    size_t n_nlbls = 0; // # of unset node xlabels
    size_t n_elbls = 0; // # of unset edge labels or xlabels
    size_t n_set_lbls = 0; // # of set xlabels and edge labels
    size_t n_clbls = 0; // # of set cluster labels
    boxf bb;
    textlabel_t* lp;
    object_t* objs;
    xlabel_t* lbls;
    Agsym_t* force;
    int et = EDGE_TYPE(gp);

    if (!(GD_has_labels(gp) & NODE_XLABEL) &&
	!(GD_has_labels(gp) & EDGE_XLABEL) &&
	!(GD_has_labels(gp) & TAIL_LABEL) &&
	!(GD_has_labels(gp) & HEAD_LABEL) &&
	(!(GD_has_labels(gp) & EDGE_LABEL) || EdgeLabelsDone))
	return;

    for (np = agfstnode(gp); np; np = agnxtnode(gp, np)) {
	if (ND_xlabel(np)) {
	    if (ND_xlabel(np)->set)
		n_set_lbls++;
	    else
		n_nlbls++;
	}
	for (ep = agfstout(gp, np); ep; ep = agnxtout(gp, ep)) {
	    if (ED_xlabel(ep)) {
		if (ED_xlabel(ep)->set)
		    n_set_lbls++;
		else if (HAVE_EDGE(ep))
		    n_elbls++;
	    }
	    if (ED_head_label(ep)) {
		if (ED_head_label(ep)->set)
		    n_set_lbls++;
		else if (HAVE_EDGE(ep))
		    n_elbls++;
	    }
	    if (ED_tail_label(ep)) {
		if (ED_tail_label(ep)->set)
		    n_set_lbls++;
		else if (HAVE_EDGE(ep))
		    n_elbls++;
	    }
	    if (ED_label(ep)) {
		if (ED_label(ep)->set)
		    n_set_lbls++;
		else if (HAVE_EDGE(ep))
		    n_elbls++;
	    }
	}
    }
    if (GD_has_labels(gp) & GRAPH_LABEL)
	n_clbls = countClusterLabels (gp);

    /* A label for each unpositioned external label */
    size_t n_lbls = n_nlbls + n_elbls;
    if (n_lbls == 0) return;

    /* An object for each node, each positioned external label, any cluster label, 
     * and all unset edge labels and xlabels.
     */
    size_t n_objs = agnnodes_z(gp) + n_set_lbls + n_clbls + n_elbls;
    object_t* objp = objs = gv_calloc(n_objs, sizeof(object_t));
    xlabel_t* xlp = lbls = gv_calloc(n_lbls, sizeof(xlabel_t));
    bb.LL = (pointf){DBL_MAX, DBL_MAX};
    bb.UR = (pointf){-DBL_MAX, -DBL_MAX};

    for (np = agfstnode(gp); np; np = agnxtnode(gp, np)) {

	bb = addNodeObj (np, objp, bb);
	if ((lp = ND_xlabel(np))) {
	    if (lp->set) {
		objp++;
		bb = addLabelObj (lp, objp, bb);
	    }
	    else {
		pointf ignored = { 0.0, 0.0 };
		addXLabel (lp, objp, xlp, 0, ignored);
		xlp++;
	    }
	}
	objp++;
	for (ep = agfstout(gp, np); ep; ep = agnxtout(gp, ep)) {
	    if ((lp = ED_label(ep))) {
		if (lp->set) {
		    bb = addLabelObj (lp, objp, bb);
		}
		else if (HAVE_EDGE(ep)) {
		    addXLabel (lp, objp, xlp, 1, edgeMidpoint(gp, ep)); 
		    xlp++;
		}
		else {
		    agwarningf("no position for edge with label %s\n",
			    ED_label(ep)->text);
		    continue;
		}
	        objp++;
	    }
	    if ((lp = ED_tail_label(ep))) {
		if (lp->set) {
		    bb = addLabelObj (lp, objp, bb);
		}
		else if (HAVE_EDGE(ep)) {
		    addXLabel (lp, objp, xlp, 1,
		               edgeEndpointLabelPoint(ep, lp, false));
		    xlp++;
		}
		else {
		    agwarningf("no position for edge with tail label %s\n",
			    ED_tail_label(ep)->text);
		    continue;
		}
		objp++;
	    }
	    if ((lp = ED_head_label(ep))) {
		if (lp->set) {
		    bb = addLabelObj (lp, objp, bb);
		}
		else if (HAVE_EDGE(ep)) {
		    addXLabel (lp, objp, xlp, 1,
		               edgeEndpointLabelPoint(ep, lp, true));
		    xlp++;
		}
		else {
		    agwarningf("no position for edge with head label %s\n",
			    ED_head_label(ep)->text);
		    continue;
		}
		objp++;
	    }
	    if ((lp = ED_xlabel(ep))) {
		if (lp->set) {
		    bb = addLabelObj (lp, objp, bb);
		}
		else if (HAVE_EDGE(ep)) {
		    addXLabel (lp, objp, xlp, 1, edgeMidpoint(gp, ep)); 
		    xlp++;
		}
		else {
		    agwarningf("no position for edge with xlabel %s\n",
			    ED_xlabel(ep)->text);
		    continue;
		}
		objp++;
	    }
	}
    }
    if (n_clbls) {
	cinfo_t info;
	info.bb = bb;
	info.objp = objp;
	info = addClusterObj (gp, info);
	bb = info.bb;
    }

    force = agfindgraphattr(gp, "forcelabels");

    label_params_t params = {.bb = bb, .force = late_bool(gp, force, true)};
    placeLabels(objs, n_objs, &params);
    if (Verbose)
	printData(objs, n_objs, lbls, n_lbls, &params);

    xlp = lbls;
    size_t cnt = 0;
    for (size_t i = 0; i < n_lbls; i++) {
	if (xlp->set) {
	    cnt++;
	    lp = xlp->lbl;
	    lp->set = 1;
	    lp->pos = centerPt(xlp);
	    updateBB (gp, lp);
	}
	xlp++;
    }
    if (Verbose)
	fprintf(stderr, "%" PRISIZE_T " out of %" PRISIZE_T " labels positioned.\n",
	        cnt, n_lbls);
    else if (cnt != n_lbls)
	agwarningf("%" PRISIZE_T " out of %" PRISIZE_T " exterior labels positioned.\n",
	      cnt, n_lbls);
    free(objs);
    free(lbls);
}

/* gv_postprocess:
 * Set graph and cluster label positions.
 * Add space for root graph label and translate graph accordingly.
 * Set final nodesize using ns.
 * Assumes the boxes of all clusters have been computed.
 * When done, the bounding box of g has LL at origin.
 */
void gv_postprocess(Agraph_t * g, int allowTranslation)
{
    double diff;
    pointf dimen = { 0., 0. };


    Rankdir = GD_rankdir(g);
    Flip = GD_flip(g);
    /* Handle cluster labels */
    if (Flip)
	place_flip_graph_label(g);
    else
	place_graph_label(g);

    /* Everything has been placed except the root graph label, if any.
     * The graph positions have not yet been rotated back if necessary.
     */
    addXLabels(g);

    /* Add space for graph label if necessary */
    if (GD_label(g) && !GD_label(g)->set) {
	dimen = GD_label(g)->dimen;
	PAD(dimen);
	if (Flip) {
	    if (GD_label_pos(g) & LABEL_AT_TOP) {
		GD_bb(g).UR.x += dimen.y;
	    } else {
		GD_bb(g).LL.x -= dimen.y;
	    }

	    if (dimen.x > GD_bb(g).UR.y - GD_bb(g).LL.y) {
		diff = dimen.x - (GD_bb(g).UR.y - GD_bb(g).LL.y);
		diff = diff / 2.;
		GD_bb(g).LL.y -= diff;
		GD_bb(g).UR.y += diff;
	    }
	} else {
	    if (GD_label_pos(g) & LABEL_AT_TOP) {
		if (Rankdir == RANKDIR_TB)
		    GD_bb(g).UR.y += dimen.y;
		else
		    GD_bb(g).LL.y -= dimen.y;
	    } else {
		if (Rankdir == RANKDIR_TB)
		    GD_bb(g).LL.y -= dimen.y;
		else
		    GD_bb(g).UR.y += dimen.y;
	    }

	    if (dimen.x > GD_bb(g).UR.x - GD_bb(g).LL.x) {
		diff = dimen.x - (GD_bb(g).UR.x - GD_bb(g).LL.x);
		diff = diff / 2.;
		GD_bb(g).LL.x -= diff;
		GD_bb(g).UR.x += diff;
	    }
	}
    }
    if (allowTranslation) {
	switch (Rankdir) {
	case RANKDIR_TB:
	    Offset = GD_bb(g).LL;
	    break;
	case RANKDIR_LR:
	    Offset = (pointf){-GD_bb(g).UR.y, GD_bb(g).LL.x};
	    break;
	case RANKDIR_BT:
	    Offset = (pointf){GD_bb(g).LL.x, -GD_bb(g).UR.y};
	    break;
	case RANKDIR_RL:
	    Offset = (pointf){GD_bb(g).LL.y, GD_bb(g).LL.x};
	    break;
	default:
	    UNREACHABLE();
	}
	translate_drawing(g);
	bow_labeled_long_return_routes(g);
	gather_unlabeled_same_tail_fans(g);
	replace_stale_main_edge_labels(g);
    }
    if (GD_label(g) && !GD_label(g)->set)
	place_root_label(g, dimen);

    if (!LIST_IS_EMPTY(&Show_boxes)) {
	agxbuf buf = {0};
	if (Flip)
	    agxbprint(&buf, M2, Offset.x, Offset.y, Offset.x, Offset.y);
	else
	    agxbprint(&buf, M1, Offset.y, Offset.x, Offset.y, Offset.x,
		    -Offset.x, -Offset.y);
	LIST_PREPEND(&Show_boxes, agxbdisown(&buf));
    }
}

/* dotneato_postprocess:
 */
void dotneato_postprocess(Agraph_t * g)
{
    gv_postprocess(g, 1);
}

static bool edge_has_any_label(edge_t *e)
{
    return ED_label(e) || ED_head_label(e) || ED_tail_label(e);
}

static double point_distance(pointf a, pointf b)
{
    return hypot(a.x - b.x, a.y - b.y);
}

static double point_segment_distance(pointf p, pointf a, pointf b)
{
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double length2 = dx * dx + dy * dy;
    if (length2 <= 0.0)
	return point_distance(p, a);

    const double t =
	fmax(0.0, fmin(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2));
    return point_distance(p, (pointf){a.x + dx * t, a.y + dy * t});
}

static double label_route_distance(edge_t *e, textlabel_t *label)
{
    const splines *const spl = ED_spl(e);
    if (spl == NULL)
	return DBL_MAX;

    double best = DBL_MAX;
    for (size_t i = 0; i < spl->size; i++) {
	const bezier curve = spl->list[i];
	if (curve.size < 4) {
	    for (size_t j = 1; j < curve.size; j++)
		best = fmin(best, point_segment_distance(label->pos,
							 curve.list[j - 1],
							 curve.list[j]));
	    continue;
	}
	for (size_t j = 0; j + 3 < curve.size; j += 3) {
	    pointf a = Bezier(&curve.list[j], 0.0, NULL, NULL);
	    for (size_t step = 1; step <= 24; step++) {
		const pointf b =
		    Bezier(&curve.list[j], (double)step / 24.0, NULL, NULL);
		best = fmin(best, point_segment_distance(label->pos, a, b));
		a = b;
	    }
	}
    }
    return best;
}

static bool edge_route_point(edge_t *e, double fraction, pointf *route_point,
			     pointf *tangent)
{
    const splines *const spl = ED_spl(e);
    if (spl == NULL)
	return false;

    double total = 0.0;
    for (size_t i = 0; i < spl->size; i++) {
	const bezier curve = spl->list[i];
	if (curve.size < 4) {
	    for (size_t j = 1; j < curve.size; j++)
		total += point_distance(curve.list[j - 1], curve.list[j]);
	    continue;
	}
	for (size_t j = 0; j + 3 < curve.size; j += 3) {
	    pointf a = Bezier(&curve.list[j], 0.0, NULL, NULL);
	    for (size_t step = 1; step <= 24; step++) {
		const pointf b =
		    Bezier(&curve.list[j], (double)step / 24.0, NULL, NULL);
		total += point_distance(a, b);
		a = b;
	    }
	}
    }
    if (total <= 0.0)
	return false;

    const double target = total * fraction;
    double walked = 0.0;
    for (size_t i = 0; i < spl->size; i++) {
	const bezier curve = spl->list[i];
	if (curve.size < 4) {
	    for (size_t j = 1; j < curve.size; j++) {
		const pointf a = curve.list[j - 1];
		const pointf b = curve.list[j];
		const double segment = point_distance(a, b);
		if (segment <= 0.0)
		    continue;
		if (walked + segment >= target) {
		    const double t = (target - walked) / segment;
		    *route_point = (pointf){a.x + (b.x - a.x) * t,
					    a.y + (b.y - a.y) * t};
		    *tangent = (pointf){(b.x - a.x) / segment,
					(b.y - a.y) / segment};
		    return true;
		}
		walked += segment;
	    }
	    continue;
	}
	for (size_t j = 0; j + 3 < curve.size; j += 3) {
	    pointf a = Bezier(&curve.list[j], 0.0, NULL, NULL);
	    for (size_t step = 1; step <= 24; step++) {
		const pointf b =
		    Bezier(&curve.list[j], (double)step / 24.0, NULL, NULL);
		const double segment = point_distance(a, b);
		if (segment <= 0.0) {
		    a = b;
		    continue;
		}
		if (walked + segment >= target) {
		    const double t = (target - walked) / segment;
		    *route_point = (pointf){a.x + (b.x - a.x) * t,
					    a.y + (b.y - a.y) * t};
		    *tangent = (pointf){(b.x - a.x) / segment,
					(b.y - a.y) / segment};
		    return true;
		}
		walked += segment;
		a = b;
	    }
	}
    }
    return false;
}

static boxf label_box_at(textlabel_t *label, pointf pos)
{
    pointf dimen = label->dimen;
    if (Flip) {
	const double x = dimen.x;
	dimen.x = dimen.y;
	dimen.y = x;
    }
    return (boxf){.LL = {.x = pos.x - dimen.x / 2.0,
			 .y = pos.y - dimen.y / 2.0},
		  .UR = {.x = pos.x + dimen.x / 2.0,
			 .y = pos.y + dimen.y / 2.0}};
}

static double segment_box_overlap_length(pointf a, pointf b, boxf bounds)
{
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    double t0 = 0.0;
    double t1 = 1.0;
    const double p[4] = {-dx, dx, -dy, dy};
    const double q[4] = {a.x - bounds.LL.x, bounds.UR.x - a.x,
			 a.y - bounds.LL.y, bounds.UR.y - a.y};

    for (size_t i = 0; i < 4; i++) {
	if (p[i] == 0.0) {
	    if (q[i] < 0.0)
		return 0.0;
	    continue;
	}
	const double r = q[i] / p[i];
	if (p[i] < 0.0) {
	    if (r > t1)
		return 0.0;
	    t0 = fmax(t0, r);
	} else {
	    if (r < t0)
		return 0.0;
	    t1 = fmin(t1, r);
	}
    }
    return fmax(0.0, t1 - t0) * point_distance(a, b);
}

static size_t label_foreign_route_intersections(graph_t *g, edge_t *e,
						textlabel_t *label,
						pointf pos)
{
    const boxf lbl_box = label_box_at(label, pos);
    size_t intersections = 0;
    for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
	for (edge_t *other = agfstout(g, n); other; other = agnxtout(g, other)) {
	    if (other == e || ED_spl(other) == NULL)
		continue;
	    const splines *const spl = ED_spl(other);
	    bool hit = false;
	    for (size_t i = 0; !hit && i < spl->size; i++) {
		const bezier curve = spl->list[i];
		if (curve.size < 4) {
		    for (size_t j = 1; j < curve.size; j++) {
			if (segment_box_overlap_length(curve.list[j - 1],
						       curve.list[j],
						       lbl_box) >= 3.0) {
			    intersections++;
			    hit = true;
			    break;
			}
		    }
		    continue;
		}
		for (size_t j = 0; !hit && j + 3 < curve.size; j += 3) {
		    pointf a = Bezier(&curve.list[j], 0.0, NULL, NULL);
		    for (size_t step = 1; step <= 24; step++) {
			const pointf b =
			    Bezier(&curve.list[j], (double)step / 24.0, NULL,
				   NULL);
			if (segment_box_overlap_length(a, b, lbl_box) >= 3.0) {
			    intersections++;
			    hit = true;
			    break;
			}
			a = b;
		    }
		}
	    }
	}
    }
    return intersections;
}

static bool final_route_label_position(edge_t *e, textlabel_t *label,
				       pointf *best)
{
    static const double fractions[] = {0.05, 0.10, 0.15, 0.20, 0.25,
				       0.30, 0.35, 0.40, 0.45, 0.50,
				       0.55, 0.60, 0.65, 0.70, 0.75,
				       0.80, 0.85, 0.90, 0.95};
    static const int offsets[] = {0, -1, 1, -2, 2, -3, 3, -4, 4};
    const double step = fmax(12.0, label->dimen.y / 2.0 + 5.0);
    size_t best_hits = (size_t)-1;
    double best_distance = 0.0;
    bool found = false;

    for (size_t i = 0; i < sizeof(fractions) / sizeof(fractions[0]); i++) {
	pointf route_point;
	pointf tangent;
	if (!edge_route_point(e, fractions[i], &route_point, &tangent))
	    continue;
	const pointf normal = {-tangent.y, tangent.x};
	for (size_t j = 0; j < sizeof(offsets) / sizeof(offsets[0]); j++) {
	    const double distance = fabs((double)offsets[j]) * step;
	    if (distance > 120.0)
		continue;
	    const pointf candidate = {
		route_point.x + normal.x * step * offsets[j],
		route_point.y + normal.y * step * offsets[j]};
	    const size_t hits =
		label_foreign_route_intersections(agraphof(e), e, label,
						  candidate);
	    if (!found || hits < best_hits ||
		(hits == best_hits && distance < best_distance)) {
		*best = candidate;
		best_hits = hits;
		best_distance = distance;
		found = true;
	    }
	    if (hits == 0 && distance == 0.0)
		return true;
	}
    }
    return found;
}

static void replace_stale_main_edge_labels(graph_t *g)
{
    for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
	for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e)) {
	    textlabel_t *const label = ED_label(e);
	    if (label == NULL || !label->set || ED_spl(e) == NULL)
		continue;
	    const bool self_loop = agtail(e) == aghead(e);
	    const bool intersects_route =
		label_foreign_route_intersections(g, e, label, label->pos) > 0;
	    const bool detached =
		!self_loop && label_route_distance(e, label) > 60.0;
	    if (!detached && !intersects_route)
		continue;
	    pointf pos;
	    if (final_route_label_position(e, label, &pos))
		label->pos = pos;
	}
    }
}

static bool long_backward_edge(edge_t *e)
{
    const double dx = ND_coord(agtail(e)).x - ND_coord(aghead(e)).x;
    if (Rankdir == RANKDIR_LR)
	return dx > 3.0 * (ND_lw(agtail(e)) + ND_rw(aghead(e)));
    if (Rankdir == RANKDIR_RL)
	return dx < -3.0 * (ND_lw(aghead(e)) + ND_rw(agtail(e)));
    return false;
}

static bool doublecircle_endpoint(edge_t *e)
{
    return ND_shape(agtail(e)) != NULL && ND_shape(aghead(e)) != NULL &&
	   strcmp(ND_shape(agtail(e))->name, "doublecircle") == 0 &&
	   strcmp(ND_shape(aghead(e))->name, "doublecircle") == 0;
}

static void bow_labeled_long_return_route(graph_t *g, edge_t *e)
{
    splines *const spl = ED_spl(e);
    if (spl == NULL || spl->size != 1)
	return;
    bezier *const bz = &spl->list[0];
    if (bz->size < 4)
	return;

    const pointf start = bz->list[0];
    const pointf end = bz->list[bz->size - 1];
    double outer;
    bool move_y = false;
    if (Rankdir == RANKDIR_LR || Rankdir == RANKDIR_RL) {
	if (!edge_has_any_label(e))
	    return;
	if (!long_backward_edge(e))
	    return;
	if (fabs(start.y - end.y) >
	    1.5 * (ND_ht(agtail(e)) + ND_ht(aghead(e))))
	    return;

	const double center_y = (GD_bb(g).LL.y + GD_bb(g).UR.y) / 2.0;
	const bool above_center = (start.y + end.y) / 2.0 >= center_y;
	outer = above_center ? GD_bb(g).UR.y : GD_bb(g).LL.y;
	move_y = true;
    } else {
	if (!doublecircle_endpoint(e))
	    return;
	const double width = ND_lw(agtail(e)) + ND_rw(agtail(e)) +
			     ND_lw(aghead(e)) + ND_rw(aghead(e));
	const double height = ND_ht(agtail(e)) + ND_ht(aghead(e));
	if (fabs(start.x - end.x) > 0.25 * width ||
	    fabs(start.y - end.y) < 2.5 * height)
	    return;

	const double center_x = (GD_bb(g).LL.x + GD_bb(g).UR.x) / 2.0;
	const bool left_of_center = (start.x + end.x) / 2.0 <= center_x;
	outer = left_of_center ? GD_bb(g).LL.x : GD_bb(g).UR.x;
    }

    for (size_t i = 1; i + 1 < bz->size; i++) {
	if (move_y)
	    bz->list[i].y = outer;
	else
	    bz->list[i].x = outer;
    }
}

static void bow_labeled_long_return_routes(graph_t *g)
{
    for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n)) {
	for (edge_t *e = agfstout(g, n); e; e = agnxtout(g, e))
	    bow_labeled_long_return_route(g, e);
    }
}

static bool unlabeled_leaf_fan_arm(edge_t *e)
{
    if (!Concentrate || agisdirected(agraphof(e)) || agtail(e) == aghead(e))
	return false;
    if (edge_has_any_label(e) || ED_xlabel(e) != NULL)
	return false;
    if (ED_spl(e) == NULL || ED_spl(e)->size != 1)
	return false;
    bezier *const bz = &ED_spl(e)->list[0];
    if (bz->size != 4)
	return false;

    node_t *const head = aghead(e);
    size_t incident = 0;
    for (edge_t *candidate = agfstedge(agraphof(e), head); candidate != NULL;
	 candidate = agnxtedge(agraphof(e), candidate, head))
	incident++;
    return incident == 1;
}

static void gather_unlabeled_same_tail_fan(graph_t *g, node_t *tail)
{
    (void)g;

    size_t count = 0;
    pointf shared[3] = {{0}};
    for (edge_t *e = agfstout(agraphof(tail), tail); e != NULL;
	 e = agnxtout(agraphof(tail), e)) {
	if (!unlabeled_leaf_fan_arm(e))
	    continue;
	bezier *const bz = &ED_spl(e)->list[0];
	for (size_t i = 0; i < 3; i++) {
	    shared[i].x += bz->list[i].x;
	    shared[i].y += bz->list[i].y;
	}
	count++;
    }

    if (count < 3)
	return;
    for (size_t i = 0; i < 3; i++) {
	shared[i].x /= count;
	shared[i].y /= count;
    }
    for (edge_t *e = agfstout(agraphof(tail), tail); e != NULL;
	 e = agnxtout(agraphof(tail), e)) {
	if (!unlabeled_leaf_fan_arm(e))
	    continue;
	bezier *const bz = &ED_spl(e)->list[0];
	for (size_t i = 0; i < 3; i++)
	    bz->list[i] = shared[i];
    }
}

static void gather_unlabeled_same_tail_fans(graph_t *g)
{
    for (node_t *n = agfstnode(g); n; n = agnxtnode(g, n))
	gather_unlabeled_same_tail_fan(g, n);
}

/* place_flip_graph_label:
 * Put cluster labels recursively in the flip case.
 */
static void place_flip_graph_label(graph_t * g)
{
    int c;
    pointf p, d;

    if (g != agroot(g) && GD_label(g) && !GD_label(g)->set) {
	if (GD_label_pos(g) & LABEL_AT_TOP) {
	    d = GD_border(g)[RIGHT_IX];
	    p.x = GD_bb(g).UR.x - d.x / 2;
	} else {
	    d = GD_border(g)[LEFT_IX];
	    p.x = GD_bb(g).LL.x + d.x / 2;
	}

	if (GD_label_pos(g) & LABEL_AT_RIGHT) {
	    p.y = GD_bb(g).LL.y + d.y / 2;
	} else if (GD_label_pos(g) & LABEL_AT_LEFT) {
	    p.y = GD_bb(g).UR.y - d.y / 2;
	} else {
	    p.y = (GD_bb(g).LL.y + GD_bb(g).UR.y) / 2;
	}
	GD_label(g)->pos = p;
	GD_label(g)->set = true;
    }

    for (c = 1; c <= GD_n_cluster(g); c++)
	place_flip_graph_label(GD_clust(g)[c]);
}

/* place_graph_label:
 * Put cluster labels recursively in the non-flip case.
 * The adjustments to the bounding boxes should no longer
 * be necessary, since we now guarantee the label fits in
 * the cluster.
 */
void place_graph_label(graph_t * g)
{
    int c;
    pointf p, d;

    if (g != agroot(g) && GD_label(g) && !GD_label(g)->set) {
	if (GD_label_pos(g) & LABEL_AT_TOP) {
	    d = GD_border(g)[TOP_IX];
	    p.y = GD_bb(g).UR.y - d.y / 2;
	} else {
	    d = GD_border(g)[BOTTOM_IX];
	    p.y = GD_bb(g).LL.y + d.y / 2;
	}

	if (GD_label_pos(g) & LABEL_AT_RIGHT) {
	    p.x = GD_bb(g).UR.x - d.x / 2;
	} else if (GD_label_pos(g) & LABEL_AT_LEFT) {
	    p.x = GD_bb(g).LL.x + d.x / 2;
	} else {
	    p.x = (GD_bb(g).LL.x + GD_bb(g).UR.x) / 2;
	}
	GD_label(g)->pos = p;
	GD_label(g)->set = true;
    }

    for (c = 1; c <= GD_n_cluster(g); c++)
	place_graph_label(GD_clust(g)[c]);
}
