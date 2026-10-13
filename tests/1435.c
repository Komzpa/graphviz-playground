/// @file
/// @brief fallback routes of `Pshortestpath` must stay inside the polygon
///
/// https://gitlab.com/graphviz/graphviz/-/issues/1435

#include <pathplan.h>
#include <stdio.h>
#include <stdlib.h>

int main(void) {
  // A C-shaped (concave) polygon whose final vertex repeats the first, so the
  // triangulation is incomplete. The diagonal between the two arms crosses the
  // notch outside the polygon, so no straight-line fallback may be reported.
  Ppoint_t ps[] = {{0, 0},  {10, 0}, {10, 2}, {2, 2}, {2, 8},
                   {10, 8}, {10, 10}, {0, 10}, {0, 0}};
  Ppoly_t poly = {.ps = ps, .pn = sizeof(ps) / sizeof(ps[0])};
  Ppoint_t eps[2] = {{9, 1}, {9, 9}};
  Ppolyline_t path = {0};

  int ret = EXIT_SUCCESS;

  const int rc = Pshortestpath(&poly, eps, &path);
  if (rc == 0 && path.pn == 2) {
    fprintf(stderr, "straight fallback through the notch was accepted\n");
    ret = EXIT_FAILURE;
  }

  // neighbouring case: a segment lying wholly inside the polygon stays valid
  Ppoint_t inside[2] = {{1, 1}, {9, 1}};
  const int rc2 = Pshortestpath(&poly, inside, &path);
  if (rc2 != 0 || path.pn < 2) {
    fprintf(stderr, "in-polygon route was rejected (rc=%d)\n", rc2);
    ret = EXIT_FAILURE;
  }

  return ret;
}
