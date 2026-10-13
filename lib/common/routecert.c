#include <common/routecert.h>

#include <inttypes.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define ROUTE_CERT_MAX_DEPTH 18u
#define ROUTE_CERT_EPSILON 1e-7

typedef struct {
  pointf point;
  double t;
  double error;
} route_flat_point_t;

typedef struct {
  route_flat_point_t *items;
  size_t size;
  size_t capacity;
} route_flat_points_t;

typedef struct {
  size_t target_piece;
  size_t target_cubic;
  double target_t;
  pointf point;
  uint64_t other_route_id;
  size_t other_piece;
  size_t other_cubic;
  int orientation;
  route_crossing_kind_t kind;
} route_raw_crossing_t;

typedef struct {
  route_raw_crossing_t *items;
  size_t size;
  size_t capacity;
} route_raw_crossings_t;

static bool finite_point(pointf position) {
  return isfinite(position.x) && isfinite(position.y);
}

static pointf point_subtract(pointf left, pointf right) {
  return (pointf){left.x - right.x, left.y - right.y};
}

static pointf point_lerp(pointf start, pointf end, double t) {
  return (pointf){start.x + t * (end.x - start.x),
                  start.y + t * (end.y - start.y)};
}

static double cross_product(pointf left, pointf right) {
  return left.x * right.y - left.y * right.x;
}

static double point_distance_squared(pointf left, pointf right) {
  const pointf delta = point_subtract(left, right);
  return delta.x * delta.x + delta.y * delta.y;
}

static double point_segment_distance(pointf position, pointf start,
                                     pointf end) {
  const pointf segment = point_subtract(end, start);
  const double length_squared = segment.x * segment.x + segment.y * segment.y;
  if (length_squared == 0.0)
    return sqrt(point_distance_squared(position, start));
  const pointf offset = point_subtract(position, start);
  const double projection =
      fmax(0.0, fmin(1.0, (offset.x * segment.x + offset.y * segment.y) /
                              length_squared));
  return sqrt(
      point_distance_squared(position, point_lerp(start, end, projection)));
}

static double segment_distance(pointf a, pointf b, pointf c, pointf d) {
  return fmin(
      fmin(point_segment_distance(a, c, d), point_segment_distance(b, c, d)),
      fmin(point_segment_distance(c, a, b), point_segment_distance(d, a, b)));
}

static bool append_flat_point(route_flat_points_t *points,
                              route_flat_point_t sample) {
  if (points->size == points->capacity) {
    const size_t capacity = points->capacity == 0 ? 16 : points->capacity * 2;
    if (capacity < points->capacity ||
        capacity > SIZE_MAX / sizeof(*points->items))
      return false;
    route_flat_point_t *items =
        realloc(points->items, capacity * sizeof(*items));
    if (items == NULL)
      return false;
    points->items = items;
    points->capacity = capacity;
  }
  points->items[points->size++] = sample;
  return true;
}

static bool append_raw_crossing(route_raw_crossings_t *crossings,
                                route_raw_crossing_t crossing) {
  if (crossings->size == crossings->capacity) {
    const size_t capacity =
        crossings->capacity == 0 ? 16 : crossings->capacity * 2;
    if (capacity < crossings->capacity ||
        capacity > SIZE_MAX / sizeof(*crossings->items))
      return false;
    route_raw_crossing_t *items =
        realloc(crossings->items, capacity * sizeof(*items));
    if (items == NULL)
      return false;
    crossings->items = items;
    crossings->capacity = capacity;
  }
  crossings->items[crossings->size++] = crossing;
  return true;
}

static double cubic_flatness(const pointf control[4]) {
  return fmax(point_segment_distance(control[1], control[0], control[3]),
              point_segment_distance(control[2], control[0], control[3]));
}

static void split_cubic(const pointf control[4], pointf left[4],
                        pointf right[4]) {
  const pointf p01 = point_lerp(control[0], control[1], 0.5);
  const pointf p12 = point_lerp(control[1], control[2], 0.5);
  const pointf p23 = point_lerp(control[2], control[3], 0.5);
  const pointf p012 = point_lerp(p01, p12, 0.5);
  const pointf p123 = point_lerp(p12, p23, 0.5);
  const pointf middle = point_lerp(p012, p123, 0.5);
  memcpy(left, (pointf[]){control[0], p01, p012, middle}, sizeof(pointf[4]));
  memcpy(right, (pointf[]){middle, p123, p23, control[3]}, sizeof(pointf[4]));
}

