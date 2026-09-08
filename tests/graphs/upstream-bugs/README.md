# Upstream reproducers

Minimal inputs for defects that reproduce on UNMODIFIED upstream graphviz
(measured against aef8a6fd8). They are kept here so a gate that trips on one of
them can point at proof that the cause is not ours, and so a report can be
written without rediscovering the input.

- `2282-fdp-bb-nondeterministic.dot` — the same binary and flags produce
  different `-Txdot` bytes across runs: near-zero cluster `bb` fields flip
  between `0` and `-1.5987e-14`. Any identity check must treat this graph as
  nondeterministic rather than retrying until two runs agree.
- `2766-concentrate-rank-lead-null.dot` — upstream exits 1 with
  `rebuild_vlists: lead is null for rank 1`.

A third candidate was removed after measurement: `digraph { A -> B; B -> A; }`
under `concentrate=true` leaves one edge with `pos=null`, which looks like a
dropped edge until you read the surviving one — it carries arrowheads at BOTH
ends. That is the documented merge of opposite parallel edges, not a defect.
