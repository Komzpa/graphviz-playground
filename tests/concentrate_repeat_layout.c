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

  const int first_layout = gvLayout(gvc, graph, "dot");
  assert(first_layout == 0);
  const int first_free = gvFreeLayout(gvc, graph);
  assert(first_free == 0);

  Agedge_t *const reverse =
      agedge(graph, agnode(graph, "b", 0), agnode(graph, "a", 0), NULL, 0);
  assert(reverse != NULL);
  const int set_status = agset(reverse, "arrowhead", "vee");
  assert(set_status == 0);

  const int second_layout = gvLayout(gvc, graph, "dot");
  assert(second_layout == 0);
  const int render_status = gvRender(gvc, graph, "json", stdout);
  assert(render_status == 0);
  const int second_free = gvFreeLayout(gvc, graph);
  assert(second_free == 0);
  const int close_status = agclose(graph);
  assert(close_status == 0);
  const int context_status = gvFreeContext(gvc);
  assert(context_status == 0);
  // Under NDEBUG the asserts vanish; keep the statuses used.
  (void)first_layout, (void)first_free, (void)set_status, (void)second_layout,
      (void)render_status, (void)second_free, (void)close_status,
      (void)context_status;
  return 0;
}
