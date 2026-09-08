/* Regression test for class default rules. See test_694_class_defaults. */
#include <assert.h>
#include <cgraph.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  Agraph_t *const graph = agmemread(
      "strict digraph {"
      "node.foo [shape=box];"
      "a [class=foo, shape=ellipse]; b [class=foo];"
      "edge.foo [color=red]; a -> b [class=foo, color=green];"
      "subgraph s { node [shape=diamond]; node.foo [shape=hexagon];"
      "edge [color=black]; edge.foo [color=blue]; a; a -> b; }"
      "}");
  assert(graph != NULL);
  Agnode_t *const a = agnode(graph, "a", 0);
  Agnode_t *const b = agnode(graph, "b", 0);
  assert(a != NULL && b != NULL);
  assert(strcmp(agget(a, "shape"), "ellipse") == 0);
  assert(strcmp(agget(b, "shape"), "box") == 0);
  Agedge_t *const edge = agedge(graph, a, b, NULL, 0);
  assert(edge != NULL);
  assert(strcmp(agget(edge, "color"), "green") == 0);

  /* A C API class change keeps the current rule-derived value and makes it
   * explicit so write/read does not silently drop it. */
  assert(agset(b, "class", "") == 0);
  assert(strcmp(agget(b, "shape"), "box") == 0);
  FILE *const stream = tmpfile();
  assert(stream != NULL);
  assert(agwrite(graph, stream) == 0);
  rewind(stream);
  Agraph_t *const roundtrip = agread(stream, NULL);
  assert(roundtrip != NULL);
  Agnode_t *const roundtrip_b = agnode(roundtrip, "b", 0);
  assert(roundtrip_b != NULL);
  assert(strcmp(agget(roundtrip_b, "shape"), "box") == 0);
  assert(agclose(roundtrip) == 0);
  assert(fclose(stream) == 0);
  assert(agclose(graph) == 0);

  /* Exercise graph-object applied-state cleanup under LeakSanitizer. */
  for (int i = 0; i < 1000; ++i) {
    Agraph_t *const cycle = agmemread(
        "digraph { graph.foo [color=red]; graph [class=foo]; }");
    assert(cycle != NULL);
    assert(agclose(cycle) == 0);
  }
  return 0;
}