static bool flatten_cubic_recursive(const pointf control[4], double t0,
                                    double t1, unsigned depth,
                                    route_flat_points_t *points) {
  const double scale =
      fmax(1.0, sqrt(point_distance_squared(control[0], control[3])));
  const double flatness = cubic_flatness(control);
  if (flatness <= ROUTE_CERT_EPSILON * scale || depth == ROUTE_CERT_MAX_DEPTH) {
    return append_flat_point(points, (route_flat_point_t){
                                         .point = control[3],
                                         .t = t1,
                                         .error = flatness,
                                     });
  }
  pointf left[4];
  pointf right[4];
  split_cubic(control, left, right);
  const double middle = (t0 + t1) / 2.0;
  return flatten_cubic_recursive(left, t0, middle, depth + 1, points) &&
         flatten_cubic_recursive(right, middle, t1, depth + 1, points);
}

static bool flatten_cubic(const pointf control[4],
                          route_flat_points_t *points) {
  return append_flat_point(points,
                           (route_flat_point_t){
                               .point = control[0],
                               .t = 0.0,
                               .error = 0.0,
                           }) &&
         flatten_cubic_recursive(control, 0.0, 1.0, 0, points);
}

static bool rendered_route_valid(const route_rendered_route_t *route) {
  if (route == NULL || (route->piece_count != 0 && route->pieces == NULL))
    return false;
  for (size_t piece = 0; piece < route->piece_count; piece++) {
    const bezier *spline = &route->pieces[piece];
    if (spline->size < 4 || (spline->size - 1) % 3 != 0 || spline->list == NULL)
      return false;
    for (size_t point_index = 0; point_index < spline->size; point_index++) {
      if (!finite_point(spline->list[point_index]))
        return false;
    }
  }
  return true;
}

typedef enum {
  SEGMENTS_DISJOINT,
  SEGMENTS_TRANSVERSE,
  SEGMENTS_AMBIGUOUS,
} segment_relation_t;

static segment_relation_t
segment_relation(route_flat_point_t a, route_flat_point_t b,
                 route_flat_point_t c, route_flat_point_t d, double *target_t,
                 pointf *intersection, int *orientation) {
  const pointf ab = point_subtract(b.point, a.point);
  const pointf cd = point_subtract(d.point, c.point);
  const double ab_length = hypot(ab.x, ab.y);
  const double cd_length = hypot(cd.x, cd.y);
  const double uncertainty =
      a.error + b.error + c.error + d.error + ROUTE_CERT_EPSILON;
  if (ab_length <= ROUTE_CERT_EPSILON || cd_length <= ROUTE_CERT_EPSILON) {
    if (segment_distance(a.point, b.point, c.point, d.point) > uncertainty)
      return SEGMENTS_DISJOINT;
    *target_t = (a.t + b.t) / 2.0;
    *intersection = point_lerp(a.point, b.point, 0.5);
    *orientation = 0;
    return SEGMENTS_AMBIGUOUS;
  }

  const double denominator = cross_product(ab, cd);
  const double parallel_tolerance =
      1e-10 * ab_length * cd_length + ROUTE_CERT_EPSILON;
  if (fabs(denominator) <= parallel_tolerance) {
    if (segment_distance(a.point, b.point, c.point, d.point) > uncertainty)
      return SEGMENTS_DISJOINT;
    *target_t = (a.t + b.t) / 2.0;
    *intersection = point_lerp(a.point, b.point, 0.5);
    *orientation = 0;
    return SEGMENTS_AMBIGUOUS;
  }

  const pointf ac = point_subtract(c.point, a.point);
  const double along_ab = cross_product(ac, cd) / denominator;
  const double along_cd = cross_product(ac, ab) / denominator;
  const double ab_margin = uncertainty / ab_length;
  const double cd_margin = uncertainty / cd_length;
  if (along_ab < -ab_margin || along_ab > 1.0 + ab_margin ||
      along_cd < -cd_margin || along_cd > 1.0 + cd_margin)
    return SEGMENTS_DISJOINT;

  const double clamped_ab = fmax(0.0, fmin(1.0, along_ab));
  *target_t = a.t + clamped_ab * (b.t - a.t);
  *intersection = point_lerp(a.point, b.point, clamped_ab);
  *orientation = denominator > 0.0 ? 1 : -1;
  return SEGMENTS_TRANSVERSE;
}

