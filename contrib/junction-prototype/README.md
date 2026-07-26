# Junction Nodes Concentration Prototype

This directory is an experiment: make `concentrate=true` a DOT-to-DOT graph
transformation before layout, rather than a post-ranking rewrite inside dotgen.
It intentionally changes no Graphviz layout engine source.

The prototype rewrites equivalent fan-in and fan-out edge groups through a
small point junction:

1. Edges sharing a head or tail and the same rendered identity are routed
   through one point node.
2. The shared trunk can carry the sum of the merged edge weights, and can
   optionally use a pen width based on the group size.
3. Group labels stay on the trunk edge, using dot's native edge-label virtual
   node machinery. Singleton labelled edges are not converted into explicit
   junction nodes.
4. With `--preserve-ranks` (the default), the tool first reads the original
   `dot -Tplain` node ranks and emits every transformed or reoriented eligible
   edge downward in that original order. Logical upward edges are reversed with
   `dir=back`, so the arrowhead remains on the logical head while dot's acyclic
   phase has less reason to flip the drawing.

Run:

```sh
contrib/junction-prototype/junctionize.py input.gv > output.gv
```

The implementation uses PyGraphviz/libcgraph for parsing and writing DOT. That
avoids depending on a particular formatter shape and lets the prototype inspect
graph, node, edge, and subgraph attributes through Graphviz's own parser.

## Grouping Rule

A group is a set of edges that share an endpoint and have identical values for:

`label`, `xlabel`, `headlabel`, `taillabel`, `color`, `style`, `penwidth`,
`arrowhead`, `arrowtail`, `dir`, `fontname`, `fontsize`, `fontcolor`,
`labelfontname`, `labelfontsize`, `labelfontcolor`, and `class`.

Every group with size at least `--min-group` is either transformed or reported
to stderr as refused. The machine-readable census format is:

```text
kind<TAB>endpoint<TAB>label<TAB>size
refused:reason<TAB>kind<TAB>endpoint<TAB>label<TAB>size
```

## Options

- `--fan-in` / `--no-fan-in`, on by default.
- `--fan-out` / `--no-fan-out`, on by default.
- `--min-group N`, default `2`.
- `--sum-weights` / `--no-sum-weights`, on by default.
- `--penwidth-by-count`, off by default.
- `--preserve-ranks` / `--no-preserve-ranks`, on by default. The off path is
  deliberately kept as a naive baseline for layout comparisons.
- `--visible-junctions`, off by default. The default point is invisible; this
  option makes it slightly visible for visual inspection.
- `--dot PATH`, default `dot`.

The output explicitly sets `concentrate=false`; otherwise old post-hoc
concentration can run on top of the transformed graph.

## Overshoot Gate

`overshoot.py` counts edges whose spline points leave the y-interval spanned by
their endpoint nodes by more than half a node height:

```sh
contrib/junction-prototype/overshoot.py input.gv
contrib/junction-prototype/overshoot.py --list dot-output.plain
```

The checker accepts either source DOT or captured `dot -Tplain` output.

## Guard Rails

The tool skips and reports self-loops, ported edges, `constraint=false` edges,
edges crossing between different clusters, strict graphs, and true multi-edges
between the same ordered endpoint pair. True multi-edges are skipped in this
prototype because PyGraphviz can parse them, but safely deleting just one member
while preserving all parallel edge identities is a separate piece of machinery.

`rankdir=LR` is reported as a non-TB case and left unchanged. The current rank
preservation and overshoot predicate are y-axis checks tuned for top-to-bottom
drawings.

## Fixtures

`fixtures/original/` contains the source graphs used for the prototype report.
Generated v2 comparison outputs are intentionally kept in the external
`artifacts-v2/` evidence directory for this lane, not checked in as static
fixtures.
