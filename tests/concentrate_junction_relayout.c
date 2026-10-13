/* A concentrate=true fan is laid out through the library API, freed and laid
 * out again. The junction node and the arm/trunk edges are real cgraph
 * objects; gvFreeLayout() must take them out of the caller's graph, or the
 * second layout finds them as user input and fuses them again. */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include <cgraph/cgraph.h>
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

static const char source[] =
    "digraph { concentrate=true; a -> z; b -> z; c -> z; }";

static void expect_original_graph(Agraph_t *g, const char *when) {
  if (agnnodes(g) != 4 || agnedges(g) != 3) {
    fprintf(stderr, "%s: %d nodes %d edges, want 4 and 3\n", when,
            agnnodes(g), agnedges(g));
    assert(false);
  }
  assert(agnode(g, "_concentrate_junction_0", 0) == NULL);
  for (Agedge_t *e = agfstout(g, agnode(g, "a", 0)); e; e = agnxtout(g, e)) {
    char *marker = agget(e, "_concentrate_junction_original");
    assert(marker == NULL || marker[0] == '\0');
  }
}

int main(void) {
  lt_preloaded_symbols[0] =
      (lt_symlist_t){.name = "gvplugin_dot_layout_LTX_library",
                     .address = &gvplugin_dot_layout_LTX_library};
  lt_preloaded_symbols[1] =
      (lt_symlist_t){.name = "gvplugin_core_LTX_library",
                     .address = &gvplugin_core_LTX_library};
  GVC_t *gvc = gvContextPlugins(lt_preloaded_symbols, 0);
  assert(gvc != NULL);
  Agraph_t *g = agmemread(source);
  assert(g != NULL);

  for (int pass = 0; pass < 3; ++pass) {
    assert(gvLayout(gvc, g, "dot") == 0);
    /* The first layout really did make a junction (negative control). */
    assert(agnode(g, "_concentrate_junction_0", 0) != NULL);
    gvFreeLayout(gvc, g);
    expect_original_graph(g, pass == 0 ? "after first layout" : "after relayout");
  }

  agclose(g);
  gvFreeContext(gvc);
  return 0;
}