static route_cert_status_t
collect_cubic_crossings(const pointf target_control[4], size_t target_piece,
                        size_t target_cubic, const pointf other_control[4],
                        const route_rendered_route_t *other, size_t other_piece,
                        size_t other_cubic, route_raw_crossings_t *crossings) {
  route_flat_points_t target = {0};
  route_flat_points_t foreign = {0};
  if (!flatten_cubic(target_control, &target) ||
      !flatten_cubic(other_control, &foreign)) {
    free(target.items);
    free(foreign.items);
    return ROUTE_CERT_OUT_OF_MEMORY;
  }
  for (size_t ti = 0; ti + 1 < target.size; ti++) {
    for (size_t oi = 0; oi + 1 < foreign.size; oi++) {
      double target_t;
      pointf intersection;
      int orientation;
      const segment_relation_t relation = segment_relation(
          target.items[ti], target.items[ti + 1], foreign.items[oi],
          foreign.items[oi + 1], &target_t, &intersection, &orientation);
      if (relation == SEGMENTS_DISJOINT)
        continue;
      if (!append_raw_crossing(crossings,
                               (route_raw_crossing_t){
                                   .target_piece = target_piece,
                                   .target_cubic = target_cubic,
                                   .target_t = target_t,
                                   .point = intersection,
                                   .other_route_id = other->id,
                                   .other_piece = other_piece,
                                   .other_cubic = other_cubic,
                                   .orientation = orientation,
                                   .kind = relation == SEGMENTS_TRANSVERSE
                                               ? ROUTE_CROSSING_TRANSVERSE
                                               : ROUTE_CROSSING_AMBIGUOUS,
                               })) {
        free(target.items);
        free(foreign.items);
        return ROUTE_CERT_OUT_OF_MEMORY;
      }
    }
  }
  free(target.items);
  free(foreign.items);
  return ROUTE_CERT_OK;
}

static int compare_raw_crossings(const void *left_pointer,
                                 const void *right_pointer) {
  const route_raw_crossing_t *left = left_pointer;
  const route_raw_crossing_t *right = right_pointer;
#define COMPARE_FIELD(field)                                                   \
  do {                                                                         \
    if (left->field < right->field)                                            \
      return -1;                                                               \
    if (left->field > right->field)                                            \
      return 1;                                                                \
  } while (0)
  COMPARE_FIELD(target_piece);
  COMPARE_FIELD(target_cubic);
  if (left->target_t < right->target_t)
    return -1;
  if (left->target_t > right->target_t)
    return 1;
  COMPARE_FIELD(other_route_id);
  COMPARE_FIELD(other_piece);
  COMPARE_FIELD(other_cubic);
#undef COMPARE_FIELD
  return 0;
}

static bool same_crossing_position(const route_raw_crossing_t *left,
                                   const route_raw_crossing_t *right) {
  return left->target_piece == right->target_piece &&
         left->target_cubic == right->target_cubic &&
         fabs(left->target_t - right->target_t) <= 1e-5 &&
         point_distance_squared(left->point, right->point) <= 1e-8;
}

static bool append_crossing_event(route_crossing_signature_t *signature,
                                  route_crossing_event_t event) {
  if (signature->size == SIZE_MAX / sizeof(*signature->events))
    return false;
  route_crossing_event_t *events = realloc(
      signature->events, (signature->size + 1) * sizeof(*signature->events));
  if (events == NULL)
    return false;
  signature->events = events;
  signature->events[signature->size++] = event;
  return true;
}

