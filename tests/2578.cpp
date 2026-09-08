#include <cstdio>

#include <gvc/gvc.h>

Agraph_t *readstring(char *string);
Agraph_t *firstsubg(Agraph_t *g);
Agnode_t *protonode(Agraph_t *g);
char *setv(Agnode_t *n, char *attr, char *val);

int main() {
  char input[] = "strict digraph { A subgraph sub { B } }";
  Agraph_t *g = readstring(input);
  if (g == nullptr)
    return 2;

  Agraph_t *subg = firstsubg(g);
  if (subg == nullptr)
    return 3;

  setv(protonode(subg), (char *)"shape", (char *)"pentagon");
  agwrite(g, stdout);
  agclose(g);
  return 0;
}
