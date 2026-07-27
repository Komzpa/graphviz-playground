#pragma once

#include <common/types.h>
#include <stdbool.h>
#include <stddef.h>

bool concentrate_junction_skip_node(const node_t *n);
bool concentrate_junction_skip_edge(const edge_t *e);
size_t concentrate_junction_draw_spline_count(const edge_t *e);
textlabel_t *concentrate_junction_label(const edge_t *e);
