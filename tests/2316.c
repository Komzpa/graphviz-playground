/// \file
/// \brief test case driver for #2316
///
/// See test_regression.py:test_2316.

#include <assert.h>
#include <cgraph.h>
#include <gvc.h>
#include <string.h>

static void use_context(void) {
  GVC_t *gvc = gvContext();
  assert(gvc != NULL);
  assert(gvFreeContext(gvc) == 0);
}

static void preexisting_default_attrs_survive(void) {
  assert(agattr(NULL, AGNODE, "test2316", "survives") != NULL);

  use_context();

  Agraph_t *g = agopen("G", Agdirected, NULL);
  assert(g != NULL);
  assert(agattr(g, AGNODE, "test2316", NULL) != NULL);
  assert(agattr(g, AGNODE, "label", NULL) != NULL);
  assert(agclose(g) == 0);
}

static void two_contexts_share_defaults(void) {
  GVC_t *gvc1 = gvContext();
  GVC_t *gvc2 = gvContext();
  assert(gvc1 != NULL);
  assert(gvc2 != NULL);

  assert(gvFreeContext(gvc1) == 0);

  Agraph_t *g = agopen("G", Agdirected, NULL);
  assert(g != NULL);
  assert(agattr(g, AGNODE, "label", NULL) != NULL);
  assert(agclose(g) == 0);

  assert(gvFreeContext(gvc2) == 0);
}

int main(int argc, char **argv) {
  if (argc > 1 && strcmp(argv[1], "preexisting") == 0) {
    preexisting_default_attrs_survive();
    return 0;
  }
  if (argc > 1 && strcmp(argv[1], "two-contexts") == 0) {
    two_contexts_share_defaults();
    return 0;
  }

  use_context();
  return 0;
}
