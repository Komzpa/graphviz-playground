/*************************************************************************
 * Copyright (c) 2026 Graphviz Authors
 * All rights reserved.
 *
 * This program and the accompanying materials are made available under
 * the terms of the Eclipse Public License v2.0 which accompanies this
 * distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *************************************************************************/

#include "config.h"

#include <common/render.h>
#include <dotgen/concentrate_plan.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>

typedef struct {
  void *address;
  size_t size;
  void *before;
} memory_undo_t;

typedef struct {
  elist *target;
  edge_t **original;
  size_t size;
} elist_undo_t;

typedef struct {
  edge_t *edge;
  gv_concentrated_arrow_snapshot_t *before;
} arrow_undo_t;

struct gv_concentration_transaction_s {
  uint64_t candidate_id;
  memory_undo_t *memory;
  size_t memory_count;
  elist_undo_t *elists;
  size_t elist_count;
  arrow_undo_t *arrows;
  size_t arrow_count;
  edge_t **new_virtual_edges;
  size_t new_virtual_edge_count;
  node_t **new_virtual_nodes;
  size_t new_virtual_node_count;
};

void gv_concentration_plan_context_init(gv_concentration_plan_context_t *ctx) {
  *ctx = (gv_concentration_plan_context_t){
      .next_candidate_id = 1,
      .rollback_probe = getenv("GV_CONCENTRATE_ROLLBACK") != NULL,
      .transaction = gv_alloc(sizeof(*ctx->transaction)),
  };
}

void gv_concentration_candidate_set_init(
    gv_concentration_plan_context_t *ctx, gv_concentration_candidate_set_t *set,
    const char *phase, gv_concentration_action_t action, bool legacy_accepts,
    uint32_t rejection_reasons, gv_concentration_candidate_executor_t execute,
    void *payload) {
  memset(set, 0, sizeof(*set));
  set->candidate_count = 2;
  set->candidates[0] = (gv_concentration_candidate_t){
      .id = ctx->next_candidate_id++,
      .phase = phase,
      .action = GV_CONCENTRATION_NO_MERGE,
      .score = 0,
  };
  set->candidates[1] = (gv_concentration_candidate_t){
      .id = ctx->next_candidate_id++,
      .phase = phase,
      .action = action,
      .score = 1,
      .feasibility_reasons = legacy_accepts ? GV_CONCENTRATION_REASON_NONE
                             : rejection_reasons == GV_CONCENTRATION_REASON_NONE
                                 ? GV_CONCENTRATION_REASON_LEGACY_REJECTED
                                 : rejection_reasons,
      .execute = execute,
      .payload = payload,
  };
}

gv_concentration_candidate_t *
gv_concentration_score(gv_concentration_candidate_set_t *set) {
  gv_concentration_candidate_t *selected = &set->candidates[0];
  for (size_t i = 1; i < set->candidate_count; i++) {
    gv_concentration_candidate_t *const candidate = &set->candidates[i];
    if (candidate->feasibility_reasons == GV_CONCENTRATION_REASON_NONE &&
        candidate->score > selected->score) {
      selected = candidate;
    }
  }
  return selected;
}

void gv_concentration_candidate_set_free(
    gv_concentration_candidate_set_t *set) {
  memset(set, 0, sizeof(*set));
}

static gv_concentration_transaction_t *
begin_transaction(const gv_concentration_candidate_t *candidate) {
  gv_concentration_transaction_t *const transaction =
      gv_alloc(sizeof(*transaction));
  transaction->candidate_id = candidate->id;
  return transaction;
}

void gv_concentration_transaction_record(
    gv_concentration_transaction_t *transaction, void *address, size_t size) {
  for (size_t i = 0; i < transaction->memory_count; i++) {
    const memory_undo_t *const undo = &transaction->memory[i];
    if (undo->address == address && undo->size == size) {
      return;
    }
  }
  transaction->memory =
      gv_recalloc(transaction->memory, transaction->memory_count,
                  transaction->memory_count + 1, sizeof(*transaction->memory));
  memory_undo_t *const undo = &transaction->memory[transaction->memory_count++];
  undo->address = address;
  undo->size = size;
  undo->before = gv_alloc(size);
  memcpy(undo->before, address, size);
}

void gv_concentration_transaction_record_elist(
    gv_concentration_transaction_t *transaction, elist *list) {
  for (size_t i = 0; i < transaction->elist_count; i++) {
    if (transaction->elists[i].target == list) {
      return;
    }
  }
  transaction->elists =
      gv_recalloc(transaction->elists, transaction->elist_count,
                  transaction->elist_count + 1, sizeof(*transaction->elists));
  elist_undo_t *const undo = &transaction->elists[transaction->elist_count++];
  undo->target = list;
  undo->size = list->size;
  undo->original = list->list;
  list->list = gv_calloc(list->size + 1, sizeof(*list->list));
  if (list->size != 0) {
    memcpy(list->list, undo->original, list->size * sizeof(*list->list));
  }
}

