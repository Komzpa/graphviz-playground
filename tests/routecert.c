#include <assert.h>
#include <stdio.h>
#include <string.h>

/* Keep this fixture link-independent so tests/test_regression.py can exercise
 * the internal certificate module on static and shared Graphviz builds. */
#include <common/routecert.c>

static bezier spline(pointf *points) {
  return (bezier){.list = points, .size = 4};
}

static route_rendered_route_t rendered(uint64_t id, bezier *pieces,
                                       size_t piece_count) {
  return (route_rendered_route_t){
      .id = id,
      .pieces = pieces,
      .piece_count = piece_count,
  };
}

static route_crossing_signature_t
crossings(const route_rendered_route_t *target,
          const route_rendered_route_t *routes, size_t route_count) {
  route_crossing_signature_t signature = {0};
  assert(route_crossing_signature(target, routes, route_count, &signature) ==
         ROUTE_CERT_OK);
  return signature;
}

static void test_portal_signature(void) {
  Pedge_t portals[] = {
      {.a = {-1, -1}, .b = {-1, 1}},
      {.a = {1, -1}, .b = {1, 1}},
  };
  route_portal_signature_t first = {0};
  route_portal_signature_t second = {0};
  assert(route_portal_signature(portals, 2, 3, &first) == ROUTE_CERT_OK);
  assert(route_portal_signature(portals, 2, 3, &second) == ROUTE_CERT_OK);
  assert(route_portal_signatures_equal(&first, &second));

  Pedge_t mirrored_portals[] = {
      {.a = {1, -1}, .b = {1, 1}},
      {.a = {-1, -1}, .b = {-1, 1}},
  };
  route_portal_signature_t mirrored = {0};
  assert(route_portal_signature(mirrored_portals, 2, 3, &mirrored) ==
         ROUTE_CERT_OK);
  assert(mirrored.size == first.size);
  for (size_t i = 0; i < first.size; i++) {
    assert(mirrored.portals[i].a.x == -first.portals[i].a.x);
    assert(mirrored.portals[i].a.y == first.portals[i].a.y);
    assert(mirrored.portals[i].b.x == -first.portals[i].b.x);
    assert(mirrored.portals[i].b.y == first.portals[i].b.y);
  }
  assert(!route_portal_signatures_equal(&first, &mirrored));

  FILE *dump = tmpfile();
  assert(dump != NULL);
  assert(route_portal_signature_dump(dump, &first) == 0);
  rewind(dump);
  char buffer[128] = {0};
  assert(fread(buffer, 1, sizeof(buffer) - 1, dump) > 0);
  assert(strstr(buffer, "portal[0]") != NULL);
  fclose(dump);
  route_portal_signature_free(&first);
  route_portal_signature_free(&second);
  route_portal_signature_free(&mirrored);
  puts("portal identity and mirrored order: pass");
}

static void test_identical_and_refit_routes(void) {
  pointf target_points[] = {{-2, 0}, {-1, 0}, {1, 0}, {2, 0}};
  pointf refit_points[] = {{-2, 0}, {-1, 0.4}, {1, -0.4}, {2, 0}};
  pointf foreign_points[] = {{0, -2}, {0, -1}, {0, 1}, {0, 2}};
  bezier target_piece = spline(target_points);
  bezier refit_piece = spline(refit_points);
  bezier foreign_piece = spline(foreign_points);
  route_rendered_route_t target = rendered(1, &target_piece, 1);
  route_rendered_route_t refit = rendered(1, &refit_piece, 1);
  route_rendered_route_t foreign = rendered(2, &foreign_piece, 1);
  route_rendered_route_t original_routes[] = {target, foreign};
  route_rendered_route_t refit_routes[] = {refit, foreign};
  route_crossing_signature_t first = crossings(&target, original_routes, 2);
  route_crossing_signature_t identical = crossings(&target, original_routes, 2);
  route_crossing_signature_t changed_geometry =
      crossings(&refit, refit_routes, 2);
  assert(first.size == 1);
  assert(first.events[0].kind == ROUTE_CROSSING_TRANSVERSE);
  assert(first.events[0].orientation == 1);
  assert(first.events[0].multiplicity == 1);
  assert(route_crossing_signatures_equal(&first, &identical));
  assert(route_crossing_signatures_equal(&first, &changed_geometry));
  route_crossing_signature_free(&first);
  route_crossing_signature_free(&identical);
  route_crossing_signature_free(&changed_geometry);
  puts("identical route and topology-preserving refit: pass");
}

