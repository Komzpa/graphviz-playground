# Concentrated Edge Routing

This note documents the implementation contract for `concentrate=true`.

It is intended for maintainers reviewing changes to concentrated edge routing
and for contributors preparing follow-up work. It describes what the current
code does, not what a clean-sheet implementation might do.

`concentrate=true` is not one isolated pass. It is a cross-phase behavior that
starts with normal edge classification, shares or suppresses virtual edge
chains where possible, preserves enough semantic information to draw every
surviving edge correctly, and then repairs spline geometry at the places where
several separately-routed pieces meet.

The central rule is:

* edges may share geometry when their routes are compatible;
* edges may disappear into a representative only when their rendered identity
  and foldable arrow decorations prove that the final drawing is still the same
  drawing.

Those two questions are deliberately separate.

## Dot Pipeline

`dot` still runs the normal phase sequence:

1. rank assignment;
2. crossing minimization;
3. node positioning;
4. spline routing;
5. compound-edge clipping when `compound=true`.

Concentration is threaded through those phases instead of being a single
post-processing rewrite.

After ranking, `dot_mincross()` calls `build_edge_chains()`. That creates
virtual chains for rank-spanning edges, records same-rank and self-edge
representations, and decides which earlier route a later edge may share. In
concentrate mode, this is also the first place where a semantically identical
edge may be marked `IGNORED`.

During `dot_position()`, `dot_concentrate()` runs after vertical coordinates
have been assigned and before leaf expansion. At that point the rank lists and
virtual chains exist, so the concentrator can merge adjacent virtual nodes into
shared trunks and junctions while still preserving the rank structure needed by
positioning.

During `dot_splines()`, the edge lists are sorted into routing groups. Ignored
edges are skipped, route-compatible but distinct edges may still be routed as
multi-edges, and concentration-specific helpers compact duplicates, restore
flat endpoints, align terminal tangents, align junction trunks, and preserve
labels.

After spline routing, `dotLayout()` calls `dot_compoundEdges()` for truthy
`compound`. Compound clipping must preserve the suppression bits carried on
concentrated junction endpoints, because an arrow that was intentionally hidden
at an absorbed junction must not reappear after clipping.

## Neato-Family Pipeline

The neato-family layouts do not call `dot_concentrate()`, because they do not
build dot rank/mincross virtual chains. They still observe the global
`Concentrate` flag set during graph initialization.

In neato spline routing, self-edges and multi-edge splines treat
`Concentrate` as "draw only the representative route" where the surrounding
code has already counted or chained parallel edges. `neatosplines.c` uses this
for self-arcs and for the main `spline_edges_()` loop. `multispline.c` makes
the same representative-only choice in its obstacle route builder.

The shared common routing and emission code is also part of this contract.
`routespl.c` folds straight-edge duplicates with the same rendered identity
when concentration is enabled. `splines.c` and `emit.c` then draw arrows and
labels from the retained edge plus its accumulated concentrated-arrow record.

## File Roles

### `lib/common/edgeattr.c`

This file defines the rendered-identity projection used by concentration. It
turns one edge into a deterministic signature of the edge features that layout
or emission will actually render.

The projection starts from parsed label and port state, then adds residual
text attributes from the graph's edge attribute table only when the later
renderer still consumes them. Unknown attributes and layout-only attributes do
not contribute to the signature.

The same code also handles reverse projection. When a candidate edge is folded
against a representative running the other way, head-owned and tail-owned
slots are read from the opposite physical endpoint.

### `lib/common/edgeattr.h`

This header exposes the comparison boundary to dotgen and common routing code.
The public functions are:

* `gv_edge_attributes_are_equal()`;
* `gv_opposite_edge_attributes_are_equal()`;
* `gv_edge_ports_are_equal()`;
* `gv_opposite_edge_ports_are_equal()`.

Callers use the same-direction forms when the candidate and retained edge run
between the same physical endpoints. They use the opposite-direction forms
when the candidate is being considered for a route whose tail and head are
swapped.

### `lib/common/arrows.c`

This file owns concentrated arrow decorations. Arrowheads are not part of the
core rendered-identity string; they are accumulated per physical endpoint on
the retained edge.

