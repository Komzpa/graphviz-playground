/// @file
/// @brief Typed attribute name identities

#include "config.h"

#include <common/attrname.h>
#include <stddef.h>
#include <string.h>

typedef struct {
  const char *prefix;
  attribute_owner_t owner;
} attribute_owner_prefix_t;

typedef struct {
  const char *canonical_stem;
  const char *alias_stem;
  attribute_kind_t kind;
} attribute_kind_name_t;

static const attribute_owner_prefix_t attribute_owner_prefixes[] = {
    {.prefix = "edge", .owner = ATTRIBUTE_OWNER_EDGE},
    {.prefix = "label", .owner = ATTRIBUTE_OWNER_LABEL},
    {.prefix = "head", .owner = ATTRIBUTE_OWNER_HEAD},
    {.prefix = "tail", .owner = ATTRIBUTE_OWNER_TAIL},
};

static const attribute_kind_name_t attribute_kind_names[] = {
    {.canonical_stem = "URL", .alias_stem = "href", .kind = ATTRIBUTE_KIND_URL},
    {.canonical_stem = "tooltip", .kind = ATTRIBUTE_KIND_TOOLTIP},
    {.canonical_stem = "target", .kind = ATTRIBUTE_KIND_TARGET},
    {.canonical_stem = "clip", .kind = ATTRIBUTE_KIND_CLIP},
};

static bool parse_attribute_kind(const char *stem, attribute_kind_t *kind) {
  for (size_t i = 0;
       i < sizeof(attribute_kind_names) / sizeof(attribute_kind_names[0]);
       i++) {
    const attribute_kind_name_t *const name = &attribute_kind_names[i];
    if (strcmp(stem, name->canonical_stem) == 0 ||
        (name->alias_stem != NULL && strcmp(stem, name->alias_stem) == 0)) {
      *kind = name->kind;
      return true;
    }
  }
  return false;
}

bool parse_composed_attribute_name(const char *name,
                                   attribute_identity_t *identity) {
  for (size_t i = 0; i < sizeof(attribute_owner_prefixes) /
                             sizeof(attribute_owner_prefixes[0]);
       i++) {
    const attribute_owner_prefix_t *const prefix = &attribute_owner_prefixes[i];
    const size_t prefix_length = strlen(prefix->prefix);
    if (strncmp(name, prefix->prefix, prefix_length) == 0) {
      attribute_kind_t kind;
      if (parse_attribute_kind(name + prefix_length, &kind)) {
        *identity =
            (attribute_identity_t){.owner = prefix->owner, .kind = kind};
        return true;
      }
    }
  }

  attribute_kind_t kind;
  if (parse_attribute_kind(name, &kind)) {
    *identity =
        (attribute_identity_t){.owner = ATTRIBUTE_OWNER_EDGE, .kind = kind};
    return true;
  }
  return false;
}

const char *attribute_owner_prefix(attribute_owner_t owner) {
  switch (owner) {
  case ATTRIBUTE_OWNER_EDGE:
    return "edge";
  case ATTRIBUTE_OWNER_LABEL:
    return "label";
  case ATTRIBUTE_OWNER_HEAD:
    return "head";
  case ATTRIBUTE_OWNER_TAIL:
    return "tail";
  case ATTRIBUTE_OWNER_COUNT:
    return "";
  }
  return "";
}

const char *attribute_kind_canonical_stem(attribute_kind_t kind) {
  switch (kind) {
  case ATTRIBUTE_KIND_URL:
    return "URL";
  case ATTRIBUTE_KIND_TOOLTIP:
    return "tooltip";
  case ATTRIBUTE_KIND_TARGET:
    return "target";
  case ATTRIBUTE_KIND_CLIP:
    return "clip";
  case ATTRIBUTE_KIND_COUNT:
    return "";
  }
  return "";
}

const char *attribute_kind_url_alias_stem(attribute_kind_t kind) {
  return kind == ATTRIBUTE_KIND_URL ? "href" : NULL;
}
