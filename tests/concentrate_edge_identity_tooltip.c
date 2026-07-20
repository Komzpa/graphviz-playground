#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include <common/const.h>
#include <cgraph/cgraph.h>
#include <common/edgeattr.h>
#include <common/types.h>
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

static bool compare_edges(const char *source) {
  lt_preloaded_symbols[0] =
      (lt_symlist_t){.name = "gvplugin_dot_layout_LTX_library",
                     .address = &gvplugin_dot_layout_LTX_library};
  lt_preloaded_symbols[1] =
      (lt_symlist_t){.name = "gvplugin_core_LTX_library",
                     .address = &gvplugin_core_LTX_library};

  GVC_t *gvc = gvContextPlugins(lt_preloaded_symbols, 0);
  assert(gvc != NULL);

  Agraph_t *graph = agmemread(source);
  assert(graph != NULL);
  assert(gvLayout(gvc, graph, "dot") == 0);

  Agnode_t *tail = agnode(graph, "a", false);
  assert(tail != NULL);
  Agedge_t *first = agfstout(graph, tail);
  assert(first != NULL);
  Agedge_t *second = agnxtout(graph, first);
  assert(second != NULL);

  const bool equal = gv_edge_attributes_are_equal(first, second);

  gvFreeLayout(gvc, graph);
  agclose(graph);
  gvFreeContext(gvc);
  return equal;
}

static bool ortho_duplicate_is_ignored(void) {
  lt_preloaded_symbols[0] =
      (lt_symlist_t){.name = "gvplugin_dot_layout_LTX_library",
                     .address = &gvplugin_dot_layout_LTX_library};
  lt_preloaded_symbols[1] =
      (lt_symlist_t){.name = "gvplugin_core_LTX_library",
                     .address = &gvplugin_core_LTX_library};

  GVC_t *gvc = gvContextPlugins(lt_preloaded_symbols, 0);
  assert(gvc != NULL);

  const char *source = "digraph {"
                       "  graph [concentrate=true splines=ortho];"
                       "  a -> b;"
                       "  a -> b;"
                       "}";
  Agraph_t *graph = agmemread(source);
  assert(graph != NULL);
  assert(gvLayout(gvc, graph, "dot") == 0);

  Agnode_t *tail = agnode(graph, "a", false);
  assert(tail != NULL);
  Agedge_t *first = agfstout(graph, tail);
  assert(first != NULL);
  Agedge_t *second = agnxtout(graph, first);
  assert(second != NULL);

  const bool ignored = ED_edge_type(second) == IGNORED;

  gvFreeLayout(gvc, graph);
  agclose(graph);
  gvFreeContext(gvc);
  return ignored;
}

int main(int argc, char **argv) {
  assert(argc == 2);

  if (strcmp(argv[1], "parsed-label-fallback") == 0) {
    const char *source = "digraph {"
                         "  a -> b [URL=\"u\" label=\"\\T\"];"
                         "  a -> b [URL=\"u\" label=\"a\" tooltip=\"a\"];"
                         "}";
    assert(compare_edges(source));
    return 0;
  }

  if (strcmp(argv[1], "xlabel-no-fallback") == 0) {
    const char *source = "digraph {"
                         "  a -> b [URL=\"u\" xlabel=\"x\"];"
                         "  a -> b [URL=\"u\" xlabel=\"x\" tooltip=\"x\"];"
                         "}";
    assert(!compare_edges(source));
    return 0;
  }

  if (strcmp(argv[1], "ortho-duplicate-ignored") == 0) {
    assert(ortho_duplicate_is_ignored());
    return 0;
  }

  fprintf(stderr, "unknown scenario: %s\n", argv[1]);
  return 1;
}