For same-direction concentration, a later edge can fold only when its own arrow
decoration equals the retained edge's accumulated decoration at each endpoint.
For opposite-direction concentration, each candidate endpoint is compared
against the opposite retained endpoint, and an empty endpoint can adopt a
non-empty compatible decoration.

The comparison helpers are
`same_direction_edge_arrow_decorations_are_mergeable()` and
`opposite_direction_edge_arrow_decorations_are_mergeable()`. The state update is
`fold_concentrated_edge_arrow_decorations()`.

The fold stores a complete record: shape flags, `arrowsize`, and effective
arrow fill color. It resolves borrowed color-list values to a stable RGBA text
form when needed, so the retained edge can emit the candidate's visible arrow
color even if the retained edge originally had no arrow at that endpoint.

### `lib/dotgen/conc.c`

This is dot's rank-list concentrator. It runs after ranking and mincross have
built the virtual chains, and after y-coordinates are set in `dot_position()`.

The main entry point, `dot_concentrate()`, first handles same-rank edges that
cannot be seen by the virtual-node passes. It then scans intermediate ranks
downward and upward, finds adjacent virtual nodes that represent equivalent
edge chains, and merges one node into the other.

When two virtual chains merge, `mergevirtual_pair()` transfers incoming or
outgoing segments, adds segment weight to the representative, marks internal
junction arrow endpoints as suppressed, and records distinct original edges
for drawing when the original rendered semantics do not match.

### `lib/dotgen/edge_chains.c`

This file builds the post-rank edge representation consumed by mincross,
positioning, and splines. Its job is broader than concentration, but
concentration relies on the order and ownership rules here.

`build_edge_chains()` visits outgoing edges in Cgraph order. A later edge may
reuse an earlier virtual chain only when the earlier edge already owns one.
This keeps input order deterministic and prevents a later edge from changing
whether an earlier edge concentrated.

There are three useful outcomes:

* an edge is rendered by its own chain;
* an edge shares another route but remains drawn as an ordinary multi-edge;
* an edge folds into a representative and becomes `IGNORED`.

The helpers `route_concentrated_parallel_edge()`,
`merge_backward_edge_with_opposite()`, and
`suppress_concentrated_cluster_edge_with_opposite()` decide which outcome
applies for same-direction, opposite-direction, and cluster-skeleton cases.

### `lib/dotgen/mincross.c`

This file does not decide rendered identity, but it consumes the virtual
chains that concentration has prepared.

`dot_mincross()` calls `build_edge_chains()` during initialization, before
rank arrays are allocated and before the crossing-reduction passes run. From
that point onward, retained virtual segments carry the counts, penalties, and
weights that crossing minimization sees.

The weight interaction is important. Concentrated or shared chains should keep
enough endpoint weight to influence ordering like the original edges, while
not over-weighting interior shared trunk segments merely because several
compatible routes passed through the same corridor.

### `lib/dotgen/dotsplines.c`

This file routes and installs dot splines after ranks and node positions are
known. It is where concentrated virtual structure becomes visible geometry.

The routing loop sorts edge representatives, skips `IGNORED` edges, compacts
duplicate routes, and then sends groups to the regular, flat, self-edge, or
curved route builders. The grouped-route builders can share corridors and
trunks for distinct edges that have compatible geometry but cannot be
suppressed semantically.

After each route is installed, concentrate-specific helpers restore flat
endpoint anchors, align arrows with their shafts, smooth alternating controls
for concentrated label and port cases, and update the bounding box.
`align_concentrated_route_tangents()` handles route-local tangent cleanup, and
`align_concentrated_junction_trunks()` handles fan junction trunk cleanup after
all routes have been installed.

The file also owns label survival after routing. Every pre-merge label must
still have a drawn instance. Identical labels may share the retained instance,
but non-identical labels remain on their own drawn routes or are positioned
beside the representative route.

### `lib/common/routespl.c`

This common router handles straight-line and curved routing used outside the
ranked dot path too. When `Concentrate` is set, `makeStraightEdges()` compares
candidate edges by rendered identity and same-direction arrow mergeability,
folds compatible arrow decorations into the retained edge, and drops ignored
candidates from the group before drawing.

This is the path that lets non-dot layouts participate in the same semantic
contract for simple concentrated multi-edges without importing dot's rank-list
concentrator.

