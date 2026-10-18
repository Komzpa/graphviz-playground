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

Labels, direction, arrow shapes, endpoint-paired attributes, weights, and
`constraint` are read with their inherited edge defaults before rewriting, so
an inherited `edge [constraint=false]` (in any false spelling) refuses
byte-identically just like an explicit per-edge value. Reversing an edge
also materializes the opposite endpoint's default so asymmetric defaults do not
leak back onto the wrong endpoint. Unescaped `\E`, `\H`, or `\T` in any of
`label`, `xlabel`, `headlabel`, or `taillabel` cause a `label-substitution`
refusal: evaluating them on a generated segment could change their text.
Escaped backslashes and ordinary multiline labels remain eligible for grouping.

Generated trunks explicitly use `minlen=0`; each arm retains the original
edge's full `minlen`, including inherited defaults. Group `label` and `xlabel`
appear only on the trunk. The shared endpoint label also stays on the trunk,
while each distinct endpoint label stays on its arm. All other label attributes
are explicitly cleared on the generated segments so defaults cannot duplicate
labels or move them onto a junction.
Candidates whose shared endpoint is a record or Mrecord are refused as
`record-endpoint`; dot can drop a zero-length flat edge against a record node.
Record nodes at arm endpoints remain supported.

The shared trunk has no original edge ID or URL/href, target, or tooltip
metadata (including the edge/label/head/tail variants). Explicit empty values
override inherited interaction defaults there; each arm retains its own
interaction attributes. A shared segment must not navigate to the first edge's
destination.

Every group with size at least `--min-group` is either transformed, or the
whole input is reported to stderr as refused and emitted byte-for-byte unchanged.
The machine-readable census format is:

```text
kind<TAB>endpoint<TAB>label<TAB>size
refused:reason<TAB>kind<TAB>endpoint<TAB>label<TAB>size
```

Field values escape backslash, tab, newline and carriage return as `\\`, `\t`,
`\n` and `\r`, so one record is always one line with a fixed column count.

The tests under `tests/test_junction_prototype.py` need PyGraphviz (it is not in
`requirements.txt` because it needs the Graphviz development headers); they are
skipped when it is not installed. `fixtures/baseline_junctionize.py` is the
reference implementation that `verify_skewer_order.py` compares against.

The verifier identifies overlap pairs by label text, label kind, and logical
endpoints, not an edge's enumeration index or physical serialization direction.
Ordinary `label` and `xlabel` are distinct owners even on the same edge: dot
draws both into `_ldraw_` (label first, then xlabel), so the verifier splits
those runs at the `lp`/`xlp` placed centers before naming them.
It also canonicalizes the order of each pair. Reordering or reversing an edge
therefore does not turn an existing collision into a new one; different label
owners and genuinely new collision pairs remain distinguishable.

The refusal oracle compares output directly with the original input bytes,
independently of what the reference implementation transforms or refuses.

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

The tool skips and reports self-loops, ported edges, record shared endpoints,
`constraint=false` edges, context-dependent labels, edges crossing between
different clusters, strict graphs, and true multi-edges between the same
ordered endpoint pair. True multi-edges are skipped in this prototype because
PyGraphviz can parse them, but safely deleting just one member while preserving
all parallel edge identities is a separate piece of machinery.

`rankdir=LR` is reported as a non-TB case and left unchanged. The current rank
preservation and overshoot predicate are y-axis checks tuned for top-to-bottom
drawings.

The original-rank `dot -Tplain` probe uses the same 60-second bound as the
prototype's full-fixture consumer test. A timeout follows the rank-read failure
path: emit the original bytes, report `refused:rank-read-failed`, and exit 1.

## Smoke Check

```sh
contrib/junction-prototype/smoke.py
```

The smoke check covers byte-exact passthrough for every current refusal reason.

## Fixtures

`fixtures/original/` contains the source graphs used for the prototype report.
Generated v2 comparison outputs are intentionally kept in the external
`artifacts-v2/` evidence directory for this lane, not checked in as static
fixtures.
