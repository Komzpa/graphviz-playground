#pragma once

#include "pathplan.h"

#ifdef __cplusplus
extern "C" {
#endif

#if defined(GVDLL) && defined(PATHPLAN_EXPORTS)
#define PATHPLAN_INTERNAL_API __declspec(dllexport)
#elif defined(GVDLL)
#define PATHPLAN_INTERNAL_API __declspec(dllimport)
#else
#define PATHPLAN_INTERNAL_API
#endif

/* Internal entry point for callers that retain an ordered corridor and can
 * associate each template point with an authoritative rank/portal direction.
 * The public Proutespline API remains source- and ABI-compatible. */
PATHPLAN_INTERNAL_API int Proutespline_with_fallbacks(
    Pedge_t *barriers, size_t n_barriers, Ppolyline_t input_route,
    Ppoint_t endpoint_slopes[2], const Pvector_t *split_fallbacks,
    Ppolyline_t *output_route);

#undef PATHPLAN_INTERNAL_API

#ifdef __cplusplus
}
#endif
