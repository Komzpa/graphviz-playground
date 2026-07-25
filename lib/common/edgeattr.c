/// @file
/// @brief Rendered edge identity projection for concentration

/*
 * Concentration projects each edge into two parts: the core rendered identity
 * built here, and the edge's own per-physical-endpoint arrow decorations built
 * in lib/common/arrows.c. Parsed labels and ports are projected directly from
 * the state initialized by common_init_edge(). The residual attribute table is
 * limited to values that later layout or emission code still reads as text.
 * Unrendered attributes contribute no slot, and reverse projection swaps head
 * and tail at extraction time.
 *
 * The arrow comparison deliberately depends on direction. Same-direction
 * parallels merge only when the candidate's own decoration record equals the
 * retained edge's accumulated record. This allows a later edge that renders the
 * same bidirectional arrows to fold, but still keeps an arrow-less candidate
 * separate from a representative that borrowed a reverse arrow. Opposite-
 * direction pairs instead must be COMBINABLE onto one bidirectional route, so
 * the candidate's own decorations are folded against the retained edge's
 * accumulated decorations after swapping physical endpoints. Requiring strict
 * equality there would break arrow-plus-no-arrow merges.
 *
 * Arrow shapes, arrowsize, and fillcolor therefore do not appear in the core
 * signature. The decoration record contains the latter two values only when
 * its edge actually draws an arrow, so invisible declarations on arrow-less
 * endpoints cannot block an opposite-direction merge.
 *
 * The residual classification and hyperlink tables are the raw slot-extractor
 * contract. Parsed state stays out of them so parser defaults and inheritance
 * cannot be reimplemented here.
 */

#include "config.h"

#include <common/attrname.h>
#include <common/colorprocs.h>
#include <common/const.h>
#include <common/edgeattr.h>
#include <common/htmltable.h>
#include <common/render.h>
#include <common/utils.h>
#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <util/agxbuf.h>
#include <util/strcasecmp.h>
#include <util/tokenize.h>

#define ATTRIBUTE_COUNT(attributes)                                            \
  (sizeof(attributes) / sizeof((attributes)[0]))

typedef struct {
  const char *text;
  bool is_html;
} comparable_attribute_value_t;

typedef struct {
  char *text;
} rendered_edge_identity_t;

typedef struct {
  strview_t color;
  double fraction;
} normalized_color_segment_t;

typedef enum {
  ATTRIBUTE_DEFAULT_NONE,
  ATTRIBUTE_DEFAULT_ONE,
  ATTRIBUTE_DEFAULT_LABEL_ANGLE,
  ATTRIBUTE_DEFAULT_FALSE,
} attribute_default_kind_t;

typedef enum {
  ATTRIBUTE_RENDER_ALWAYS,
  ATTRIBUTE_RENDER_MAIN_LABEL,
  ATTRIBUTE_RENDER_PLAIN_PRIMARY_LABEL,
  ATTRIBUTE_RENDER_ENDPOINT_LABEL,
  ATTRIBUTE_RENDER_HYPERLINK,
  ATTRIBUTE_RENDER_LAYOUT_ONLY,
  ATTRIBUTE_RENDER_COLOR_TRANSLATOR,
  ATTRIBUTE_RENDER_ORTHO_EDGE,
  ATTRIBUTE_RENDER_GRAPH_LAYERS,
} attribute_render_scope_t;

typedef struct {
  attribute_default_kind_t default_kind;
  attribute_render_scope_t render_scope;
  bool color : 1;
  bool clipping : 1;
  bool substituted : 1;
  bool style : 1;
  bool compound_only : 1;
  bool presence_affects_rendering : 1;
} edge_attribute_facts_t;

typedef struct {
  attribute_kind_t kind;
  edge_attribute_facts_t facts;
} edge_attribute_kind_t;

typedef struct {
  const char *name;
  edge_attribute_facts_t facts;
  bool endpoint : 1;
  attribute_owner_t owner;
} edge_attribute_exception_t;

