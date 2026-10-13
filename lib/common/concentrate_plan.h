/// @file
/// @brief Read-only concentration candidate planning

#pragma once

#include <cgraph/cgraph.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef GVDLL
#ifdef GVC_EXPORTS
#define CONCENTRATE_PLAN_API __declspec(dllexport)
#else
#define CONCENTRATE_PLAN_API __declspec(dllimport)
#endif
#endif

#ifndef CONCENTRATE_PLAN_API
#define CONCENTRATE_PLAN_API /* nothing */
#endif

typedef enum {
  GV_CONCENTRATION_SAME_DIRECTION,
  GV_CONCENTRATION_OPPOSITE_DIRECTION,
} gv_concentration_direction_t;

typedef enum {
  GV_CONCENTRATION_SUPPRESSIBLE,
  GV_CONCENTRATION_SHARE_ROUTE_ONLY,
  GV_CONCENTRATION_INDEPENDENT,
} gv_concentration_verdict_t;

typedef struct {
  Agedge_t *edge;
  size_t input_index;
  gv_concentration_direction_t direction;
} gv_concentration_member_t;

typedef struct {
  Agedge_t *representative;
  size_t representative_index;
  gv_concentration_member_t *members;
  size_t member_count;
  gv_concentration_verdict_t verdict;
} gv_concentration_group_t;

typedef struct {
  gv_concentration_group_t *groups;
  size_t group_count;
} gv_concentration_plan_t;

CONCENTRATE_PLAN_API bool gv_concentration_edges_have_equal_rendered_identity(
    Agedge_t *representative, Agedge_t *candidate,
    gv_concentration_direction_t direction);
CONCENTRATE_PLAN_API gv_concentration_plan_t
gv_concentration_plan(Agraph_t *graph);
CONCENTRATE_PLAN_API void
gv_concentration_plan_free(gv_concentration_plan_t *plan);
CONCENTRATE_PLAN_API void
gv_concentration_plan_diagnose_if_enabled(Agraph_t *graph);

#undef CONCENTRATE_PLAN_API
