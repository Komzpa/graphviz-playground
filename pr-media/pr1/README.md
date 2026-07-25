# PR 1 Before/After Visual Evidence

Small DOT inputs rendered twice for Komzpa/graphviz-playground PR 1:

- before: base `main` at `aef8a6fd874726f17e3797561d23491bd9e156fa`
- after: reviewer examples at PR head `4468c76c0`; fixed promo gallery at
  `abc0279bd`

The pull request description references these files so the rendered comparison
stays stable even if the source branch moves.

## Reviewer Examples

| Example | Defect class | Before | After |
| --- | --- | --- | --- |
| `parallel-colors` | Hidden distinct parallel edge | Base concentrates red/blue/red parallel edges into fewer visible lanes and hides one distinct color route. | Branch keeps the distinct red and blue parallel routes visible while still merging the duplicate red edge. |
| `y-trunk-colors` | Equivalent pair still merges plus distinct keep routes | Base loses the distinct colored trunk routes when concentration chooses a shared route. | Branch keeps the red and blue trunk routes separate while the equivalent plain routes still concentrate. |
| `reverse-arrow-shapes` | Opposite-direction decoration fold | Base folds opposite-direction edges with different arrowhead shapes into a shared decoration. | Branch preserves the direction-specific normal and vee arrowheads on their own visible routes. |
| `same-rank-reverse` | Flat bidirectional shared route | Base draws the same-rank reverse pair as two bowed routes. | Branch concentrates the pair into one flat shared bidirectional route. |
| `segmented-vs-parallel` | Segmented color route vs parallel color lanes | Base suppresses one of the two distinct color semantics. | Branch draws both the parallel red/blue lanes and the serial half-red/half-blue route. |
| `crash-2764` | Public issue 2764 concentration crash | Base exits with SIGSEGV in `conc_slope()`; the before side is recorded as text only. | Branch renders the public repro successfully. |

## Fixed Defect Promo Gallery

One minimized before/after pair per fixed visual defect class where the rendered
change is clear at a glance.

| Example | Defect class | Before | After |
| --- | --- | --- | --- |
| `endpoint-labels` | Detached endpoint labels | Endpoint labels float away from their own edge. | Endpoint labels sit on the edge they describe. |
| `touching-splines-drbd` | Confusable touching splines | Distinct state-transition routes touch and become hard to follow. | The routes separate so each transition is traceable. |
| `line-gap-train11` | Gap in a route | The long return route has a visible break near the endpoint. | The return route is continuous. |
| `horizontal-bus-collapse` | Horizontal bus collapse | The bus flattens into a dense cable band. | The bus descends diagonally through the graph. |
| `spine-bend` | Bent spine | The main spine bends back through the layout. | The spine follows a cleaner downward path. |
| `mid-air-junction-arrowheads` | Mid-air junction arrowheads | A concentrated route draws an arrowhead at an internal junction. | Only real endpoint arrowheads remain. |
| `fanout-shared-trunk` | Fan-out shared trunk | Same-tail fan-out routes split immediately. | The routes share an initial trunk before splitting. |