static route_cert_status_t collapse_crossings(route_raw_crossings_t *raw,
                                              route_crossing_signature_t *out) {
  if (raw->size > 1)
    qsort(raw->items, raw->size, sizeof(*raw->items), compare_raw_crossings);
  size_t position_begin = 0;
  while (position_begin < raw->size) {
    size_t position_end = position_begin + 1;
    while (position_end < raw->size &&
           same_crossing_position(&raw->items[position_begin],
                                  &raw->items[position_end]))
      position_end++;

    size_t distinct_routes = 0;
    uint64_t previous_route = 0;
    for (size_t i = position_begin; i < position_end; i++) {
      if (i == position_begin ||
          raw->items[i].other_route_id != previous_route) {
        distinct_routes++;
        previous_route = raw->items[i].other_route_id;
      }
    }

    size_t route_begin = position_begin;
    while (route_begin < position_end) {
      const uint64_t route_id = raw->items[route_begin].other_route_id;
      size_t route_end = route_begin + 1;
      while (route_end < position_end &&
             raw->items[route_end].other_route_id == route_id)
        route_end++;
      route_crossing_kind_t kind = distinct_routes > 1
                                       ? ROUTE_CROSSING_AMBIGUOUS
                                       : ROUTE_CROSSING_TRANSVERSE;
      int orientation = raw->items[route_begin].orientation;
      size_t multiplicity = 0;
      size_t prior_piece = SIZE_MAX;
      size_t prior_cubic = SIZE_MAX;
      for (size_t i = route_begin; i < route_end; i++) {
        const route_raw_crossing_t *event = &raw->items[i];
        if (event->kind == ROUTE_CROSSING_AMBIGUOUS ||
            (orientation != 0 && event->orientation != 0 &&
             orientation != event->orientation))
          kind = ROUTE_CROSSING_AMBIGUOUS;
        if (event->other_piece != prior_piece ||
            event->other_cubic != prior_cubic) {
          multiplicity++;
          prior_piece = event->other_piece;
          prior_cubic = event->other_cubic;
        }
      }
      if (!append_crossing_event(
              out, (route_crossing_event_t){
                       .other_route_id = route_id,
                       .orientation =
                           kind == ROUTE_CROSSING_TRANSVERSE ? orientation : 0,
                       .multiplicity = multiplicity == 0 ? 1 : multiplicity,
                       .kind = kind,
                   }))
        return ROUTE_CERT_OUT_OF_MEMORY;
      route_begin = route_end;
    }
    position_begin = position_end;
  }
  return ROUTE_CERT_OK;
}

route_cert_status_t
route_portal_signature(const Pedge_t *portals, size_t portal_count,
                       size_t corridor_count,
                       route_portal_signature_t *signature) {
  if (signature == NULL || (portal_count != 0 && portals == NULL) ||
      (corridor_count == 0 && portal_count != 0) ||
      (corridor_count != 0 && portal_count + 1 != corridor_count) ||
      portal_count > SIZE_MAX / sizeof(*signature->portals))
    return ROUTE_CERT_INVALID_INPUT;
  *signature = (route_portal_signature_t){0};
  if (portal_count == 0)
    return ROUTE_CERT_OK;
  for (size_t i = 0; i < portal_count; i++) {
    if (!finite_point(portals[i].a) || !finite_point(portals[i].b))
      return ROUTE_CERT_INVALID_INPUT;
  }
  signature->portals = malloc(portal_count * sizeof(*signature->portals));
  if (signature->portals == NULL)
    return ROUTE_CERT_OUT_OF_MEMORY;
  memcpy(signature->portals, portals,
         portal_count * sizeof(*signature->portals));
  signature->size = portal_count;
  return ROUTE_CERT_OK;
}

void route_portal_signature_free(route_portal_signature_t *signature) {
  if (signature == NULL)
    return;
  free(signature->portals);
  *signature = (route_portal_signature_t){0};
}

bool route_portal_signatures_equal(const route_portal_signature_t *left,
                                   const route_portal_signature_t *right) {
  if (left == NULL || right == NULL || left->size != right->size ||
      (left->size != 0 && (left->portals == NULL || right->portals == NULL)))
    return false;
  for (size_t i = 0; i < left->size; i++) {
    if (left->portals[i].a.x != right->portals[i].a.x ||
        left->portals[i].a.y != right->portals[i].a.y ||
        left->portals[i].b.x != right->portals[i].b.x ||
        left->portals[i].b.y != right->portals[i].b.y)
      return false;
  }
  return true;
}

