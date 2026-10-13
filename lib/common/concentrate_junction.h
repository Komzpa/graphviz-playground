#pragma once

#include <cgraph/cgraph.h>
#include <common/types.h>
#include <stdbool.h>
#include <stddef.h>

/* Junction bookkeeping lives in its own cgraph record, bound next to the
 * layout record, never in Agnodeinfo_t/Agedgeinfo_t: those structs are part of
 * the installed public headers, and growing them would shift the offset of
 * every later member for plugins built against the previous libgvc ABI. */
typedef struct {
  Agrec_t hdr;
  bool junction_node;
  bool internal;
  bool draw_trunk;
  size_t emit_splines;
  void *info;
} concentrate_junction_state_t;

#define CONCENTRATE_JUNCTION_RECORD "Agconcentratejunction_t"

/** Read the state of a node or edge; an unbound or virtual object reads as all
 *  zero. The result is read-only. */
const concentrate_junction_state_t *concentrate_junction_state(const void *obj);
/** Bind (if needed) and return the writable state. The object must already
 *  carry its locked layout record so the new record cannot become AGDATA. */
concentrate_junction_state_t *concentrate_junction_state_bind(void *obj);
/** Free and unbind the state record of obj, releasing its owned info. */
void concentrate_junction_state_release(void *obj);

#define ND_concentrate_junction(n) (concentrate_junction_state(n)->junction_node)
#define ED_concentrate_junction(e) (concentrate_junction_state(e)->info)
#define ED_concentrate_junction_internal(e)                                    \
  (concentrate_junction_state(e)->internal)
#define ED_concentrate_junction_draw_trunk(e)                                  \
  (concentrate_junction_state(e)->draw_trunk)
#define ED_concentrate_junction_emit_splines(e)                                \
  (concentrate_junction_state(e)->emit_splines)

bool concentrate_junction_skip_node(const node_t *n);
bool concentrate_junction_skip_edge(const edge_t *e);
size_t concentrate_junction_draw_spline_count(const edge_t *e);
textlabel_t *concentrate_junction_label(const edge_t *e);
