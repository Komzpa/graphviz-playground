/// @file
/// @brief Read-only concentration candidate planning

#include "config.h"

#include <common/concentrate_plan.h>
#include <common/edgeattr.h>
#include <common/render.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>

static gv_concentration_direction_t direction_from(Agedge_t *representative,
                                                   Agedge_t *candidate) {
  return agtail(representative) != aghead(representative) &&
                 agtail(representative) == aghead(candidate) &&
                 aghead(representative) == agtail(candidate)
             ? GV_CONCENTRATION_OPPOSITE_DIRECTION
             : GV_CONCENTRATION_SAME_DIRECTION;
}

static bool have_common_unordered_endpoints(Agedge_t *first, Agedge_t *second) {
  return (agtail(first) == agtail(second) && aghead(first) == aghead(second)) ||
         (agtail(first) == aghead(second) && aghead(first) == agtail(second));
}

static bool ports_are_equal(Agedge_t *representative, Agedge_t *candidate,
                            gv_concentration_direction_t direction) {
  return direction == GV_CONCENTRATION_OPPOSITE_DIRECTION
             ? gv_opposite_edge_ports_are_equal(representative, candidate)
             : gv_edge_ports_are_equal(representative, candidate);
}

bool gv_concentration_edges_have_equal_rendered_identity(
    Agedge_t *representative, Agedge_t *candidate,
    gv_concentration_direction_t direction) {
  const bool attributes_are_equal =
      direction == GV_CONCENTRATION_OPPOSITE_DIRECTION
          ? gv_opposite_edge_attributes_are_equal(representative, candidate)
          : gv_edge_attributes_are_equal(representative, candidate);
  const bool arrows_are_mergeable =
      direction == GV_CONCENTRATION_OPPOSITE_DIRECTION
          ? opposite_direction_edge_arrow_decorations_are_mergeable(
                representative, candidate)
          : same_direction_edge_arrow_decorations_are_mergeable(representative,
                                                                candidate);
  return attributes_are_equal && arrows_are_mergeable;
}

gv_concentration_plan_t gv_concentration_plan(Agraph_t *graph) {
  gv_concentration_plan_t plan = {0};
  const size_t edge_count = agnedges(graph);
  if (edge_count < 2)
    return plan;

  Agedge_t **edges = gv_calloc(edge_count, sizeof(*edges));
  bool *assigned = gv_calloc(edge_count, sizeof(*assigned));
  size_t collected = 0;
  for (Agnode_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    for (Agedge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {
      edges[collected++] = edge;
    }
  }

  plan.groups = gv_calloc(collected, sizeof(*plan.groups));
  for (size_t i = 0; i < collected; ++i) {
    if (assigned[i])
      continue;

    size_t member_count = 0;
    for (size_t j = i; j < collected; ++j) {
      if (!assigned[j] && have_common_unordered_endpoints(edges[i], edges[j]))
        member_count++;
    }
    if (member_count < 2) {
      assigned[i] = true;
      continue;
    }

    gv_concentration_group_t *group = &plan.groups[plan.group_count++];
    group->representative = edges[i];
    group->representative_index = i;
    group->members = gv_calloc(member_count, sizeof(*group->members));
    group->member_count = member_count;
    group->verdict = GV_CONCENTRATION_SUPPRESSIBLE;

    size_t member_index = 0;
    for (size_t j = i; j < collected; ++j) {
      if (assigned[j] || !have_common_unordered_endpoints(edges[i], edges[j]))
        continue;
      assigned[j] = true;
      const gv_concentration_direction_t direction =
          direction_from(edges[i], edges[j]);
      group->members[member_index++] = (gv_concentration_member_t){
          .edge = edges[j], .input_index = j, .direction = direction};
      if (!ports_are_equal(edges[i], edges[j], direction)) {
        group->verdict = GV_CONCENTRATION_INDEPENDENT;
      } else if (group->verdict != GV_CONCENTRATION_INDEPENDENT &&
                 !gv_concentration_edges_have_equal_rendered_identity(
                     edges[i], edges[j], direction)) {
        group->verdict = GV_CONCENTRATION_SHARE_ROUTE_ONLY;
      }
    }
  }

  free(assigned);
  free(edges);
  return plan;
}

void gv_concentration_plan_free(gv_concentration_plan_t *plan) {
  if (plan == NULL)
    return;
  for (size_t i = 0; i < plan->group_count; ++i)
    free(plan->groups[i].members);
  free(plan->groups);
  *plan = (gv_concentration_plan_t){0};
}

static const char *verdict_name(gv_concentration_verdict_t verdict) {
  switch (verdict) {
  case GV_CONCENTRATION_SUPPRESSIBLE:
    return "suppressible";
  case GV_CONCENTRATION_SHARE_ROUTE_ONLY:
    return "share-route-only";
  case GV_CONCENTRATION_INDEPENDENT:
    return "independent";
  }
  return "independent";
}

void gv_concentration_plan_diagnose_if_enabled(Agraph_t *graph) {
  const char *const enabled = getenv("GV_CONCENTRATION_PLAN_DIAGNOSTICS");
  if (enabled == NULL || enabled[0] == '\0' || strcmp(enabled, "0") == 0)
    return;

  gv_concentration_plan_t plan = gv_concentration_plan(graph);
  for (size_t i = 0; i < plan.group_count; ++i) {
    const gv_concentration_group_t *const group = &plan.groups[i];
    fprintf(stderr,
            "concentrate-plan\tgroup=%zu\trepresentative=%zu:%s%s%s"
            "\tmembers=",
            i, group->representative_index,
            agnameof(agtail(group->representative)),
            agisdirected(graph) ? "->" : "--",
            agnameof(aghead(group->representative)));
    for (size_t j = 0; j < group->member_count; ++j) {
      const gv_concentration_member_t *const member = &group->members[j];
      fprintf(stderr, "%s%zu:%s", j == 0 ? "" : ",", member->input_index,
              member->direction == GV_CONCENTRATION_OPPOSITE_DIRECTION
                  ? "opposite"
                  : "same");
    }
    fprintf(stderr, "\tverdict=%s\n", verdict_name(group->verdict));
  }
  gv_concentration_plan_free(&plan);
}