### Compound Paths

Compound clipping is a late stage. It must respect the state carried by earlier
concentration decisions: ignored edges stay ignored, representative arrows may
include borrowed decorations, and absorbed junction endpoints keep their
suppression bits.

The identity projection also treats compound-only endpoint attributes as
rendered only when the root graph has `compound=true`. That avoids blocking
ordinary concentration on attributes that have no visible effect without
compound clipping, while still keeping cluster-clipping differences distinct
when they matter.

## The Two Questions

Concentration answers two questions for each candidate edge.

The first question is: would the non-arrow part of the rendered edge be the
same if this candidate disappeared into that representative?

The second question is: can this candidate's arrow decorations be folded onto
the representative without drawing the wrong arrows?

The first question is an identity projection. The second question is
mergeability, and mergeability is intentionally not a plain equivalence
relation.

### Rendered-Identity Projection

Rendered identity includes parsed edge labels, xlabels, endpoint labels, label
font state, label justification, label hyperlink state, HTML label structure,
HTML table data, HTML image identity, rendered label color, resolved ports,
clipping attributes, samehead and sametail groups when they have more than one
member, and rendered residual edge attributes.

The projection uses parsed state where parsing already happened. This matters
for aliases, defaults, inheritance, and normalization. For example, ports are
compared through resolved `ED_head_port()` and `ED_tail_port()` values rather
than raw `headport` or `tailport` text. Color values that can be translated are
compared after colorscheme-aware translation. HTML labels are walked as parsed
HTML label structures instead of treated as raw strings.

The projection deliberately excludes arrow shape, `arrowsize`, and arrow
fillcolor. Those are handled by the arrow-fold question. Keeping arrows out of
the identity string lets opposite-direction arrow-plus-no-arrow pairs fold
into one bidirectional route when that is visually correct.

It also excludes attributes that are not rendered. Hyperlink attributes affect
identity only when their owner and target are actually emitted. Layout-only
attributes such as weight are not part of rendered identity, because they
should influence the route construction rather than decide whether two already
routed edges look the same.

### Arrow-Fold Mergeability

Arrow compatibility is stateful because the retained edge may already have
borrowed decorations from earlier candidates.

Same-direction concentration is strict: the candidate's own start endpoint must
match the retained route's accumulated start endpoint, and the candidate's own
end endpoint must match the retained route's accumulated end endpoint. This
keeps an arrowless candidate from disappearing into a route that has already
borrowed arrows it would not draw.

Opposite-direction concentration is combinable rather than strictly equal. The
candidate's start endpoint is compared with the retained end endpoint, and its
end endpoint is compared with the retained start endpoint. Empty endpoints are
neutral, so an arrow can be borrowed onto the retained edge when no conflicting
arrow is already present.

That is why compatibility is not an equivalence relation. "Can fold into this
currently accumulated representative" depends on the representative's state
and on physical endpoint direction, not just on two independent edge records.

## Route Machinery

Concentration has three route shapes.

A true merge suppresses a candidate edge and draws the retained representative
instead. The suppressed candidate is marked `IGNORED`; any compatible arrow
decorations it contributed are stored on the retained edge.

A shared route keeps both edges drawn but uses one virtual chain. This is used
for distinct edges whose endpoints and route geometry are compatible but whose
rendered semantics are not identical enough to suppress. It lets dot keep
parallel corridors useful without disabling features.

A distinct route builds a separate chain. This is required when ports, labels,
endpoint attributes, clipping, arrows, or route geometry would make sharing or
suppression misleading.

Virtual chains are the spine of rank-spanning routing. `make_virtual_edge_chain()`
creates one virtual edge per rank step. `merge_chain()` lets another edge reuse
that chain and adjusts counts, crossing penalties, node width, and endpoint
weights according to whether the reused route should still affect mincross.

Mincross sees the retained virtual segments, not the suppressed original
parallel edges. That means concentrated edges must carry enough count, weight,
and penalty information for the rank ordering to behave like the original
graph's visible edge pressure.

Spline routing then turns chains into geometry. For hub fans, distinct edges
can use common corridors and trunks but receive separate terminal arms and
labels where needed. Junction nodes are virtual nodes with more than one
incoming or outgoing segment; they are drawn as meeting points, not as visible
nodes.

