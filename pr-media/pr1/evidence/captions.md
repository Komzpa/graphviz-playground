# Synthetic Test-Fixture Promo Pairs

All images are from repo test fixtures or inline DOT extracted from tests/test_regression.py. No operator corpora used.

## 01-colors-kept

- before: `01-colors-kept-before.png`
- after: `01-colors-kept-after.png`
- fixture: `extracted_test_concentrate_preserves_distinct_edge_colors`
- provenance: inline DOT test_regression.py::test_concentrate_preserves_distinct_edge_colors line 7877
- caption: Colors kept: upstream collapses red and blue parallel edges into one red lane; branch keeps the blue lane visible.

## 02-reverse-arrow-kept

- before: `02-reverse-arrow-kept-before.png`
- after: `02-reverse-arrow-kept-after.png`
- fixture: `extracted_test_concentrate_preserves_reverse_arrow_after_no_arrow_duplicate`
- provenance: inline DOT test_regression.py::test_concentrate_preserves_reverse_arrow_after_no_arrow_duplicate line 8692
- caption: Reverse arrows kept: upstream loses the borrowed reverse arrow; branch renders the reverse arrowhead on the retained route.

## 03-multiedge-separation

- before: `03-multiedge-separation-before.png`
- after: `03-multiedge-separation-after.png`
- fixture: `extracted_test_concentrate_shared_trunk_keeps_visible_lanes_for_distinct_edges`
- provenance: inline DOT test_regression.py::test_concentrate_shared_trunk_keeps_visible_lanes_for_distinct_edges line 7964
- caption: Multi-edge separation: distinct colored fan-in routes remain separately traceable after the branch instead of sharing a misleading trunk.

## 04-same-rank-still-concentrates

- before: `04-same-rank-still-concentrates-before.png`
- after: `04-same-rank-still-concentrates-after.png`
- fixture: `extracted_test_concentrate_same_rank_equivalent_reverse_edges`
- provenance: inline DOT test_regression.py::test_concentrate_same_rank_equivalent_reverse_edges line 9193
- caption: Same-rank still concentrates: equivalent reverse same-rank edges remain concentrated in the branch; this is the no-regression pair.

## 05-crash-2764

- before: `05-crash-2764-before.png`
- after: `05-crash-2764-after.png`
- fixture: `tests_2764`
- provenance: repo fixture tests/2764.dot
- caption: Crash family: upstream base segfaults on public issue #2764; branch renders the repo test fixture.

## 06-self-loop-label-beside-loop

- before: `06-self-loop-label-beside-loop-before.png`
- after: `06-self-loop-label-beside-loop-after.png`
- fixture: `extracted_test_concentrated_duplicate_self_edge_routes_merge_with_one_label`
- provenance: inline DOT test_regression.py::test_concentrated_duplicate_self_edge_routes_merge_with_one_label line 5296
- caption: Self-loop label beside loop: upstream overprints duplicate labels on the loop; branch keeps one readable label beside the retained loop.

