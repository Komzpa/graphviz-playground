/// \file
/// \brief verify that failed dot layouts propagate through the public C API
///
/// See https://gitlab.com/graphviz/graphviz/-/issues/2761.

#ifdef NDEBUG
#error "this program is not intended to be compiled with assertions disabled"
#endif

#include <assert.h>
#include <graphviz/cgraph.h>
#include <graphviz/gvc.h>
#include <stddef.h>

static const char malformed_root[] =
    "digraph{00[0=0]00[0=0]{subgraph cluster{{c}->A{Act000->4}Act004->0}}"
    "{Act000->Act004[0=0]rank=same}pack=0}";

static const char malformed_subgraph[] =
    "digraph root{subgraph target{00[0=0]00[0=0]{subgraph cluster{{c}->A"
    "{Act000->4}Act004->0}}{Act000->Act004[0=0]rank=same}pack=0}}";

static const char valid_root[] = "digraph{a->b}";

static const char valid_subgraph[] = "digraph root{subgraph target{a->b}}";

static char target_name[] = "target";

static Agraph_t *select_target(Agraph_t *root, char *subgraph_name) {
  if (subgraph_name == NULL)
    return root;

  Agraph_t *target = agsubg(root, subgraph_name, 0);
  assert(target != NULL);
  return target;
}

static void check_failed_layout(const char *source, char *subgraph_name) {
  GVC_t *gvc = gvContext();
  assert(gvc != NULL);

  Agraph_t *root = agmemread(source);
  assert(root != NULL);
  Agraph_t *target = select_target(root, subgraph_name);

  assert(gvLayout(gvc, target, "dot") != 0);
  assert(!gvLayoutDone(target));
  assert(!gvLayoutDone(root));
  assert(gvFreeLayout(gvc, target) == 0);
  assert(agclose(root) == 0);
  gvFreeContext(gvc);
}

static void check_successful_layout(const char *source, char *subgraph_name) {
  GVC_t *gvc = gvContext();
  assert(gvc != NULL);

  Agraph_t *root = agmemread(source);
  assert(root != NULL);
  Agraph_t *target = select_target(root, subgraph_name);

  assert(gvLayout(gvc, target, "dot") == 0);
  assert(gvLayoutDone(target));
  assert(gvLayoutDone(root));
  assert(gvFreeLayout(gvc, target) == 0);
  assert(agclose(root) == 0);
  gvFreeContext(gvc);
}

int main(void) {
  check_failed_layout(malformed_root, NULL);
  check_failed_layout(malformed_subgraph, target_name);
  check_successful_layout(valid_root, NULL);
  check_successful_layout(valid_subgraph, target_name);
  return 0;
}
