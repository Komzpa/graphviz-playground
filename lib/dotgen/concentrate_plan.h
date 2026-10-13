/*************************************************************************
 * Copyright (c) 2026 Graphviz Authors
 * All rights reserved.
 *
 * This program and the accompanying materials are made available under
 * the terms of the Eclipse Public License v2.0 which accompanies this
 * distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *************************************************************************/

#pragma once

#include <common/types.h>
#include <stdbool.h>
#include <stdint.h>

typedef enum {
  GV_CONCENTRATION_NO_MERGE,
  GV_CONCENTRATION_SUPPRESS_PARALLEL,
  GV_CONCENTRATION_SHARE_ROUTE,
  GV_CONCENTRATION_SUPPRESS_OPPOSITE,
  GV_CONCENTRATION_SHARE_FLAT_ROUTE,
  GV_CONCENTRATION_SUPPRESS_FLAT,
  GV_CONCENTRATION_MERGE_VIRTUAL_PAIR,
  GV_CONCENTRATION_REPRESENT_CLUSTER,
  GV_CONCENTRATION_FINALIZE_RANKS,
} gv_concentration_action_t;

typedef enum {
  GV_CONCENTRATION_REASON_NONE = 0,
  GV_CONCENTRATION_REASON_LEGACY_REJECTED = 1u << 0,
  GV_CONCENTRATION_REASON_NO_REPRESENTATIVE = 1u << 1,
  GV_CONCENTRATION_REASON_INCOMPATIBLE_ENDPOINTS = 1u << 2,
  GV_CONCENTRATION_REASON_INCOMPATIBLE_ATTRIBUTES = 1u << 3,
  GV_CONCENTRATION_REASON_CONSTRAINT_ROUTE = 1u << 4,
} gv_concentration_reason_t;

typedef struct gv_concentration_transaction_s gv_concentration_transaction_t;
typedef struct gv_concentration_candidate_s gv_concentration_candidate_t;

typedef bool (*gv_concentration_candidate_executor_t)(
    const gv_concentration_candidate_t *candidate,
    gv_concentration_transaction_t *transaction);

struct gv_concentration_candidate_s {
  uint64_t id;
  const char *phase;
  gv_concentration_action_t action;
  int64_t score;
  uint32_t feasibility_reasons;
  gv_concentration_candidate_executor_t execute;
  void *payload;
};

typedef struct {
  gv_concentration_candidate_t candidates[2];
  size_t candidate_count;
} gv_concentration_candidate_set_t;

typedef struct {
  uint64_t next_candidate_id;
  bool rollback_probe;
  gv_concentration_transaction_t *transaction;
} gv_concentration_plan_context_t;

void gv_concentration_plan_context_init(gv_concentration_plan_context_t *ctx);
void gv_concentration_candidate_set_init(
    gv_concentration_plan_context_t *ctx, gv_concentration_candidate_set_t *set,
    const char *phase, gv_concentration_action_t action, bool legacy_accepts,
    uint32_t rejection_reasons, gv_concentration_candidate_executor_t execute,
    void *payload);
gv_concentration_candidate_t *
gv_concentration_score(gv_concentration_candidate_set_t *set);
void gv_concentration_candidate_set_free(gv_concentration_candidate_set_t *set);

bool gv_concentration_apply(gv_concentration_plan_context_t *ctx,
                            gv_concentration_candidate_set_t *set);
void gv_concentration_plan_context_commit(gv_concentration_plan_context_t *ctx);

void gv_concentration_transaction_record(
    gv_concentration_transaction_t *transaction, void *address, size_t size);
void gv_concentration_transaction_record_elist(
    gv_concentration_transaction_t *transaction, elist *list);
void gv_concentration_transaction_record_arrow(
    gv_concentration_transaction_t *transaction, edge_t *edge);
void gv_concentration_transaction_track_virtual_edge(
    gv_concentration_transaction_t *transaction, edge_t *edge);
void gv_concentration_transaction_track_virtual_node(
    gv_concentration_transaction_t *transaction, node_t *node);