After route installation, junction alignment adjusts the single trunk arm
toward the mean direction of the multiple fan arms. This makes independent
spline pieces meet more smoothly at the same junction coordinate.

## Labels

Label survival is an invariant, not a cosmetic preference.

If an edge had a label before route sharing or concentration, the final drawing
must still contain a drawn instance of that label unless it is semantically the
same label already represented by the retained route.

Main labels and xlabels are conservative in the early chain-sharing decisions:
labeled candidates usually avoid semantic suppression. Endpoint labels remain
part of rendered identity because they are attached to physical endpoints and
can differ across same-direction and reverse edges.

After spline routing, identical concentrated labels may share one retained
placement. Distinct labels are kept on distinct routes or moved beside the
retained geometry when a self-edge label would otherwise pile up with its
duplicate.

## Invariants Worth Testing

The tests should keep pinning both sides of concentration:

* equivalent edges still merge;
* distinct rendered edges do not disappear;
* enabling concentration should not disable labels, endpoint labels, ports,
  hyperlinks, arrows, arrow colors, or compound clipping;
* a change should not pass by making concentration useless;
* a change should not pass by adding intermediate geometry degradations.

Crossing checks should compare concentrated output against a known base or
against the unconcentrated output where that is the meaningful baseline. In
the current tests, p3 and zero-crossing fixtures pin that concentration does
not add sampled crossings, and the b69 minimized case pins same-tail fan-out
crossing parity.

Geometry checks should include continuity at concatenation points and junctions.
The route-construction invariant is that separately installed spline pieces
that meet at a concentrated junction should share the junction coordinate and
should have compatible tangent directions there.

Current limitation: the G1-style artificial-joint cases still expose open work
around tangent continuity where a route is assembled through artificial joints
rather than through one ordinary concentrator junction. Tests should describe
that as a known limitation, not silently redefine the invariant away.

Arrow checks should cover both the route geometry and emission state. The
important failures are misplaced terminal tangents, arrows reappearing at
absorbed internal junctions, reverse-arrow decorations being borrowed onto the
wrong physical endpoint, and color-list or colorscheme values being compared
or emitted as the wrong endpoint color.

Label checks should verify that pre-merge labels survive and that identical
texts may share only when the drawn identity really is identical. Endpoint
label tests should include flat routes, same-rank reverse routes, and compound
routes, because those paths use different parts of the pipeline.

## Suspended Design Notes

Naive tangent averaging at joints was rejected. Averaging all incident arms can
point the trunk away from the actual single-trunk direction or flatten useful
fan separation. The current code aligns the single trunk arm toward the mean of
the opposite fan arms only for one-trunk/many-arm junctions.

Merge-all-boxes rerouting was rejected. Treating all concentrated routes as one
large obstacle problem can avoid some local artifacts, but it also loses the
distinction between "share a corridor" and "erase an edge." The current design
keeps semantic suppression separate from geometric sharing.

Fixed-side label placement was rejected. It is attractive because it is simple,
but it breaks when rankdir, label size, self-edges, ports, and fan shape change
the available side. The current code keeps labels attached to the route that
survives, dedupes only matching labels, and otherwise lets routing context
choose or adjust placement.

## Contributor Checklist

When changing concentration, check the call site first:

* identity or attribute semantics belong in `lib/common/edgeattr.c`;
* arrow folding or emitted arrow scalar values belong in `lib/common/arrows.c`;
* rank-list virtual-node merging belongs in `lib/dotgen/conc.c`;
* chain ownership and early route sharing belong in `lib/dotgen/edge_chains.c`;
* mincross pressure belongs in `lib/dotgen/mincross.c` or in weights passed to
  it;
* installed spline shape, junction continuity, and label survival belong in
  `lib/dotgen/dotsplines.c`;
* straight-edge folding shared with neato-family layouts belongs in
  `lib/common/routespl.c`;
* clipping and arrow suppression at installed routes must still satisfy the
  common `splines.c` and emission contracts.

Do not turn a semantic mismatch into a route-sharing shortcut unless the edge
will remain drawn. Do not turn a geometric sharing opportunity into semantic
suppression unless both rendered identity and arrow foldability pass.
