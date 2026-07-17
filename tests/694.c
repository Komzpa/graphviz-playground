/* Regression test for class default rules. See test_694_class_defaults. */
#include <assert.h>
#include <cgraph.h>
#include <string.h>

int main(void) {
  Agraph_t *const graph = agmemread(
      "digraph { node.foo [shape=box]; a [class=foo]; b [class=foo, shape=ellipse]; }");
  assert(graph != NULL);
  Agnode_t *const a = agnode(graph, "a", 0);
  Agnode_t *const b = agnode(graph, "b", 0);
  assert(a != NULL && b != NULL);
  assert(strcmp(agget(a, "shape"), "box") == 0);
  assert(strcmp(agget(b, "shape"), "ellipse") == 0);

  /* Rules are read-time defaults, not a retroactive C API cascade. */
  assert(agset(a, "class", "") == 0);
  assert(strcmp(agget(a, "shape"), "box") == 0);
  assert(agclose(graph) == 0);
  return 0;
}
