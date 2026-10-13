# Human-in-the-loop visual bisect

This method finds the commit that made a rendering look wrong when the defect
depends on visual judgment, such as ugly routing or label placement, and writing
a calibrated geometric checker first would be slower or contentious. It was
first used to locate a perimeter-detour-arc change across a 217-commit range
with only two human questions.

## Protocol

1. Pick one fixture whose rendering shows the defect at the branch tip and is
   clean at the base. Run `git bisect start <tip> <base>`.
2. At each bisect step, build `dot_builtins` and render the fixture to normalized
   xdot and PNG. Strip creator and version comments and build paths from the
   xdot, but keep drawing operations.
3. Keep a ledger of already judged renderings. If the new xdot is byte-identical
   to a rendering already judged good, automatically run `git bisect good`. If
   it is identical to a judged-bad rendering, automatically run
   `git bisect bad`. Ask the human only when a genuinely new picture appears.
4. The human question count is approximately the number of distinct visual
   states across the range, not the base-2 logarithm of the commit count. Most
   commits do not change a given picture, so two or three questions can be
   enough.
5. Record every human good verdict in an approvals ledger, using the
   `graphviz-operator-approvals-v1` schema, with the verbatim decision and the
   binary and PNG hashes. The approved rendering becomes both the target state
   for the eventual fix and an oracle waiver.
6. After locating the change, add a permanent automated checker and a
   minimized pytest reproducer. The human bisect finds the culprit; the checker
   prevents the defect from returning.

## Mechanics that matter

- Use one persistent clone and one build directory. `git bisect` switches the
  source, while incremental
  `cmake --build <build-dir> --target dot_builtins -- -j<jobs>` keeps each step
  focused.
- Normalize xdot before comparing it, or version stamps and build paths will
  create false new pictures.
- Run an automated bisect in parallel using a geometric checker or a plain
  output-diff predicate. Agreement between independent methods is evidence that
  the transition was found. Disagreement means the picture changed more than
  once, so bisect each transition separately.
- Keep per-step PNGs. They become evidence in the defect card.

## Worked example

In a 217-commit range, the first midpoint was clean and received a human good
verdict. The next step was identical to that approved rendering and was marked
good automatically. A later step introduced perimeter arcs and received a human
bad verdict. The remaining steps were judged automatically by rendering
identity. The bisect converged with two human decisions.