void gv_concentration_transaction_record_arrow(
    gv_concentration_transaction_t *transaction, edge_t *edge) {
  for (size_t i = 0; i < transaction->arrow_count; i++) {
    if (transaction->arrows[i].edge == edge) {
      return;
    }
  }
  transaction->arrows =
      gv_recalloc(transaction->arrows, transaction->arrow_count,
                  transaction->arrow_count + 1, sizeof(*transaction->arrows));
  arrow_undo_t *const undo = &transaction->arrows[transaction->arrow_count++];
  undo->edge = edge;
  undo->before = snapshot_concentrated_edge_arrow_decorations(edge);
}

void gv_concentration_transaction_track_virtual_edge(
    gv_concentration_transaction_t *transaction, edge_t *edge) {
  transaction->new_virtual_edges = gv_recalloc(
      transaction->new_virtual_edges, transaction->new_virtual_edge_count,
      transaction->new_virtual_edge_count + 1,
      sizeof(*transaction->new_virtual_edges));
  transaction->new_virtual_edges[transaction->new_virtual_edge_count++] = edge;
}

void gv_concentration_transaction_track_virtual_node(
    gv_concentration_transaction_t *transaction, node_t *node) {
  transaction->new_virtual_nodes = gv_recalloc(
      transaction->new_virtual_nodes, transaction->new_virtual_node_count,
      transaction->new_virtual_node_count + 1,
      sizeof(*transaction->new_virtual_nodes));
  transaction->new_virtual_nodes[transaction->new_virtual_node_count++] = node;
}

static void
free_transaction_storage(gv_concentration_transaction_t *transaction) {
  for (size_t i = 0; i < transaction->memory_count; i++) {
    free(transaction->memory[i].before);
  }
  for (size_t i = 0; i < transaction->arrow_count; i++) {
    free_concentrated_edge_arrow_snapshot(transaction->arrows[i].before);
  }
  free(transaction->memory);
  free(transaction->elists);
  free(transaction->arrows);
  free(transaction->new_virtual_edges);
  free(transaction->new_virtual_nodes);
  free(transaction);
}

static void rollback_transaction(gv_concentration_transaction_t *transaction) {
  for (size_t i = transaction->arrow_count; i > 0; i--) {
    const arrow_undo_t *const undo = &transaction->arrows[i - 1];
    restore_concentrated_edge_arrow_decorations(undo->edge, undo->before);
  }
  for (size_t i = transaction->memory_count; i > 0; i--) {
    const memory_undo_t *const undo = &transaction->memory[i - 1];
    memcpy(undo->address, undo->before, undo->size);
  }
  for (size_t i = transaction->elist_count; i > 0; i--) {
    elist_undo_t *const undo = &transaction->elists[i - 1];
    free(undo->target->list);
    undo->target->list = undo->original;
    undo->target->size = undo->size;
    undo->original = NULL;
  }

  for (size_t i = transaction->new_virtual_edge_count; i > 0; i--) {
    edge_t *const edge = transaction->new_virtual_edges[i - 1];
    Agedgepair_t *const pair =
        (Agedgepair_t *)((char *)edge - offsetof(Agedgepair_t, out));
    free(AGDATA(&pair->out));
    free(pair);
  }
  for (size_t i = transaction->new_virtual_node_count; i > 0; i--) {
    node_t *const node = transaction->new_virtual_nodes[i - 1];
    free(ND_in(node).list);
    free(ND_out(node).list);
    free(ND_flat_in(node).list);
    free(ND_flat_out(node).list);
    free(ND_other(node).list);
    free(ND_save_in(node).list);
    free(ND_save_out(node).list);
    free(ND_tree_in(node).list);
    free(ND_tree_out(node).list);
    free(AGDATA(node));
    free(node);
  }

  free_transaction_storage(transaction);
}

static void commit_transaction(gv_concentration_transaction_t *transaction) {
  for (size_t i = 0; i < transaction->elist_count; i++) {
    free(transaction->elists[i].original);
  }
  free_transaction_storage(transaction);
}

bool gv_concentration_apply(gv_concentration_plan_context_t *ctx,
                            gv_concentration_candidate_set_t *set) {
  gv_concentration_candidate_t *const selected = gv_concentration_score(set);
  if (selected->action == GV_CONCENTRATION_NO_MERGE) {
    gv_concentration_candidate_set_free(set);
    return false;
  }

  gv_concentration_transaction_t *transaction = ctx->transaction;
  if (transaction == NULL)
    transaction = begin_transaction(selected);
  if (!selected->execute(selected, transaction)) {
    rollback_transaction(transaction);
    if (transaction == ctx->transaction)
      ctx->transaction = NULL;
    gv_concentration_candidate_set_free(set);
    return false;
  }

  if (ctx->rollback_probe && transaction != ctx->transaction) {
    rollback_transaction(transaction);
    transaction = begin_transaction(selected);
    if (!selected->execute(selected, transaction)) {
      rollback_transaction(transaction);
      gv_concentration_candidate_set_free(set);
      return false;
    }
  }

  if (transaction != ctx->transaction)
    commit_transaction(transaction);
  gv_concentration_candidate_set_free(set);
  return true;
}

void gv_concentration_plan_context_commit(gv_concentration_plan_context_t *ctx) {
  if (ctx->transaction != NULL) {
    commit_transaction(ctx->transaction);
    ctx->transaction = NULL;
  }
}
