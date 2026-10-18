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

  // a concave vertex crossing: the segment leaves and re-enters the polygon
  // exactly at the vertices around the notch, so it lies outside the polygon
  // in between and no straight-line fallback may be reported for it
  Ppoint_t ps2[] = {{0, 0},  {10, 0}, {10, 10}, {7, 10}, {7, 5}, {6, 4},
                    {5, 5},  {3, 5},  {3, 10},  {0, 10}, {0, 0}};
  Ppoly_t poly2 = {.ps = ps2, .pn = sizeof(ps2) / sizeof(ps2[0])};
  Ppoint_t crossing[2] = {{1, 5}, {9, 5}};
  const int rc3 = Pshortestpath(&poly2, crossing, &path);
  if (rc3 == 0 && path.pn == 2) {
    fprintf(stderr, "vertex-crossing straight fallback was accepted\n");
    ret = EXIT_FAILURE;
  }

  // Collinear boundary chains hide two separate exterior intervals. Both
  // endpoints and the whole segment's midpoint are inside the polygon.
  Ppoint_t ps3[] = {{0, 0},   {14, 0},  {14, 10}, {12, 10}, {12, 5},
                   {11, 5},  {11, 4},  {10, 4},  {10, 5},  {9, 5},
                   {9, 10},  {5, 10},  {5, 5},   {4, 5},   {4, 4},
                   {3, 4},   {3, 5},   {2, 5},   {2, 10},  {0, 10}, {0, 0}};
  Ppoly_t poly3 = {.ps = ps3, .pn = sizeof(ps3) / sizeof(ps3[0])};
  for (int reverse = 0; reverse < 2; ++reverse) {
    Ppoint_t intervals[2] = {{reverse ? 13 : 1, 5}, {reverse ? 1 : 13, 5}};
    const int rc4 = Pshortestpath(&poly3, intervals, &path);
    if (rc4 == 0 && path.pn == 2) {
      fprintf(stderr, "collinear multiple-interval fallback was accepted\n");
      ret = EXIT_FAILURE;
    }
  }

  // Boundary-following and tangent segments remain valid, including the
  // collinear edges at the bottoms of both notches.
  for (int y = 0; y <= 4; y += 4) {
    Ppoint_t valid[2] = {{1, y}, {13, y}};
    const int rc5 = Pshortestpath(&poly3, valid, &path);
    if (rc5 != 0 || path.pn < 2) {
      fprintf(stderr, "boundary/tangent route was rejected (y=%d, rc=%d)\n", y,
              rc5);
      ret = EXIT_FAILURE;
    }
  }

  return ret;
}
