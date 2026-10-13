/// @file
/// @brief Local tuning constants for dot spline reconstruction.

#pragma once

/// Extra clearance, in points, kept around node half-widths when estimating
/// multi-edge clearance. This prevents a concentrated replacement route from
/// clipping the node stroke after pen width is added.
#define MULTIEDGE_NODE_MARGIN 0.5

/// Stop tolerance, in points, for binary searches over cubic Bezier pieces that
/// trim unsafe route segments. Smaller values make clipping more exact but
/// spend more iterations in route cleanup.
#define MULTIEDGE_BEZIER_FLATNESS 0.01

/// Control-arm length, in points, used when a flat port has no natural segment
/// normal. This keeps the synthetic spline visibly curved instead of collapsing
/// into a straight overlap at the endpoint.
#define FLAT_PORT_NORMAL_ARM 36.0

/// Nominal arrow length, in points, before per-edge arrowsize scaling. This is
/// the same drawing convention used by compound edge clipping.
#define NOMINAL_ARROW_LENGTH 10.0

/// Minimum gap, in points, between endpoint labels and the route/node geometry
/// they are moved away from.
#define ENDPOINT_LABEL_GAP 4.0

/// Minimum node diameter, in points, before endpoint-label avoidance treats the
/// node as large enough to need explicit clearance.
#define ENDPOINT_LABEL_NODE_MIN 20.0
