/// @file
/// @brief Check that concentration preserves suppressed duplicate weights.

#define _GNU_SOURCE

#include <assert.h>
#include <stdbool.h>

#include <cgraph/cgraph.h>
#include <dotgen/dot.h>
#include <gvc/gvc.h>
#include <gvc/gvcext.h>

#ifdef _WIN32
#define IMPORT __declspec(dllimport)
#else
#define IMPORT
#endif

IMPORT extern gvplugin_library_t gvplugin_dot_layout_LTX_library;
IMPORT extern gvplugin_library_t gvplugin_core_LTX_library;

lt_symlist_t lt_preloaded_symbols[3];

static void load_builtin_plugins(void) {
  lt_preloaded_symbols[0] =
      (lt_symlist_t){.name = "gvplugin_dot_layout_LTX_library",
                     .address = &gvplugin_dot_layout_LTX_library};
  lt_symlist_t core = {.name = "gvplugin_core_LTX_library",
                       .address = &gvplugin_core_LTX_library};
  lt_preloaded_symbols[1] = core;
}

static bool chain_contains_weight(Agedge_t *edge, int weight) {
  for (Agedge_t *virtual_edge = ED_to_virt(edge); virtual_edge != NULL;
       virtual_edge = ED_to_virt(virtual_edge)) {
    if (ED_weight(virtual_edge) == weight) {
      return true;
    }
  }
  return false;
}

int main(void) {
  load_builtin_plugins();

  GVC_t *const gvc = gvContextPlugins(lt_preloaded_symbols, 0);
  assert(gvc != NULL);

  Agraph_t *const graph = agmemread("digraph {"
                                    "  graph [concentrate=true splines=ortho];"
                                    "  a -> b [label=<x> labelaligned=true];"
                                    "  a -> b [label=<x>];"
                                    "  b -> c [label=x labelaligned=true];"
                                    "  b -> c [label=x];"
                                    "  c -> d [xlabel=x labelaligned=true];"
                                    "  c -> d [xlabel=x];"
                                    "}");
  assert(graph != NULL);
  assert(gvLayout(gvc, graph, "dot") == 0);

  Agnode_t *const c = agnode(graph, "c", false);
  Agnode_t *const d = agnode(graph, "d", false);
  assert(c != NULL);
  assert(d != NULL);

  bool saw_summed_carrying_segment = false;
  for (Agedge_t *edge = agfstout(graph, c); edge != NULL;
       edge = agnxtout(graph, edge)) {
    if (aghead(edge) == d && chain_contains_weight(edge, 3)) {
      saw_summed_carrying_segment = true;
    }
  }
  assert(saw_summed_carrying_segment);

  assert(gvFreeLayout(gvc, graph) == 0);
  assert(agclose(graph) == 0);
  assert(gvFreeContext(gvc) == 0);
  return 0;
}
