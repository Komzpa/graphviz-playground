/// @file
/// @brief Rendered edge identity comparison

#pragma once

#include <stdbool.h>

#include <common/types.h>

#ifdef GVDLL
#ifdef GVC_EXPORTS
#define EDGEATTR_API __declspec(dllexport)
#else
#define EDGEATTR_API __declspec(dllimport)
#endif
#endif

#ifndef EDGEATTR_API
#define EDGEATTR_API /* nothing */
#endif

EDGEATTR_API bool gv_edge_attributes_are_equal(Agedge_t *first_edge,
                                               Agedge_t *second_edge);
EDGEATTR_API bool gv_opposite_edge_attributes_are_equal(Agedge_t *first_edge,
                                                        Agedge_t *second_edge);
EDGEATTR_API bool gv_edge_ports_are_equal(Agedge_t *first_edge,
                                          Agedge_t *second_edge);
EDGEATTR_API bool gv_opposite_edge_ports_are_equal(Agedge_t *first_edge,
                                                   Agedge_t *second_edge);

#undef EDGEATTR_API
