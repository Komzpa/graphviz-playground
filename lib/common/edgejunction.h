#pragma once

#include <common/types.h>
#include <stdbool.h>
#include <stddef.h>

bool edgejunction_skip_node(const node_t *n);
bool edgejunction_skip_edge(const edge_t *e);
size_t edgejunction_draw_spline_count(const edge_t *e);
textlabel_t *edgejunction_label(const edge_t *e);
