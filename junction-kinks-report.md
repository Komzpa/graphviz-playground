# Junction kinks report

As of 2026-07-27 22:04 Asia/Tbilisi.

Objective: fix labelled edgejunction fans on the DRBD anchor so the labels remain fused once each through junction nodes, while reducing foreign-spline label crossings, label-label overlaps, label-node overlaps, and dot final crossings under both default ranker and `-Gnewrank=true`.

Frozen invariants:
- Do not suppress labels or disable labelled fan fusion.
- Do not special-case the anchor by name.
- Fix this in rank/order/space reservation, not post-routing spline or label movement.
- Graphs without a labelled fan must remain byte-identical to the narrow branch head.

Lane ledger:
- Lead: owns implementation, integration, build, commit, final proof in `/home/kom/proj/ai_pr/graphviz-kinks-20260727/src`.
- Faraday `019fa44f-47aa-7df0-ab2a-b2c407cbc242`: read-only code scout for edgejunction rank/label ownership.
- Singer `019fa44f-b3d8-7272-9669-fb324e606344`: read-only verification scout for metric and corpus surfaces.

Initial findings:
- No checkout-local or parent `AGENTS.md` was found up to `/home/kom`; the user-provided instructions and selected skills are active.
- Branch is `codex/junction-kinks-20260727` at `05d1f897f6fd5b17762a31af93e0ba0933c008c4`, same head as `codex/junction-refuse-narrow-20260727`.
- `lib/dotgen/edgejunction.c` owns labelled fan insertion before rank. `make_group()` creates a synthetic trunk with the label copied from the representative edge, and label-less arms.
- The current code also assigns original edge label positions in `dot_edgejunction_splines()` after routing. That is a red flag against the requested ownership model: the label should reserve space through the synthetic labelled trunk during ranking instead of being moved after splines exist.

Subagent findings folded in:
- Faraday confirmed `dot_edgejunction()` is called from `dotLayout()` before `dot_rank()`, with `make_group()` as the synthetic node/trunk/arm owner.
- Faraday's candidate minimal fix is a pre-ranking reservation for the labelled synthetic trunk/junction, because the original edges are zeroed while the trunk only has the default one-rank hop.
- Singer confirmed this checkout has no build directory yet, while the comparison trees do have `build/cmd/dot/dot_builtins`.
- Singer found reusable verification pieces in `tools/verify_edgejunction_fuse.py`, `tools/verify_label_placement.py`, `tools/verify_flat_arrow_room.py`, and `tests/concentrate_helpers.py`.

Implementation probes:
- Copying the synthetic trunk's already-ranked label position back to the original edge preserves single label emission, but by itself did not reduce anchor metrics.
- Giving labelled junctions real invisible ellipse size after trunk label initialization proved the node footprint can be reserved before rank. At `0.25` inches it moved junctions and labels, but regressed `foreign_spline_labels` from 3 to 4 on the anchor under both rankers, so that size is not acceptable.
- Final accepted code keeps junction nodes as tiny points, gives labelled synthetic trunks `minlen >= 2` before rank, scales labelled trunk ordering weight using the parsed trunk label rather than the empty group attribute slot, and copies the ranked trunk-label position back to the original edge instead of recomputing a post-routing midpoint.

Final verification:
- Build: `cmake --build build --target dot_builtins -j 4` passed.
- Exit command passed: `python3 tools/verify_junction_labels.py && pytest tests/test_edgejunction.py tests/test_concentrate_*.py`.
- Pytest result: 318 passed, 1 skipped.
- Anchor default ranker: `foreign_spline_labels 3 -> 2`, `label_label_overlaps 0 -> 0`, `label_node_overlaps 0 -> 0`, `final_crossings 2 -> 2`.
- Anchor `newrank=true`: `foreign_spline_labels 3 -> 2`, `label_label_overlaps 0 -> 0`, `label_node_overlaps 0 -> 0`, `final_crossings 2 -> 2`.
- Required DRBD labels through junction nodes: `ioctl_set_disk()`, `receive_param()`, `io completion error`, and `start resync` each render exactly once before and after under both rankers.
- Named-label stories under both rankers:
  - `ioctl_set_disk()`: before box `(221.55,579.57,327.30,594.97)`, after `(290.90,713.06,396.65,728.46)`, no foreign splines crossing either.
  - `receive_param()`: before box `(248.56,441.64,363.31,457.04)` crossed by `Consistent->UpToDate`; after `(284.04,547.37,398.79,562.77)` crossed by none.
  - `io completion error`: before box `(109.63,47.26,246.88,62.66)`, after `(201.04,72.05,338.29,87.45)`, no foreign splines crossing either.
- Byte-identity sample for graph/rankers without a labelled fan: `78/78`.
- Changed tracked graphs containing a labelled fan: `graphs/directed/nhg.gv`.