typedef struct {
  bool found : 1;
  bool owner_prefixed : 1;
  attribute_identity_t identity;
  const edge_attribute_facts_t *facts;
  const char *name;
  bool endpoint : 1;
} edge_attribute_classification_t;

/*
 * Only residual attributes still consumed as text by layout or emission belong
 * here. Owner-prefixed names are parsed into typed identities below.
 * Parsed labels, fonts, ports, and arrow decorations are projected from their
 * renderer-owned structures instead. Unknown attributes are not rendered and
 * therefore do not contribute identity.
 */
static const edge_attribute_kind_t edge_attribute_kinds[] = {
    {.kind = ATTRIBUTE_KIND_URL,
     .facts = {.render_scope = ATTRIBUTE_RENDER_HYPERLINK}},
    {.kind = ATTRIBUTE_KIND_TOOLTIP,
     .facts = {.render_scope = ATTRIBUTE_RENDER_HYPERLINK,
               .substituted = true}},
    {.kind = ATTRIBUTE_KIND_TARGET,
     .facts = {.render_scope = ATTRIBUTE_RENDER_HYPERLINK}},
    {.kind = ATTRIBUTE_KIND_CLIP, .facts = {.clipping = true}},
};

static const edge_attribute_exception_t edge_attribute_exceptions[] = {
    {.name = "class"},
    {.name = "color", .facts = {.color = true}},
    {.name = "comment"},
    /* emit_end_edge() attaches decorate splines only to label/xlabel. */
    {.name = "decorate",
     .facts = {.default_kind = ATTRIBUTE_DEFAULT_FALSE,
               .render_scope = ATTRIBUTE_RENDER_MAIN_LABEL}},
    {.name = "id", .facts = {.substituted = true}},
    {.name = "labelaligned",
     .facts = {.default_kind = ATTRIBUTE_DEFAULT_FALSE,
               .render_scope = ATTRIBUTE_RENDER_PLAIN_PRIMARY_LABEL}},
    /* place_portlabel() reads these only for headlabel/taillabel. */
    {.name = "labelangle",
     .facts = {.default_kind = ATTRIBUTE_DEFAULT_LABEL_ANGLE,
               .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL}},
    {.name = "labeldistance",
     .facts = {.default_kind = ATTRIBUTE_DEFAULT_ONE,
               .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL}},
    {.name = "layer", .facts = {.render_scope = ATTRIBUTE_RENDER_GRAPH_LAYERS}},
    {.name = "lhead",
     .facts = {.compound_only = true},
     .endpoint = true,
     .owner = ATTRIBUTE_OWNER_HEAD},
    {.name = "ltail",
     .facts = {.compound_only = true},
     .endpoint = true,
     .owner = ATTRIBUTE_OWNER_TAIL},
    {.name = "penwidth", .facts = {.default_kind = ATTRIBUTE_DEFAULT_ONE}},
    {.name = "radius", .facts = {.render_scope = ATTRIBUTE_RENDER_ORTHO_EDGE}},
    {.name = "samehead", .endpoint = true, .owner = ATTRIBUTE_OWNER_HEAD},
    {.name = "sametail", .endpoint = true, .owner = ATTRIBUTE_OWNER_TAIL},
    {.name = "showboxes"},
    {.name = "style", .facts = {.style = true}},

    /* Translators and layout-only attributes do not emit identity slots. */
    {.name = "colorscheme",
     .facts = {.render_scope = ATTRIBUTE_RENDER_COLOR_TRANSLATOR}},
    {.name = "constraint",
     .facts = {.render_scope = ATTRIBUTE_RENDER_LAYOUT_ONLY}},
    {.name = "minlen", .facts = {.render_scope = ATTRIBUTE_RENDER_LAYOUT_ONLY}},
    {.name = "weight", .facts = {.render_scope = ATTRIBUTE_RENDER_LAYOUT_ONLY}},
};

typedef enum {
  EDGE_TAIL_ENDPOINT,
  EDGE_HEAD_ENDPOINT,
  EDGE_ENDPOINT_COUNT,
} edge_endpoint_t;

