/// @file
/// @brief Typed attribute name identities

#pragma once

#include <stdbool.h>

typedef enum {
  ATTRIBUTE_OWNER_EDGE,
  ATTRIBUTE_OWNER_LABEL,
  ATTRIBUTE_OWNER_HEAD,
  ATTRIBUTE_OWNER_TAIL,
  ATTRIBUTE_OWNER_COUNT,
} attribute_owner_t;

typedef enum {
  ATTRIBUTE_KIND_URL,
  ATTRIBUTE_KIND_TOOLTIP,
  ATTRIBUTE_KIND_TARGET,
  ATTRIBUTE_KIND_CLIP,
  ATTRIBUTE_KIND_COUNT,
} attribute_kind_t;

typedef struct {
  attribute_owner_t owner;
  attribute_kind_t kind;
} attribute_identity_t;

bool parse_owner_prefixed_attribute_name(const char *name,
                                         attribute_identity_t *identity);
const char *attribute_owner_prefix(attribute_owner_t owner);
const char *attribute_kind_canonical_stem(attribute_kind_t kind);
const char *attribute_kind_url_alias_stem(attribute_kind_t kind);
