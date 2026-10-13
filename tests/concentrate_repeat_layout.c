/// @file
/// @brief Check that concentration arrow state does not survive gvFreeLayout.

#include <assert.h>
#include <cgraph/cgraph.h>
#include <gvc/gvc.h>
#include <gvc/gvcext.h>
#include <stddef.h>
#include <stdio.h>

#ifdef _WIN32
#define IMPORT __declspec(dllimport)
#else
#define IMPORT
#endif

IMPORT extern gvplugin_library_t gvplugin_dot_layout_LTX_library;
IMPORT extern gvplugin_library_t gvplugin_core_LTX_library;

lt_symlist_t lt_preloaded_symbols[3];

int main(void) {
  lt_preloaded_symbols[0] =
      (lt_symlist_t){.name = "gvplugin_dot_layout_LTX_library",
                     .address = &gvplugin_dot_layout_LTX_library};
  lt_preloaded_symbols[1] =
      (lt_symlist_t){.name = "gvplugin_core_LTX_library",
                     .address = &gvplugin_core_LTX_library};

  GVC_t *const gvc = gvContextPlugins(lt_preloaded_symbols, 0);
  assert(gvc != NULL);
  Agraph_t *const graph =
      agmemread("digraph { concentrate=true; a -> b [dir=none]; "
                "b -> a [arrowhead=normal]; }");
  assert(graph != NULL);

  assert(gvLayout(gvc, graph, "dot") == 0);
  assert(gvFreeLayout(gvc, graph) == 0);

  Agedge_t *const reverse =
      agedge(graph, agnode(graph, "b", 0), agnode(graph, "a", 0), NULL, 0);
  assert(reverse != NULL);
  assert(agset(reverse, "arrowhead", "vee") == 0);

  assert(gvLayout(gvc, graph, "dot") == 0);
  assert(gvRender(gvc, graph, "json", stdout) == 0);
  assert(gvFreeLayout(gvc, graph) == 0);
  assert(agclose(graph) == 0);
  assert(gvFreeContext(gvc) == 0);
  return 0;
}
