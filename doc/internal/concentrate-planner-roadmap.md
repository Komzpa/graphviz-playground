# Concentration Planner Integration Roadmap

## Current State

The common concentration planner groups input edges by unordered endpoints and
classifies each group as suppressible, share-route-only, or independent.
Suppressible means every member has the same rendered identity, compatible
ports, and mergeable arrow decorations. Share-route-only means ports are
compatible but rendered identity differs, so geometry may be shared but no edge
may be suppressed. Independent means endpoint ports differ, so even route
sharing needs an engine-specific decision.

Dot uses its own apply-phase planner for topology-changing concentration.
The common planner is read-only and is available for diagnostics in dot, neato,
and fdp. Neato-family straight-edge concentration already compares rendered
attributes and arrow decorations before suppressing ordinary parallel edges.
Self-loop concentration now also checks the common rendered-identity predicate
before suppressing a virtual self-edge chain.

## Census Snapshot

The neato/fdp census compared common-planner verdicts with emitted xdot edge
records on the corpus section inputs and the duplicate-edge fixture family.
A drawn edge record is an xdot edge statement with `_draw_`; a suppressed edge
record is an emitted edge statement without `_draw_`.

The current scan covered 658 planner groups and recorded 83 render failures from
the corpus inputs. The classified rendered groups were:

| Class | Count |
| --- | ---: |
| Suppressible group still drawn separately | 106 |
| Share-route-only group suppressed | 1 |
| Kept distinct as expected | 519 |
| Suppressed as expected | 32 |

The duplicate-edge fixture family is clean for neato and fdp: no unsafe
suppression, no missed suppressible merge, and no share/suppress mismatch.

The remaining share/suppress mismatch is a compound cluster case with one
visible edge and one `style=invis` edge. The missed suppressible merges are
concentrated in compound or clustered corpus inputs where straight-looking
output does not prove that the engine has a safe common route certificate.

## Harvested Now

The low-risk changes harvested at this stage are:

- fdp emits the same read-only planner diagnostics as neato when
  `GV_CONCENTRATION_PLAN_DIAGNOSTICS` is enabled.
- neato-family self-loops keep visually distinct loop edges instead of
  suppressing every self-edge chain under `concentrate=true`.

Both changes are local. The diagnostic hook does not change output when
diagnostics are disabled. The self-loop guard only changes groups whose common
planner identity predicate rejects suppression.

## Next Stages

### Curved and Multispline Routes

Curved and multispline concentration must not suppress or share routes merely
because endpoints match. Add an engine-owned route certificate that records the
representative path, endpoint ports, clipping endpoints, and obstacle context.
Only groups with equal rendered identity and a matching route certificate may
be suppressed. Share-route-only groups may reuse geometry only if every member
keeps its own rendered edge record.

Gate this stage with the crossing gate, kink fixtures, curl census, and
candidate-vs-head xdot parity for fixtures outside the changed route class.

### Trunk-and-Junction Fan-In

The planned fan-in design is to collect member edges into a port, draw one
shared trunk segment about one rank length, end that trunk at a virtual junction
node, and then fan out from that virtual junction to the member nodes around it.
This is a future concentration apply-phase design, not current behavior.

### fdp Stress Coverage

fdp currently exposes planner diagnostics but still needs apply-phase work.
Start with straight, non-compound, non-cluster parallel edges whose emitted
routes are identical and whose planner verdict is suppressible. Keep compound,
cluster, and pin-position interactions out of the first fdp apply patch.

Gate this stage with neato/fdp per-engine merge counts, crossing checks, and
frozen fixture parity for all non-fdp inputs.

### sfdp Guards

sfdp should initially remain a guarded no-op for concentration apply behavior.
It may use the planner for diagnostics only after initialization order and
rendered identity are proven equivalent to fdp for the same frozen fixtures.

Gate this stage with diagnostics-only byte parity when the environment variable
is disabled and explicit rejection of route-moving behavior.

### twopi and circo Hooks

twopi and circo can adopt read-only planner diagnostics after their
initialization phases populate edge records, labels, ports, and arrow state.
No apply behavior should be added until each engine has an explicit route
certificate model.

Gate this stage with diagnostics-disabled byte parity and a small frozen
fixture set covering directed, undirected, reverse, ported, and labeled edges.

### osage and patchwork

osage and patchwork should remain explicit no-ops. Their layout models do not
have a concentration apply phase, and adding one would need a separate design.

Gate this stage by preserving diagnostics-disabled output parity and documenting
the no-op behavior in engine-level tests.