static const edge_attribute_facts_t *
edge_attribute_kind_facts(attribute_kind_t kind) {
  for (size_t i = 0; i < ATTRIBUTE_COUNT(edge_attribute_kinds); i++) {
    if (edge_attribute_kinds[i].kind == kind) {
      return &edge_attribute_kinds[i].facts;
    }
  }
  return NULL;
}

static edge_attribute_classification_t
edge_attribute_classification(const char *name) {
  attribute_identity_t identity;
  if (parse_owner_prefixed_attribute_name(name, &identity)) {
    const edge_attribute_facts_t *const facts =
        edge_attribute_kind_facts(identity.kind);
    if (facts == NULL || (identity.kind == ATTRIBUTE_KIND_CLIP &&
                          identity.owner != ATTRIBUTE_OWNER_HEAD &&
                          identity.owner != ATTRIBUTE_OWNER_TAIL)) {
      return (edge_attribute_classification_t){0};
    }
    return (edge_attribute_classification_t){
        .found = true,
        .owner_prefixed = true,
        .identity = identity,
        .facts = facts,
        .name = name,
        .endpoint = identity.owner == ATTRIBUTE_OWNER_HEAD ||
                    identity.owner == ATTRIBUTE_OWNER_TAIL,
    };
  }

  for (size_t i = 0; i < ATTRIBUTE_COUNT(edge_attribute_exceptions); i++) {
    const edge_attribute_exception_t *const exception =
        &edge_attribute_exceptions[i];
    if (strcmp(name, exception->name) == 0) {
      return (edge_attribute_classification_t){
          .found = true,
          .identity = {.owner = exception->owner},
          .facts = &exception->facts,
          .name = exception->name,
          .endpoint = exception->endpoint,
      };
    }
  }
  return (edge_attribute_classification_t){0};
}

static comparable_attribute_value_t plain_attribute_value(const char *text) {
  return (comparable_attribute_value_t){.text = text, .is_html = false};
}

static comparable_attribute_value_t
named_attribute_value(Agraph_t *root_graph, Agedge_t *edge,
                      const char *attribute_name) {
  Agsym_t *const attribute = agfindedgeattr(root_graph, (char *)attribute_name);
  if (attribute == NULL) {
    /* aghtmlstr() accepts Cgraph refstrings, not this empty literal. */
    return plain_attribute_value("");
  }
  const char *const text = agxget(edge, attribute);
  return (comparable_attribute_value_t){
      .text = text,
      .is_html = aghtmlstr(text),
  };
}

static void append_signature_slot(agxbuf *signature, const char *slot_name,
                                  comparable_attribute_value_t value) {
  /* Length prefixes make arbitrary attribute bytes unambiguous. */
  agxbprint(signature, "%zu:", strlen(slot_name));
  agxbput(signature, slot_name);
  agxbprint(signature, "=%c%zu:", value.is_html ? 'h' : 'p',
            strlen(value.text));
  agxbput(signature, value.text);
  agxbputc(signature, ';');
}

static void append_plain_signature_slot(agxbuf *signature,
                                        const char *slot_name,
                                        const char *value) {
  append_signature_slot(signature, slot_name, plain_attribute_value(value));
}

static void append_substituted_signature_slot(agxbuf *signature,
                                              const char *slot_name,
                                              const char *value,
                                              Agedge_t *edge) {
  char *const substituted = strdup_and_subst_obj((char *)value, edge);
  append_plain_signature_slot(signature, slot_name, substituted);
  free(substituted);
}

static void append_nullable_plain_field(agxbuf *signature,
                                        const char *field_name,
                                        const char *value) {
  append_plain_signature_slot(signature, field_name,
                              value == NULL ? "" : value);
}

static void append_edge_color_value(agxbuf *signature, Agedge_t *edge,
                                    const char *slot_name,
                                    comparable_attribute_value_t value,
                                    bool allow_color_list,
                                    bool reverse_orientation);
static void append_color_segment(agxbuf *rendered_list, strview_t color_name);

enum {
  DEFAULT_HTML_BORDER = 1,
  DEFAULT_HTML_CELLPADDING = 2,
  DEFAULT_HTML_CELLSPACING = 2,
};