static void test_moved_route_changes_signature(void) {
  pointf crossing_points[] = {{-2, 0}, {-1, 0}, {1, 0}, {2, 0}};
  pointf moved_points[] = {{-2, 3}, {-1, 3}, {1, 3}, {2, 3}};
  pointf foreign_points[] = {{0, -2}, {0, -1}, {0, 1}, {0, 2}};
  bezier crossing_piece = spline(crossing_points);
  bezier moved_piece = spline(moved_points);
  bezier foreign_piece = spline(foreign_points);
  route_rendered_route_t crossing_route = rendered(10, &crossing_piece, 1);
  route_rendered_route_t moved_route = rendered(10, &moved_piece, 1);
  route_rendered_route_t foreign = rendered(11, &foreign_piece, 1);
  route_rendered_route_t crossing_routes[] = {crossing_route, foreign};
  route_rendered_route_t moved_routes[] = {moved_route, foreign};
  route_crossing_signature_t before =
      crossings(&crossing_route, crossing_routes, 2);
  route_crossing_signature_t after = crossings(&moved_route, moved_routes, 2);
  assert(before.size == 1);
  assert(after.size == 0);
  assert(!route_crossing_signatures_equal(&before, &after));
  route_crossing_signature_free(&before);
  route_crossing_signature_free(&after);
  puts("route moved across another route: pass");
}

static void test_crossing_multiplicity(void) {
  pointf target_points[] = {{-2, 0}, {-1, 0}, {1, 0}, {2, 0}};
  pointf first_foreign_points[] = {{0, -2}, {0, -1}, {0, 1}, {0, 2}};
  pointf second_foreign_points[] = {{0, -2}, {0, -1}, {0, 1}, {0, 2}};
  bezier target_piece = spline(target_points);
  bezier foreign_pieces[] = {
      spline(first_foreign_points),
      spline(second_foreign_points),
  };
  route_rendered_route_t target = rendered(12, &target_piece, 1);
  route_rendered_route_t foreign = rendered(13, foreign_pieces, 2);
  route_rendered_route_t routes[] = {target, foreign};
  route_crossing_signature_t signature = crossings(&target, routes, 2);
  assert(signature.size == 1);
  assert(signature.events[0].kind == ROUTE_CROSSING_TRANSVERSE);
  assert(signature.events[0].orientation == 1);
  assert(signature.events[0].multiplicity == 2);
  route_crossing_signature_free(&signature);
  puts("coincident crossing multiplicity: pass");
}

static void assert_all_ambiguous(const route_crossing_signature_t *signature) {
  assert(signature->size > 0);
  for (size_t i = 0; i < signature->size; i++) {
    assert(signature->events[i].kind == ROUTE_CROSSING_AMBIGUOUS);
    assert(signature->events[i].orientation == 0);
  }
}