route_cert_status_t
route_crossing_signature(const route_rendered_route_t *route,
                         const route_rendered_route_t *rendered_routes,
                         size_t rendered_route_count,
                         route_crossing_signature_t *signature) {
  if (!rendered_route_valid(route) || signature == NULL ||
      (rendered_route_count != 0 && rendered_routes == NULL))
    return ROUTE_CERT_INVALID_INPUT;
  *signature = (route_crossing_signature_t){0};
  for (size_t i = 0; i < rendered_route_count; i++) {
    if (!rendered_route_valid(&rendered_routes[i]))
      return ROUTE_CERT_INVALID_INPUT;
    for (size_t j = i + 1; j < rendered_route_count; j++) {
      if (rendered_routes[i].id == rendered_routes[j].id)
        return ROUTE_CERT_INVALID_INPUT;
    }
  }

  route_raw_crossings_t raw = {0};
  route_cert_status_t status = ROUTE_CERT_OK;
  for (size_t target_piece = 0; target_piece < route->piece_count;
       target_piece++) {
    const bezier *target_spline = &route->pieces[target_piece];
    for (size_t target_start = 0; target_start + 3 < target_spline->size;
         target_start += 3) {
      for (size_t other_index = 0; other_index < rendered_route_count;
           other_index++) {
        const route_rendered_route_t *other = &rendered_routes[other_index];
        if (other->id == route->id)
          continue;
        for (size_t other_piece = 0; other_piece < other->piece_count;
             other_piece++) {
          const bezier *other_spline = &other->pieces[other_piece];
          for (size_t other_start = 0; other_start + 3 < other_spline->size;
               other_start += 3) {
            status = collect_cubic_crossings(
                &target_spline->list[target_start], target_piece,
                target_start / 3, &other_spline->list[other_start], other,
                other_piece, other_start / 3, &raw);
            if (status != ROUTE_CERT_OK)
              goto done;
          }
        }
      }
    }
  }
  status = collapse_crossings(&raw, signature);

done:
  free(raw.items);
  if (status != ROUTE_CERT_OK)
    route_crossing_signature_free(signature);
  return status;
}

void route_crossing_signature_free(route_crossing_signature_t *signature) {
  if (signature == NULL)
    return;
  free(signature->events);
  *signature = (route_crossing_signature_t){0};
}

bool route_crossing_signatures_equal(const route_crossing_signature_t *left,
                                     const route_crossing_signature_t *right) {
  if (left == NULL || right == NULL || left->size != right->size ||
      (left->size != 0 && (left->events == NULL || right->events == NULL)))
    return false;
  for (size_t i = 0; i < left->size; i++) {
    const route_crossing_event_t a = left->events[i];
    const route_crossing_event_t b = right->events[i];
    if (a.other_route_id != b.other_route_id ||
        a.orientation != b.orientation || a.multiplicity != b.multiplicity ||
        a.kind != b.kind)
      return false;
  }
  return true;
}

int route_portal_signature_dump(FILE *stream,
                                const route_portal_signature_t *signature) {
  if (stream == NULL || signature == NULL ||
      (signature->size != 0 && signature->portals == NULL))
    return -1;
  for (size_t i = 0; i < signature->size; i++) {
    const Pedge_t portal = signature->portals[i];
    if (fprintf(stream, "portal[%zu]=(%g,%g)->(%g,%g)\n", i, portal.a.x,
                portal.a.y, portal.b.x, portal.b.y) < 0)
      return -1;
  }
  return 0;
}

int route_crossing_signature_dump(FILE *stream,
                                  const route_crossing_signature_t *signature) {
  if (stream == NULL || signature == NULL ||
      (signature->size != 0 && signature->events == NULL))
    return -1;
  for (size_t i = 0; i < signature->size; i++) {
    const route_crossing_event_t event = signature->events[i];
    const char *kind =
        event.kind == ROUTE_CROSSING_TRANSVERSE ? "TRANSVERSE" : "AMBIGUOUS";
    if (fprintf(stream,
                "crossing[%zu]=route:%" PRIu64
                " kind:%s sign:%+d multiplicity:%zu\n",
                i, event.other_route_id, kind, event.orientation,
                event.multiplicity) < 0)
      return -1;
  }
  return 0;
}
