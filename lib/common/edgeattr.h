/// @file
/// @brief Rendered edge identity comparison

#pragma once

#include <stdbool.h>

#include <common/types.h>

bool gv_edge_attributes_are_equal(Agedge_t *first_edge, Agedge_t *second_edge);
bool gv_opposite_edge_attributes_are_equal(Agedge_t *first_edge,
                                           Agedge_t *second_edge);
bool gv_edge_ports_are_equal(Agedge_t *first_edge, Agedge_t *second_edge);
bool gv_opposite_edge_ports_are_equal(Agedge_t *first_edge,
                                      Agedge_t *second_edge);