static void test_ambiguous_geometry(void) {
  pointf trunk_points[] = {{-2, 0}, {-1, 0}, {1, 0}, {2, 0}};
  bezier target_piece = spline(trunk_points);
  bezier shared_piece = spline(trunk_points);
  route_rendered_route_t target = rendered(20, &target_piece, 1);
  route_rendered_route_t shared = rendered(21, &shared_piece, 1);
  route_rendered_route_t shared_routes[] = {target, shared};
  route_crossing_signature_t trunk = crossings(&target, shared_routes, 2);
  assert_all_ambiguous(&trunk);

  pointf tangent_points[] = {
      {-1, 1}, {-1.0 / 3.0, -1.0 / 3.0}, {1.0 / 3.0, -1.0 / 3.0}, {1, 1}};
  bezier tangent_piece = spline(tangent_points);
  route_rendered_route_t tangent = rendered(22, &tangent_piece, 1);
  route_rendered_route_t tangent_routes[] = {target, tangent};
  route_crossing_signature_t tangency = crossings(&target, tangent_routes, 2);
  assert_all_ambiguous(&tangency);

  pointf vertical_points[] = {{0, -2}, {0, -1}, {0, 1}, {0, 2}};
  bezier first_vertical_piece = spline(vertical_points);
  bezier second_vertical_piece = spline(vertical_points);
  route_rendered_route_t first_vertical =
      rendered(23, &first_vertical_piece, 1);
  route_rendered_route_t second_vertical =
      rendered(24, &second_vertical_piece, 1);
  route_rendered_route_t triple_routes[] = {target, first_vertical,
                                            second_vertical};
  route_crossing_signature_t triple = crossings(&target, triple_routes, 3);
  assert(triple.size == 2);
  assert_all_ambiguous(&triple);

  FILE *dump = tmpfile();
  assert(dump != NULL);
  assert(route_crossing_signature_dump(dump, &triple) == 0);
  rewind(dump);
  char buffer[256] = {0};
  assert(fread(buffer, 1, sizeof(buffer) - 1, dump) > 0);
  assert(strstr(buffer, "AMBIGUOUS") != NULL);
  fclose(dump);
  route_crossing_signature_free(&trunk);
  route_crossing_signature_free(&tangency);
  route_crossing_signature_free(&triple);
  puts("shared trunk, tangency, and triple intersection ambiguity: pass");
}

static void test_mirrored_crossing_orientation(void) {
  pointf target_points[] = {{-2, 0}, {-1, 0}, {1, 0}, {2, 0}};
  pointf foreign_points[] = {{0, -2}, {0, -1}, {0, 1}, {0, 2}};
  pointf mirror_target_points[] = {{2, 0}, {1, 0}, {-1, 0}, {-2, 0}};
  pointf mirror_foreign_points[] = {{0, -2}, {0, -1}, {0, 1}, {0, 2}};
  bezier target_piece = spline(target_points);
  bezier foreign_piece = spline(foreign_points);
  bezier mirror_target_piece = spline(mirror_target_points);
  bezier mirror_foreign_piece = spline(mirror_foreign_points);
  route_rendered_route_t target = rendered(30, &target_piece, 1);
  route_rendered_route_t foreign = rendered(31, &foreign_piece, 1);
  route_rendered_route_t mirror_target = rendered(30, &mirror_target_piece, 1);
  route_rendered_route_t mirror_foreign =
      rendered(31, &mirror_foreign_piece, 1);
  route_rendered_route_t routes[] = {target, foreign};
  route_rendered_route_t mirror_routes[] = {mirror_target, mirror_foreign};
  route_crossing_signature_t original = crossings(&target, routes, 2);
  route_crossing_signature_t mirror =
      crossings(&mirror_target, mirror_routes, 2);
  assert(original.size == 1 && mirror.size == 1);
  assert(original.events[0].other_route_id == mirror.events[0].other_route_id);
  assert(original.events[0].kind == mirror.events[0].kind);
  assert(original.events[0].multiplicity == mirror.events[0].multiplicity);
  assert(original.events[0].orientation == -mirror.events[0].orientation);
  route_crossing_signature_free(&original);
  route_crossing_signature_free(&mirror);
  puts("mirrored route orientation: pass");
}

int main(void) {
  test_portal_signature();
  test_identical_and_refit_routes();
  test_moved_route_changes_signature();
  test_crossing_multiplicity();
  test_ambiguous_geometry();
  test_mirrored_crossing_orientation();
  return 0;
}
