#pragma once

#include <common/types.h>
#include <pathplan/pathgeom.h>

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

typedef enum {
  ROUTE_CERT_OK,
  ROUTE_CERT_INVALID_INPUT,
  ROUTE_CERT_OUT_OF_MEMORY,
} route_cert_status_t;

typedef struct {
  /* Oriented overlap edges between consecutive routesplines corridor boxes. */
  Pedge_t *portals;
  size_t size;
} route_portal_signature_t;

typedef struct {
  /* Stable identity assigned to one installed rendered route by the caller. */
  uint64_t id;
  /* Post-routing Bezier pieces, in drawing order from route start to end. */
  const bezier *pieces;
  size_t piece_count;
} route_rendered_route_t;

typedef enum {
  ROUTE_CROSSING_TRANSVERSE,
  ROUTE_CROSSING_AMBIGUOUS,
} route_crossing_kind_t;

typedef struct {
  uint64_t other_route_id;
  /* Sign of target tangent cross foreign tangent; zero means ambiguous. */
  int orientation;
  /* Coincident hits of the same foreign rendered route at this event. */
  size_t multiplicity;
  route_crossing_kind_t kind;
} route_crossing_event_t;

typedef struct {
  route_crossing_event_t *events;
  size_t size;
} route_crossing_signature_t;

route_cert_status_t route_portal_signature(const Pedge_t *portals,
                                           size_t portal_count,
                                           size_t corridor_count,
                                           route_portal_signature_t *signature);
void route_portal_signature_free(route_portal_signature_t *signature);
bool route_portal_signatures_equal(const route_portal_signature_t *left,
                                   const route_portal_signature_t *right);

route_cert_status_t
route_crossing_signature(const route_rendered_route_t *route,
                         const route_rendered_route_t *rendered_routes,
                         size_t rendered_route_count,
                         route_crossing_signature_t *signature);
void route_crossing_signature_free(route_crossing_signature_t *signature);
bool route_crossing_signatures_equal(const route_crossing_signature_t *left,
                                     const route_crossing_signature_t *right);

int route_portal_signature_dump(FILE *stream,
                                const route_portal_signature_t *signature);
int route_crossing_signature_dump(FILE *stream,
                                  const route_crossing_signature_t *signature);
