Graphviz 2437/1308 regression repair report
===========================================

Working branch: `codex/regress-2437-1308-20260728`

Base/head under repair before this change:
`1b34dd4f034b4f6e43d1879d76621f7f58073772`

Verification command:

```sh
python3 tools/verify_wobble_and_border.py && pytest tests/test_concentrate_*.py
```

Result: exit 0. The verifier completed first, then pytest reported
`290 passed, 1 skipped`.

Defect 1: tests/2437.dot
------------------------

Offending edge: `AA3:se -> AA4:sw [dir=both]`.

Upstream routes the concentrated/simple flat case as a straight edge after the
flat aux layout is copied back. Our tree had preserved an unnecessary
two-cubic excursion after the flat endpoint restoration step. The fix keeps the
existing x-only progression repair for ordinary ported flat edges, but for
non-grouped flat port edges explicitly drawing both arrows it aligns all
interior controls on the rendered endpoint line.

Rendered `-Tjson` control points and maximum deviation from the endpoint line:

```text
default:
  upstream: [(56.77, 4.05), (66.17, -1.48), (75.75, -1.08), (86.78, 4.78)] deviation=5.757pt
  before:   [(54.91, 19.25), (80.37, -6.21), (61.85, -6.42), (87.31, 19.04)] deviation=25.624pt
  after:    [(55.1, 10.09), (80.56, -15.36), (68.2, 16.33), (74.75, 15.98), (81.3, 15.92), (68.94, -15.29), (94.4, 10.16)] deviation=25.495pt
  fixed:    [(55.1, 0.0), (61.65, 0.01), (68.2, 0.02), (74.75, 0.04), (81.3, 0.05), (87.85, 0.06), (94.4, 0.07)] deviation=0.005pt

newrank=true:
  upstream: [(56.77, 4.05), (66.17, -1.48), (75.75, -1.08), (86.78, 4.78)] deviation=5.757pt
  before:   [(54.91, 19.25), (80.37, -6.21), (61.85, -6.42), (87.31, 19.04)] deviation=25.624pt
  after:    [(55.1, 10.09), (80.56, -15.36), (68.2, 16.33), (74.75, 15.98), (81.3, 15.92), (68.94, -15.29), (94.4, 10.16)] deviation=25.495pt
  fixed:    [(55.1, 0.0), (61.65, 0.01), (68.2, 0.02), (74.75, 0.04), (81.3, 0.05), (87.85, 0.06), (94.4, 0.07)] deviation=0.005pt
```

Defect 2: tests/1308.dot
------------------------

Offending edge: `Act_23 -> Act_24`.

Offending border after `1b34dd4f0`: `cluster_inner` bottom segment
`(97.895,18)-(203.89,18)`.

The verifier defines an edge as running along a cluster border when a rendered
control segment is within `0.5pt` of that border and overlaps it by at least
`4pt`. Four points is large enough to distinguish a real visible coincidence
from a clipped endpoint touch or floating point noise. The route fix nudges the
interior controls of simple adjacent flat splines by `8pt` away from a detected
cluster border; rendered clearance on 1308 is `4.525pt`.

Rendered `-Tjson` measurements:

```text
default:
  before: min edge-border distance=0.000pt, along_border=none
  after:  edge=Act_23 -> Act_24, border=cluster_inner bottom (97.895,18)-(203.89,18),
          separation=0.000pt, overlap=36.730pt
  fixed:  min edge-border distance=4.525pt, along_border=none

newrank=true:
  before: min edge-border distance=1.510pt, along_border=none
  after:  min edge-border distance=1.510pt, along_border=none
  fixed:  min edge-border distance=1.510pt, along_border=none
```

No-regression checks
--------------------

Concentrated same-rank pair still routes between its nodes:
`tests/test_concentrate_demo.py::test_same_rank_equivalent_edges_route_between_endpoints`
passed as part of `pytest tests/test_concentrate_*.py`.

Both arrowheads of a flat edge still have room:
covered by the concentrate pytest suite, including
`test_concentrate_short_bidirectional_flat_edge_stays_between_nodes` and
`test_concentrate_short_compound_arrows_do_not_overlap`.

No non-member node intersects a cluster box:
the 1308 default fix moves the edge route, not the cluster containment
constraints. Existing cluster containment behavior is preserved; the verifier's
byte-identity scan limits changed comparable outputs to the named fixtures.

Byte identity
-------------

Comparable rendered outputs matched `1858/1861`.

Changed graph/ranker outputs:

```text
default tests/1308.dot: 1308 cluster-border overlap removed
default tests/2437.dot: 2437 wobble removed
newrank=true tests/2437.dot: 2437 wobble removed
```

`tests/2282.dot` has pre-existing sub-nanopoint textual zero jitter
(`0` vs `-1.5987e-14`) between repeated guarded renders. The verifier reports
this as `identity zero-normalized matched ...` and does not count it as a graph
change.
