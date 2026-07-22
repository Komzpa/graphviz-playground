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
               .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL,
               .presence_affects_rendering = true}},
    {.name = "labeldistance",
     .facts = {.default_kind = ATTRIBUTE_DEFAULT_ONE,
               .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL,
               .presence_affects_rendering = true}},
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

static void append_substituted_html_signature_slot(agxbuf *signature,
                                                   const char *slot_name,
                                                   const char *value,
                                                   Agedge_t *edge) {
  char *const substituted = strdup_and_subst_obj((char *)value, edge);
  append_signature_slot(
      signature, slot_name,
      (comparable_attribute_value_t){.text = substituted, .is_html = true});
  free(substituted);
}

static void append_nullable_plain_field(agxbuf *signature,
                                        const char *field_name,
                                        const char *value) {
  append_plain_signature_slot(signature, field_name,
                              value == NULL ? "" : value);
}

static void append_html_data_identity_slots(agxbuf *signature,
                                            const char *slot_prefix,
                                            const htmldata_t *data) {
  agxbuf field = {0};
  agxbprint(&field, "%s:border", slot_prefix);
  agxbuf rendered_number = {0};
  agxbprint(&rendered_number, "%u", data->border);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbclear(&rendered_number);

  agxbclear(&field);
  agxbprint(&field, "%s:cellborder-set", slot_prefix);
  append_plain_signature_slot(signature, agxbuse(&field),
                              (data->flags & BORDER_SET) ? "true" : "false");

  agxbclear(&field);
  agxbprint(&field, "%s:pencolor", slot_prefix);
  append_nullable_plain_field(signature, agxbuse(&field), data->pencolor);

  agxbclear(&field);
  agxbprint(&field, "%s:bgcolor", slot_prefix);
  append_nullable_plain_field(signature, agxbuse(&field), data->bgcolor);

  agxbclear(&field);
  agxbprint(&field, "%s:gradientangle", slot_prefix);
  agxbprint(&rendered_number, "%d", data->gradientangle);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbclear(&rendered_number);

  agxbclear(&field);
  agxbprint(&field, "%s:sides", slot_prefix);
  agxbprint(&rendered_number, "%u", data->sides);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbclear(&rendered_number);

  agxbclear(&field);
  agxbprint(&field, "%s:layout", slot_prefix);
  agxbprint(&rendered_number, "%d:%u:%hhu:%hhu:%hu:%hu", data->space,
            data->border, data->pad, data->sides, data->width, data->height);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbclear(&rendered_number);

  agxbclear(&field);
  agxbprint(&field, "%s:flags", slot_prefix);
  agxbprint(&rendered_number, "%hu", data->flags);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbclear(&rendered_number);

  agxbclear(&field);
  agxbprint(&field, "%s:style", slot_prefix);
  agxbprint(&rendered_number, "%d:%d:%d:%d:%d", data->style.radial,
            data->style.rounded, data->style.invisible, data->style.dotted,
            data->style.dashed);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbfree(&rendered_number);
  agxbfree(&field);
}

static void append_html_font_color(agxbuf *rendered_number, Agedge_t *edge,
                                   const char *color_name) {
  if (color_name == NULL || color_name[0] == '\0') {
    return;
  }

  char *const previous_color_scheme =
      setColorScheme(agget(edge, "colorscheme"));
  gvcolor_t color;
  const int result = colorxlate(color_name, &color, RGBA_BYTE);
  char *const restored_color_scheme = setColorScheme(previous_color_scheme);
  free(previous_color_scheme);
  free(restored_color_scheme);

  if (result == COLOR_OK) {
    agxbprint(rendered_number, "#%02x%02x%02x%02x", color.u.rgba[0],
              color.u.rgba[1], color.u.rgba[2], color.u.rgba[3]);
  } else {
    agxbput(rendered_number, color_name);
  }
}

static void append_html_label_identity_slots(agxbuf *signature, Agedge_t *edge,
                                             const char *slot_prefix,
                                             const htmllabel_t *label,
                                             const char *fallback_imagescale);

static void append_html_text_identity_slots(agxbuf *signature, Agedge_t *edge,
                                            const char *slot_prefix,
                                            const htmltxt_t *text) {
  agxbuf field = {0};
  agxbprint(&field, "%s:span-count", slot_prefix);
  agxbuf rendered_number = {0};
  agxbprint(&rendered_number, "%zu", text->nspans);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));

  for (size_t i = 0; i < text->nspans; i++) {
    const htextspan_t *span = &text->spans[i];
    agxbclear(&field);
    agxbprint(&field, "%s:%zu:just", slot_prefix, i);
    agxbclear(&rendered_number);
    agxbprint(&rendered_number, "%hhd", span->just);
    append_plain_signature_slot(signature, agxbuse(&field),
                                agxbuse(&rendered_number));

    for (size_t j = 0; j < span->nitems; j++) {
      const textspan_t *item = &span->items[j];
      agxbclear(&field);
      agxbprint(&field, "%s:%zu:%zu:text", slot_prefix, i, j);
      append_substituted_signature_slot(signature, agxbuse(&field), item->str,
                                        edge);
      if (item->font != NULL) {
        agxbclear(&field);
        agxbprint(&field, "%s:%zu:%zu:font", slot_prefix, i, j);
        agxbclear(&rendered_number);
        agxbprint(&rendered_number,
                  "%s:", item->font->name == NULL ? "" : item->font->name);
        append_html_font_color(&rendered_number, edge, item->font->color);
        agxbprint(&rendered_number, ":%a:%u", item->font->size,
                  item->font->flags);
        append_plain_signature_slot(signature, agxbuse(&field),
                                    agxbuse(&rendered_number));
      }
    }
  }
  agxbfree(&rendered_number);
  agxbfree(&field);
}

static void append_html_image_identity_slots(agxbuf *signature,
                                             const char *slot_prefix,
                                             const htmlimg_t *image,
                                             const char *fallback_imagescale) {
  agxbuf field = {0};
  agxbprint(&field, "%s:src", slot_prefix);
  append_nullable_plain_field(signature, agxbuse(&field), image->src);

  agxbclear(&field);
  agxbprint(&field, "%s:scale", slot_prefix);
  append_plain_signature_slot(signature, agxbuse(&field),
                              image->scale != NULL ? image->scale
                                                   : fallback_imagescale);
  agxbfree(&field);
}

static void append_html_table_identity_slots(agxbuf *signature, Agedge_t *edge,
                                             const char *slot_prefix,
                                             const htmltbl_t *table,
                                             const char *fallback_imagescale) {
  append_html_data_identity_slots(signature, slot_prefix, &table->data);

  agxbuf field = {0};
  agxbprint(&field, "%s:shape", slot_prefix);
  agxbuf rendered_number = {0};
  agxbprint(&rendered_number, "%zu:%zu:%d:%d", table->row_count,
            table->column_count, table->hrule, table->vrule);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));

  if (table->cells != NULL) {
    for (htmlcell_t **cell = table->cells; *cell != NULL; cell++) {
      agxbclear(&field);
      agxbprint(&field, "%s:cell:%u:%u", slot_prefix, (*cell)->row,
                (*cell)->col);
      append_html_data_identity_slots(signature, agxbuse(&field),
                                      &(*cell)->data);
      agxbclear(&rendered_number);
      agxbprint(&rendered_number, "%u:%u:%d:%d", (*cell)->rowspan,
                (*cell)->colspan, (*cell)->hruled, (*cell)->vruled);
      agxbput(&field, ":span");
      append_plain_signature_slot(signature, agxbuse(&field),
                                  agxbuse(&rendered_number));

      agxbclear(&field);
      agxbprint(&field, "%s:cell:%u:%u:child", slot_prefix, (*cell)->row,
                (*cell)->col);
      append_html_label_identity_slots(signature, edge, agxbuse(&field),
                                       &(*cell)->child, fallback_imagescale);
    }
  }
  agxbfree(&rendered_number);
  agxbfree(&field);
}

static void append_html_label_identity_slots(agxbuf *signature, Agedge_t *edge,
                                             const char *slot_prefix,
                                             const htmllabel_t *label,
                                             const char *fallback_imagescale) {
  append_plain_signature_slot(signature, slot_prefix,
                              label->kind == HTML_TBL     ? "table"
                              : label->kind == HTML_TEXT  ? "text"
                              : label->kind == HTML_IMAGE ? "image"
                                                          : "unset");
  switch (label->kind) {
  case HTML_TBL:
    append_html_table_identity_slots(signature, edge, slot_prefix, label->u.tbl,
                                     fallback_imagescale);
    return;
  case HTML_TEXT:
    append_html_text_identity_slots(signature, edge, slot_prefix, label->u.txt);
    return;
  case HTML_IMAGE:
    append_html_image_identity_slots(signature, slot_prefix, label->u.img,
                                     fallback_imagescale);
    return;
  case HTML_UNSET:
    return;
  }
}

static bool html_label_may_use_colorscheme(const char *text) {
  for (const char *attribute = text; *attribute != '\0'; attribute++) {
    size_t name_size = strlen("COLOR");
    if (strncasecmp(attribute, "COLOR", name_size) != 0) {
      name_size = strlen("BGCOLOR");
      if (strncasecmp(attribute, "BGCOLOR", name_size) != 0) {
        continue;
      }
    }
    const char *value = attribute + name_size;
    while (isspace((unsigned char)value[0])) {
      value++;
    }
    if (value[0] != '=') {
      continue;
    }
    value++;
    while (isspace((unsigned char)value[0])) {
      value++;
    }
    if (value[0] != '"' && value[0] != '\'') {
      continue;
    }
    value++;
    if (value[0] >= '0' && value[0] <= '9') {
      return true;
    }
  }
  return false;
}

static bool edge_has_main_label(Agedge_t *edge) {
  return ED_label(edge) != NULL || ED_xlabel(edge) != NULL;
}

static bool edge_has_endpoint_label(Agedge_t *edge) {
  return ED_head_label(edge) != NULL || ED_tail_label(edge) != NULL;
}

static void append_html_label_hyperlink_slots(agxbuf *signature, Agedge_t *edge,
                                              const char *slot_prefix,
                                              const htmllabel_t *label,
                                              size_t *anchor_index);

static void append_html_data_hyperlink_field(agxbuf *signature, Agedge_t *edge,
                                             const char *slot_prefix,
                                             size_t anchor_index,
                                             const char *field_name,
                                             const char *value) {
  if (value == NULL || value[0] == '\0') {
    return;
  }

  agxbuf slot_name = {0};
  agxbprint(&slot_name, "%s:html-anchor:%zu:%s", slot_prefix, anchor_index,
            field_name);
  append_substituted_signature_slot(signature, agxbuse(&slot_name), value,
                                    edge);
  agxbfree(&slot_name);
}

static void append_html_data_hyperlink_slots(agxbuf *signature, Agedge_t *edge,
                                             const char *slot_prefix,
                                             const htmldata_t *data,
                                             size_t *anchor_index) {
  if ((data->href == NULL || data->href[0] == '\0') &&
      (data->title == NULL || data->title[0] == '\0') &&
      (data->target == NULL || data->target[0] == '\0') &&
      (data->id == NULL || data->id[0] == '\0')) {
    return;
  }

  const size_t current_anchor = (*anchor_index)++;
  append_html_data_hyperlink_field(signature, edge, slot_prefix, current_anchor,
                                   "HREF", data->href);
  append_html_data_hyperlink_field(signature, edge, slot_prefix, current_anchor,
                                   "TITLE", data->title);
  append_html_data_hyperlink_field(signature, edge, slot_prefix, current_anchor,
                                   "TARGET", data->target);
  append_html_data_hyperlink_field(signature, edge, slot_prefix, current_anchor,
                                   "ID", data->id);
}

static void append_html_table_hyperlink_slots(agxbuf *signature, Agedge_t *edge,
                                              const char *slot_prefix,
                                              const htmltbl_t *table,
                                              size_t *anchor_index) {
  append_html_data_hyperlink_slots(signature, edge, slot_prefix, &table->data,
                                   anchor_index);
  if (table->cells == NULL) {
    return;
  }
  for (htmlcell_t **cell = table->cells; *cell != NULL; cell++) {
    append_html_data_hyperlink_slots(signature, edge, slot_prefix,
                                     &(*cell)->data, anchor_index);
    append_html_label_hyperlink_slots(signature, edge, slot_prefix,
                                      &(*cell)->child, anchor_index);
  }
}

static void append_html_label_hyperlink_slots(agxbuf *signature, Agedge_t *edge,
                                              const char *slot_prefix,
                                              const htmllabel_t *label,
                                              size_t *anchor_index) {
  switch (label->kind) {
  case HTML_TBL:
    append_html_table_hyperlink_slots(signature, edge, slot_prefix,
                                      label->u.tbl, anchor_index);
    return;
  case HTML_TEXT:
  case HTML_IMAGE:
  case HTML_UNSET:
    return;
  }
}

static bool
edge_attribute_is_rendered(Agraph_t *root_graph, Agedge_t *edge,
                           edge_attribute_classification_t classification) {
  const edge_attribute_facts_t *const facts = classification.facts;
  if (facts->render_scope == ATTRIBUTE_RENDER_HYPERLINK ||
      facts->render_scope == ATTRIBUTE_RENDER_LAYOUT_ONLY ||
      facts->render_scope == ATTRIBUTE_RENDER_COLOR_TRANSLATOR) {
    return false;
  }
  if (facts->render_scope == ATTRIBUTE_RENDER_MAIN_LABEL &&
      !edge_has_main_label(edge)) {
    return false;
  }
  if (facts->render_scope == ATTRIBUTE_RENDER_PLAIN_PRIMARY_LABEL) {
    if (ED_label(edge) == NULL || ED_label(edge)->html) {
      return false;
    }
  }
  if (facts->render_scope == ATTRIBUTE_RENDER_ENDPOINT_LABEL &&
      !edge_has_endpoint_label(edge)) {
    return false;
  }
  if (facts->render_scope == ATTRIBUTE_RENDER_ORTHO_EDGE) {
    const char *const splines_value = agget(root_graph, "splines");
    if (splines_value == NULL || strcmp(splines_value, "ortho") != 0) {
      return false;
    }
  }
  if (facts->render_scope == ATTRIBUTE_RENDER_GRAPH_LAYERS) {
    const char *const layers = agget(root_graph, "layers");
    if (layers == NULL || layers[0] == '\0') {
      return false;
    }
  }
  /* dotLayout() calls dot_compoundEdges() only for a truthy compound graph. */
  if (facts->compound_only && !mapbool(agget(root_graph, "compound"))) {
    return false;
  }
  return true;
}

static bool edge_color_value(Agedge_t *edge, comparable_attribute_value_t value,
                             gvcolor_t *color) {
  if (value.is_html || value.text[0] == '\0' ||
      strchr(value.text, ':') != NULL) {
    return false;
  }

  char *const previous_color_scheme =
      setColorScheme(agget(edge, "colorscheme"));
  const int result = colorxlate(value.text, color, RGBA_BYTE);
  char *const restored_color_scheme = setColorScheme(previous_color_scheme);
  free(previous_color_scheme);
  free(restored_color_scheme);
  return result == COLOR_OK;
}

static bool parse_color_segment_fraction(strview_t *segment, double *fraction) {
  const char *const separator = memchr(segment->data, ';', segment->size);
  if (separator == NULL) {
    *fraction = 0.0;
    return true;
  }

  char *end = NULL;
  const double parsed = strtod(separator + 1, &end);
  if (end == separator + 1 || parsed < 0.0) {
    return false;
  }
  segment->size = (size_t)(separator - segment->data);
  *fraction = parsed;
  return true;
}

static normalized_color_segment_t *
normalized_color_segments(const char *color_list, size_t *segment_count) {
  size_t capacity = 1;
  for (const char *p = color_list; *p != '\0'; p++) {
    if (*p == ':') {
      capacity++;
    }
  }
  normalized_color_segment_t *segments = gv_calloc(capacity, sizeof(*segments));
  double left = 1.0;
  size_t count = 0;
  const char *segment_start = color_list;
  for (size_t i = 0; i < capacity; i++) {
    const char *const segment_end = strchr(segment_start, ':');
    strview_t color = {
        .data = segment_start,
        .size = segment_end == NULL ? strlen(segment_start)
                                    : (size_t)(segment_end - segment_start),
    };
    double fraction = 0.0;
    if (!parse_color_segment_fraction(&color, &fraction)) {
      free(segments);
      *segment_count = 0;
      return NULL;
    }
    if (fraction > left) {
      fraction = left;
    }
    left -= fraction;
    segments[count++] =
        (normalized_color_segment_t){.color = color, .fraction = fraction};
    if (left > -1E-5 && left < 1E-5) {
      left = 0.0;
      break;
    }
    if (segment_end == NULL) {
      break;
    }
    segment_start = segment_end + 1;
  }

  if (left > 0.0) {
    size_t empty_fraction_count = 0;
    for (size_t i = 0; i < count; i++) {
      if (segments[i].fraction <= 0.0) {
        empty_fraction_count++;
      }
    }
    if (empty_fraction_count > 0) {
      const double delta = left / (double)empty_fraction_count;
      for (size_t i = 0; i < count; i++) {
        if (segments[i].fraction <= 0.0) {
          segments[i].fraction = delta;
        }
      }
    } else if (count > 0) {
      segments[count - 1].fraction += left;
    }
  }

  while (count > 0 && segments[count - 1].fraction <= 0.0) {
    count--;
  }
  *segment_count = count;
  return segments;
}

static void append_color_segment(agxbuf *rendered_list, strview_t color_name) {
  agxbuf color_text = {0};
  agxbput_n(&color_text, color_name.data, color_name.size);
  gvcolor_t color;
  if (colorxlate(agxbuse(&color_text), &color, RGBA_BYTE) == COLOR_OK) {
    agxbprint(rendered_list, "#%02x%02x%02x%02x", color.u.rgba[0],
              color.u.rgba[1], color.u.rgba[2], color.u.rgba[3]);
  } else {
    agxbput_n(rendered_list, color_name.data, color_name.size);
  }
  agxbfree(&color_text);
}

static bool color_list_has_explicit_segments(const char *color_list) {
  return strchr(color_list, ';') != NULL;
}

static void append_edge_color_list_value(agxbuf *signature, Agedge_t *edge,
                                         const char *slot_name,
                                         const char *color_list,
                                         bool reverse_orientation) {
  size_t segment_count = 0;
  normalized_color_segment_t *const segments =
      normalized_color_segments(color_list, &segment_count);
  if (segments == NULL) {
    append_plain_signature_slot(signature, slot_name, color_list);
    return;
  }

  agxbuf rendered_list = {0};
  char *const previous_color_scheme =
      setColorScheme(agget(edge, "colorscheme"));
  const bool has_explicit_segments =
      color_list_has_explicit_segments(color_list);

  if (segment_count == 1 && segments[0].fraction > 1.0 - 1E-5 &&
      segments[0].fraction < 1.0 + 1E-5) {
    append_color_segment(&rendered_list, segments[0].color);
  } else if (!has_explicit_segments) {
    agxbput(&rendered_list, "parallel:");
    for (size_t j = 0; j < segment_count; j++) {
      const size_t i = reverse_orientation ? segment_count - j - 1 : j;
      if (j > 0) {
        agxbputc(&rendered_list, ':');
      }
      append_color_segment(&rendered_list, segments[i].color);
    }
  } else {
    for (size_t j = 0; j < segment_count; j++) {
      const size_t i = reverse_orientation ? segment_count - j - 1 : j;
      if (j > 0) {
        agxbputc(&rendered_list, ':');
      }
      append_color_segment(&rendered_list, segments[i].color);
      agxbprint(&rendered_list, ";%a", segments[i].fraction);
    }
  }

  char *const restored_color_scheme = setColorScheme(previous_color_scheme);
  free(previous_color_scheme);
  free(restored_color_scheme);
  free(segments);
  append_plain_signature_slot(signature, slot_name, agxbuse(&rendered_list));
  if (!has_explicit_segments) {
    agxbuf count_slot_name = {0};
    agxbprint(&count_slot_name, "%s:lane-count", slot_name);
    agxbuf rendered_count = {0};
    agxbprint(&rendered_count, "%zu", segment_count);
    append_plain_signature_slot(signature, agxbuse(&count_slot_name),
                                agxbuse(&rendered_count));
    agxbfree(&rendered_count);
    agxbfree(&count_slot_name);
  }
  agxbfree(&rendered_list);
}

static void append_edge_color_value(agxbuf *signature, Agedge_t *edge,
                                    const char *slot_name,
                                    comparable_attribute_value_t value,
                                    bool allow_color_list,
                                    bool reverse_orientation) {
  if (allow_color_list && !value.is_html && strchr(value.text, ':') != NULL) {
    append_edge_color_list_value(signature, edge, slot_name, value.text,
                                 reverse_orientation);
    return;
  }

  gvcolor_t color;
  if (edge_color_value(edge, value, &color)) {
    agxbuf rendered_color = {0};
    agxbprint(&rendered_color, "#%02x%02x%02x%02x", color.u.rgba[0],
              color.u.rgba[1], color.u.rgba[2], color.u.rgba[3]);
    append_plain_signature_slot(signature, slot_name, agxbuse(&rendered_color));
    agxbfree(&rendered_color);
    return;
  }

  append_signature_slot(signature, slot_name, value);
}

static void append_textlabel_slots(agxbuf *signature, Agedge_t *edge,
                                   const char *slot_prefix,
                                   const textlabel_t *label) {
  if (label == NULL) {
    return;
  }

  agxbuf slot_name = {0};
  agxbprint(&slot_name, "%s:text", slot_prefix);
  if (label->html) {
    const char *imagescale = agget(edge, "imagescale");
    if (imagescale == NULL || imagescale[0] == '\0') {
      imagescale = "false";
    }
    append_html_label_identity_slots(signature, edge, agxbuse(&slot_name),
                                     label->u.html, imagescale);
    if (html_label_may_use_colorscheme(label->text)) {
      agxbclear(&slot_name);
      agxbprint(&slot_name, "%s:colorscheme", slot_prefix);
      const char *colorscheme = agget(edge, "colorscheme");
      append_plain_signature_slot(signature, agxbuse(&slot_name),
                                  colorscheme == NULL ? "" : colorscheme);
    }
    size_t anchor_index = 0;
    append_html_label_hyperlink_slots(signature, edge, slot_prefix,
                                      label->u.html, &anchor_index);
  } else {
    agxbuf rendered_label = {0};
    for (size_t i = 0; i < label->u.txt.nspans; i++) {
      if (i > 0) {
        agxbputc(&rendered_label, '\n');
      }
      agxbputc(&rendered_label, label->u.txt.span[i].just);
      agxbputc(&rendered_label, ':');
      agxbput(&rendered_label, label->u.txt.span[i].str);
    }
    append_plain_signature_slot(signature, agxbuse(&slot_name),
                                agxbuse(&rendered_label));
    agxbfree(&rendered_label);
  }

  agxbclear(&slot_name);
  agxbprint(&slot_name, "%s:fontname", slot_prefix);
  append_plain_signature_slot(signature, agxbuse(&slot_name), label->fontname);

  agxbclear(&slot_name);
  agxbprint(&slot_name, "%s:fontsize", slot_prefix);
  agxbuf rendered_number = {0};
  agxbprint(&rendered_number, "%a", label->fontsize);
  append_plain_signature_slot(signature, agxbuse(&slot_name),
                              agxbuse(&rendered_number));
  agxbfree(&rendered_number);

  agxbclear(&slot_name);
  agxbprint(&slot_name, "%s:fontcolor", slot_prefix);
  append_edge_color_value(signature, edge, agxbuse(&slot_name),
                          plain_attribute_value(label->fontcolor), false,
                          false);
  agxbfree(&slot_name);
}

static textlabel_t *edge_endpoint_label(Agedge_t *edge,
                                        edge_endpoint_t endpoint) {
  return endpoint == EDGE_HEAD_ENDPOINT ? ED_head_label(edge)
                                        : ED_tail_label(edge);
}

static void append_structured_label_slots(agxbuf *signature, Agedge_t *edge,
                                          bool reverse_orientation) {
  append_textlabel_slots(signature, edge, "label", ED_label(edge));
  if (ED_label(edge) != NULL) {
    append_plain_signature_slot(signature, "label:ontop",
                                ED_label_ontop(edge) ? "true" : "false");
  }
  append_textlabel_slots(signature, edge, "xlabel", ED_xlabel(edge));

  for (edge_endpoint_t endpoint = EDGE_TAIL_ENDPOINT;
       endpoint < EDGE_ENDPOINT_COUNT; endpoint++) {
    const edge_endpoint_t source_endpoint =
        reverse_orientation
            ? (endpoint == EDGE_HEAD_ENDPOINT ? EDGE_TAIL_ENDPOINT
                                              : EDGE_HEAD_ENDPOINT)
            : endpoint;
    append_textlabel_slots(signature, edge,
                           endpoint == EDGE_HEAD_ENDPOINT ? "headlabel"
                                                          : "taillabel",
                           edge_endpoint_label(edge, source_endpoint));
  }
}

static bool edge_numeric_attribute_value(comparable_attribute_value_t value,
                                         double default_value, double minimum,
                                         double *number) {
  if (value.is_html) {
    return false;
  }
  if (value.text[0] == '\0') {
    *number = default_value;
    return true;
  }

  char *end = NULL;
  const double parsed_value = strtod(value.text, &end);
  if (end == value.text) {
    return false;
  }
  *number = parsed_value < minimum ? minimum : parsed_value;
  return true;
}

static bool
edge_numeric_projected_value(edge_attribute_classification_t classification,
                             comparable_attribute_value_t value,
                             double *number) {
  if (!classification.found) {
    return false;
  }
  double default_value;
  double minimum = 0.0;

  if (classification.facts->default_kind == ATTRIBUTE_DEFAULT_ONE) {
    default_value = 1.0;
  } else if (classification.facts->default_kind ==
             ATTRIBUTE_DEFAULT_LABEL_ANGLE) {
    default_value = PORT_LABEL_ANGLE;
    minimum = -180.0;
  } else {
    return false;
  }

  return edge_numeric_attribute_value(value, default_value, minimum, number);
}

static bool style_penwidth_value(const char *style, double *penwidth) {
  if (style == NULL || style[0] == '\0') {
    return false;
  }
  bool found = false;
  for (char **item = parse_style((char *)style); *item != NULL; item++) {
    if (strcmp(*item, "bold") == 0) {
      *penwidth = 2.0;
      found = true;
      continue;
    }
    if (strcmp(*item, "setlinewidth") != 0) {
      continue;
    }
    char *end = NULL;
    const char *const argument = *item + strlen(*item) + 1;
    const double parsed = strtod(argument, &end);
    if (end != argument) {
      *penwidth = parsed;
      found = true;
    }
  }
  return found;
}

static bool
edge_projected_penwidth(edge_attribute_classification_t classification,
                        comparable_attribute_value_t value, Agedge_t *edge,
                        double *penwidth) {
  if (!classification.found ||
      classification.facts->default_kind != ATTRIBUTE_DEFAULT_ONE) {
    return false;
  }
  if (strcmp(classification.name, "penwidth") == 0 && value.text[0] == '\0' &&
      !value.is_html && style_penwidth_value(agget(edge, "style"), penwidth)) {
    return true;
  }
  return edge_numeric_projected_value(classification, value, penwidth);
}

static bool graph_uses_ortho_edges(Agraph_t *root_graph) {
  const char *const splines_value = agget(root_graph, "splines");
  return splines_value != NULL && strcmp(splines_value, "ortho") == 0;
}

static bool edge_style_token_sets_pen_pattern(const char *style) {
  return strcmp(style, "solid") == 0 || strcmp(style, "dashed") == 0 ||
         strcmp(style, "dotted") == 0 || strcmp(style, "invis") == 0;
}

static void append_style_value(agxbuf *signature, const char *slot_name,
                               comparable_attribute_value_t value,
                               bool keep_rounded) {
  if (value.is_html) {
    append_signature_slot(signature, slot_name, value);
    return;
  }

  agxbuf rendered_style = {0};
  const char *pen_pattern = NULL;
  for (char **item = parse_style((char *)value.text); *item != NULL; item++) {
    if (strcmp(*item, "invis") == 0) {
      append_plain_signature_slot(signature, slot_name, "invis");
      agxbfree(&rendered_style);
      return;
    }
    if (strcmp(*item, "bold") == 0 || strcmp(*item, "setlinewidth") == 0 ||
        (!keep_rounded && strcmp(*item, "rounded") == 0)) {
      continue;
    }
    if (edge_style_token_sets_pen_pattern(*item)) {
      pen_pattern = *item;
      continue;
    }
    if (agxblen(&rendered_style) > 0) {
      agxbputc(&rendered_style, ',');
    }
    agxbput(&rendered_style, *item);
  }
  if (pen_pattern != NULL) {
    if (agxblen(&rendered_style) > 0) {
      agxbputc(&rendered_style, ',');
    }
    agxbput(&rendered_style, pen_pattern);
  }
  append_plain_signature_slot(
      signature, slot_name,
      agxblen(&rendered_style) == 0 ? "solid" : agxbuse(&rendered_style));
  agxbfree(&rendered_style);
}

static port edge_endpoint_port(Agedge_t *edge, edge_endpoint_t endpoint) {
  return endpoint == EDGE_HEAD_ENDPOINT ? ED_head_port(edge)
                                        : ED_tail_port(edge);
}

static void append_structured_port_slots(agxbuf *signature, Agedge_t *edge,
                                         bool reverse_orientation) {
  for (edge_endpoint_t endpoint = EDGE_TAIL_ENDPOINT;
       endpoint < EDGE_ENDPOINT_COUNT; endpoint++) {
    const edge_endpoint_t source_endpoint =
        reverse_orientation
            ? (endpoint == EDGE_HEAD_ENDPOINT ? EDGE_TAIL_ENDPOINT
                                              : EDGE_HEAD_ENDPOINT)
            : endpoint;
    const port resolved_port = edge_endpoint_port(edge, source_endpoint);
    if (!resolved_port.defined) {
      continue;
    }

    agxbuf resolved_value = {0};
    agxbprint(&resolved_value, "%a,%a,%d,%d", resolved_port.p.x,
              resolved_port.p.y, resolved_port.constrained, resolved_port.dyna);
    append_plain_signature_slot(
        signature, endpoint == EDGE_HEAD_ENDPOINT ? "headport" : "tailport",
        agxbuse(&resolved_value));
    agxbfree(&resolved_value);
  }
}

static void append_projected_attribute_value(agxbuf *signature,
                                             Agraph_t *root_graph,
                                             Agedge_t *edge,
                                             const char *slot_name,
                                             const char *attribute_name,
                                             bool reverse_orientation) {
  comparable_attribute_value_t value =
      named_attribute_value(root_graph, edge, attribute_name);
  const edge_attribute_classification_t classification =
      edge_attribute_classification(attribute_name);
  const edge_attribute_facts_t *const facts = classification.facts;

  if (facts->substituted) {
    append_substituted_signature_slot(signature, slot_name, value.text, edge);
    return;
  }

  if (facts->clipping) {
    append_plain_signature_slot(
        signature, slot_name,
        value.text[0] == '\0' || mapbool(value.text) ? "true" : "false");
    return;
  }

  if (facts->color) {
    if (value.text[0] == '\0') {
      value = plain_attribute_value(DEFAULT_COLOR);
    }
    append_edge_color_value(signature, edge, slot_name, value, true,
                            reverse_orientation);
    return;
  }

  if (facts->presence_affects_rendering) {
    agxbuf presence_slot = {0};
    agxbprint(&presence_slot, "%s:present", slot_name);
    append_plain_signature_slot(signature, agxbuse(&presence_slot),
                                value.text[0] == '\0' ? "false" : "true");
    agxbfree(&presence_slot);
  }

  double number;
  if (edge_projected_penwidth(classification, value, edge, &number)) {
    agxbuf rendered_number = {0};
    agxbprint(&rendered_number, "%a", number);
    append_plain_signature_slot(signature, slot_name,
                                agxbuse(&rendered_number));
    agxbfree(&rendered_number);
    return;
  }

  if (facts->style && !value.is_html && value.text[0] == '\0') {
    value = plain_attribute_value("solid");
  }
  if (facts->style) {
    double penwidth;
    if (!value.is_html && style_penwidth_value(value.text, &penwidth) &&
        agfindedgeattr(root_graph, "penwidth") == NULL) {
      agxbuf rendered_number = {0};
      agxbprint(&rendered_number, "%a", penwidth);
      append_plain_signature_slot(signature, "penwidth",
                                  agxbuse(&rendered_number));
      agxbfree(&rendered_number);
    }
    append_style_value(signature, slot_name, value,
                       graph_uses_ortho_edges(root_graph));
    return;
  }

  if (facts->default_kind == ATTRIBUTE_DEFAULT_FALSE) {
    append_plain_signature_slot(
        signature, slot_name,
        value.text[0] != '\0' && mapbool(value.text) ? "true" : "false");
    return;
  }

  append_signature_slot(signature, slot_name, value);
}

static void append_ordinary_attribute_slots(agxbuf *signature,
                                            Agraph_t *root_graph,
                                            Agedge_t *edge,
                                            bool reverse_orientation) {
  for (Agsym_t *attribute = agnxtattr(root_graph, AGEDGE, NULL);
       attribute != NULL;
       attribute = agnxtattr(root_graph, AGEDGE, attribute)) {
    const edge_attribute_classification_t classification =
        edge_attribute_classification(attribute->name);
    if (!classification.found || classification.endpoint ||
        !edge_attribute_is_rendered(root_graph, edge, classification)) {
      continue;
    }
    append_projected_attribute_value(signature, root_graph, edge,
                                     attribute->name, attribute->name,
                                     reverse_orientation);
  }
}

static bool edge_uses_tapered_style(Agedge_t *edge) {
  char *const style = agget(edge, "style");
  if (style == NULL || style[0] == '\0') {
    return false;
  }
  for (char **item = parse_style(style); *item != NULL; item++) {
    if (strcmp(*item, "tapered") == 0) {
      return true;
    }
  }
  return false;
}

static void append_taper_direction_slot(agxbuf *signature, Agedge_t *edge,
                                        bool reverse_orientation) {
  if (!edge_uses_tapered_style(edge)) {
    return;
  }

  const char *direction = agget(edge, "dir");
  if (direction == NULL ||
      (strcmp(direction, "forward") != 0 && strcmp(direction, "back") != 0 &&
       strcmp(direction, "both") != 0 && strcmp(direction, "none") != 0)) {
    direction = agisdirected(agraphof(edge)) ? "forward" : "none";
  }
  if (reverse_orientation) {
    if (strcmp(direction, "forward") == 0) {
      direction = "back";
    } else if (strcmp(direction, "back") == 0) {
      direction = "forward";
    }
  }

  /*
   * An arrowless tapered edge has an empty decoration record, but taperfun()
   * still consumes dir. Keep direction in the core identity only while the
   * tapered renderer consumes it, so ordinary arrowless edges remain equal.
   */
  append_plain_signature_slot(signature, "taper:direction", direction);
}

typedef enum {
  HYPERLINK_VALUE_URL,
  HYPERLINK_VALUE_TOOLTIP,
  HYPERLINK_VALUE_TARGET,
  HYPERLINK_VALUE_COUNT,
} hyperlink_value_kind_t;

typedef enum {
  ATTRIBUTE_NAME_PREFIXED,
  ATTRIBUTE_NAME_BARE,
} attribute_name_form_t;

typedef struct {
  attribute_owner_t owner;
  attribute_name_form_t form;
} attribute_value_source_t;

typedef struct {
  attribute_identity_t slot;
  attribute_value_source_t sources[3];
  size_t sources_size;
} attribute_inheritance_t;

static const attribute_inheritance_t hyperlink_value_matrix[] = {
    {.slot = {.owner = ATTRIBUTE_OWNER_EDGE, .kind = ATTRIBUTE_KIND_URL},
     .sources = {{ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
    {.slot = {.owner = ATTRIBUTE_OWNER_LABEL, .kind = ATTRIBUTE_KIND_URL},
     .sources = {{ATTRIBUTE_OWNER_LABEL, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
    /* nodeIntersect() falls back from explicit endpoint URLs to obj->url. */
    {.slot = {.owner = ATTRIBUTE_OWNER_HEAD, .kind = ATTRIBUTE_KIND_URL},
     .sources = {{ATTRIBUTE_OWNER_HEAD, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 3},
    {.slot = {.owner = ATTRIBUTE_OWNER_TAIL, .kind = ATTRIBUTE_KIND_URL},
     .sources = {{ATTRIBUTE_OWNER_TAIL, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 3},
    {.slot = {.owner = ATTRIBUTE_OWNER_EDGE, .kind = ATTRIBUTE_KIND_TOOLTIP},
     .sources = {{ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_PREFIXED}},
     .sources_size = 2},
    {.slot = {.owner = ATTRIBUTE_OWNER_LABEL, .kind = ATTRIBUTE_KIND_TOOLTIP},
     .sources = {{ATTRIBUTE_OWNER_LABEL, ATTRIBUTE_NAME_PREFIXED}},
     .sources_size = 1},
    {.slot = {.owner = ATTRIBUTE_OWNER_HEAD, .kind = ATTRIBUTE_KIND_TOOLTIP},
     .sources = {{ATTRIBUTE_OWNER_HEAD, ATTRIBUTE_NAME_PREFIXED}},
     .sources_size = 1},
    {.slot = {.owner = ATTRIBUTE_OWNER_TAIL, .kind = ATTRIBUTE_KIND_TOOLTIP},
     .sources = {{ATTRIBUTE_OWNER_TAIL, ATTRIBUTE_NAME_PREFIXED}},
     .sources_size = 1},
    {.slot = {.owner = ATTRIBUTE_OWNER_EDGE, .kind = ATTRIBUTE_KIND_TARGET},
     .sources = {{ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
    {.slot = {.owner = ATTRIBUTE_OWNER_LABEL, .kind = ATTRIBUTE_KIND_TARGET},
     .sources = {{ATTRIBUTE_OWNER_LABEL, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
    {.slot = {.owner = ATTRIBUTE_OWNER_HEAD, .kind = ATTRIBUTE_KIND_TARGET},
     .sources = {{ATTRIBUTE_OWNER_HEAD, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
    {.slot = {.owner = ATTRIBUTE_OWNER_TAIL, .kind = ATTRIBUTE_KIND_TARGET},
     .sources = {{ATTRIBUTE_OWNER_TAIL, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
};

static const attribute_inheritance_t endpoint_label_url_matrix[] = {
    /* emit_begin_edge() gives endpoint labels only the href/URL default. */
    {.slot = {.owner = ATTRIBUTE_OWNER_HEAD, .kind = ATTRIBUTE_KIND_URL},
     .sources = {{ATTRIBUTE_OWNER_HEAD, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
    {.slot = {.owner = ATTRIBUTE_OWNER_TAIL, .kind = ATTRIBUTE_KIND_URL},
     .sources = {{ATTRIBUTE_OWNER_TAIL, ATTRIBUTE_NAME_PREFIXED},
                 {ATTRIBUTE_OWNER_EDGE, ATTRIBUTE_NAME_BARE}},
     .sources_size = 2},
};

static attribute_owner_t opposite_attribute_owner(attribute_owner_t owner) {
  switch (owner) {
  case ATTRIBUTE_OWNER_HEAD:
    return ATTRIBUTE_OWNER_TAIL;
  case ATTRIBUTE_OWNER_TAIL:
    return ATTRIBUTE_OWNER_HEAD;
  case ATTRIBUTE_OWNER_EDGE:
  case ATTRIBUTE_OWNER_LABEL:
  case ATTRIBUTE_OWNER_COUNT:
    return owner;
  }
  return owner;
}

static const char *attribute_owner_slot_name(attribute_owner_t owner) {
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

static void composed_attribute_name(attribute_owner_t owner,
                                    attribute_kind_t kind,
                                    attribute_name_form_t form, bool alias,
                                    char *name, size_t name_size) {
  const char *const stem = alias && attribute_kind_url_alias_stem(kind) != NULL
                               ? attribute_kind_url_alias_stem(kind)
                               : attribute_kind_canonical_stem(kind);
  if (form == ATTRIBUTE_NAME_BARE) {
    snprintf(name, name_size, "%s", stem);
  } else {
    snprintf(name, name_size, "%s%s", attribute_owner_prefix(owner), stem);
  }
}

static comparable_attribute_value_t
named_composed_attribute_value(Agraph_t *root_graph, Agedge_t *edge,
                               attribute_value_source_t source,
                               attribute_kind_t kind) {
  char name[32];
  const bool has_alias = attribute_kind_url_alias_stem(kind) != NULL;
  if (has_alias) {
    composed_attribute_name(source.owner, kind, source.form, true, name,
                            sizeof(name));
    const comparable_attribute_value_t value =
        named_attribute_value(root_graph, edge, name);
    if (value.text[0] != '\0') {
      return value;
    }
  }
  composed_attribute_name(source.owner, kind, source.form, false, name,
                          sizeof(name));
  return named_attribute_value(root_graph, edge, name);
}

static const attribute_inheritance_t *
attribute_inheritance(const attribute_inheritance_t *matrix, size_t matrix_size,
                      attribute_identity_t slot) {
  for (size_t i = 0; i < matrix_size; i++) {
    if (matrix[i].slot.owner == slot.owner &&
        matrix[i].slot.kind == slot.kind) {
      return &matrix[i];
    }
  }
  return NULL;
}

static comparable_attribute_value_t
named_inherited_attribute_value(Agraph_t *root_graph, Agedge_t *edge,
                                const attribute_inheritance_t *inheritance) {
  comparable_attribute_value_t empty_value = plain_attribute_value("");
  for (size_t i = 0; i < inheritance->sources_size; i++) {
    const comparable_attribute_value_t value = named_composed_attribute_value(
        root_graph, edge, inheritance->sources[i], inheritance->slot.kind);
    if (value.text[0] != '\0') {
      return value;
    }
    empty_value = value;
  }
  return empty_value;
}

static bool edge_has_inherited_attribute(Agraph_t *root_graph, Agedge_t *edge,
                                         const attribute_inheritance_t *matrix,
                                         size_t matrix_size,
                                         attribute_identity_t slot) {
  const attribute_inheritance_t *const inheritance =
      attribute_inheritance(matrix, matrix_size, slot);
  if (inheritance == NULL) {
    return false;
  }
  return named_inherited_attribute_value(root_graph, edge, inheritance)
             .text[0] != '\0';
}

static bool edge_has_explicit_owner_kind(Agraph_t *root_graph, Agedge_t *edge,
                                         attribute_owner_t owner,
                                         attribute_kind_t kind) {
  return named_composed_attribute_value(
             root_graph, edge,
             (attribute_value_source_t){.owner = owner,
                                        .form = ATTRIBUTE_NAME_PREFIXED},
             kind)
             .text[0] != '\0';
}

static bool hyperlink_owner_gate_is_open(Agedge_t *edge,
                                         attribute_owner_t owner) {
  switch (owner) {
  case ATTRIBUTE_OWNER_EDGE:
    return true;
  case ATTRIBUTE_OWNER_LABEL:
    return edge_has_main_label(edge);
  case ATTRIBUTE_OWNER_HEAD:
    return ED_head_label(edge) != NULL;
  case ATTRIBUTE_OWNER_TAIL:
    return ED_tail_label(edge) != NULL;
  case ATTRIBUTE_OWNER_COUNT:
    return false;
  }
  return false;
}

static bool hyperlink_tooltip_gate_is_open(Agraph_t *root_graph, Agedge_t *edge,
                                           attribute_owner_t owner) {
  if (hyperlink_owner_gate_is_open(edge, owner)) {
    return true;
  }
  if (owner != ATTRIBUTE_OWNER_HEAD && owner != ATTRIBUTE_OWNER_TAIL) {
    return false;
  }
  /* nodeIntersect() maps explicit endpoint tooltips without label geometry. */
  return edge_has_explicit_owner_kind(root_graph, edge, owner,
                                      ATTRIBUTE_KIND_TOOLTIP);
}

static const char *hyperlink_fallback_label(Agedge_t *edge,
                                            attribute_owner_t owner) {
  switch (owner) {
  case ATTRIBUTE_OWNER_EDGE:
  case ATTRIBUTE_OWNER_LABEL:
    return ED_label(edge) != NULL ? ED_label(edge)->text : "";
  case ATTRIBUTE_OWNER_HEAD:
    return ED_head_label(edge) != NULL ? ED_head_label(edge)->text : "";
  case ATTRIBUTE_OWNER_TAIL:
    return ED_tail_label(edge) != NULL ? ED_tail_label(edge)->text : "";
  case ATTRIBUTE_OWNER_COUNT:
    return "";
  }
  return "";
}

static bool hyperlink_target_anchor_is_present(Agraph_t *root_graph,
                                               Agedge_t *edge,
                                               attribute_owner_t owner) {
  if (owner == ATTRIBUTE_OWNER_EDGE) {
    return edge_has_inherited_attribute(
               root_graph, edge, hyperlink_value_matrix,
               ATTRIBUTE_COUNT(hyperlink_value_matrix),
               (attribute_identity_t){.owner = owner,
                                      .kind = ATTRIBUTE_KIND_URL}) ||
           edge_has_inherited_attribute(
               root_graph, edge, hyperlink_value_matrix,
               ATTRIBUTE_COUNT(hyperlink_value_matrix),
               (attribute_identity_t){.owner = owner,
                                      .kind = ATTRIBUTE_KIND_TOOLTIP});
  }

  if (owner == ATTRIBUTE_OWNER_LABEL) {
    return edge_has_inherited_attribute(
               root_graph, edge, hyperlink_value_matrix,
               ATTRIBUTE_COUNT(hyperlink_value_matrix),
               (attribute_identity_t){.owner = owner,
                                      .kind = ATTRIBUTE_KIND_URL}) ||
           edge_has_explicit_owner_kind(root_graph, edge, owner,
                                        ATTRIBUTE_KIND_TOOLTIP) ||
           edge_has_explicit_owner_kind(root_graph, edge, ATTRIBUTE_OWNER_EDGE,
                                        ATTRIBUTE_KIND_URL);
  }

  if (owner == ATTRIBUTE_OWNER_HEAD || owner == ATTRIBUTE_OWNER_TAIL) {
    return edge_has_inherited_attribute(
               root_graph, edge, endpoint_label_url_matrix,
               ATTRIBUTE_COUNT(endpoint_label_url_matrix),
               (attribute_identity_t){.owner = owner,
                                      .kind = ATTRIBUTE_KIND_URL}) ||
           edge_has_explicit_owner_kind(root_graph, edge, owner,
                                        ATTRIBUTE_KIND_TOOLTIP);
  }

  return edge_has_explicit_owner_kind(root_graph, edge, owner,
                                      ATTRIBUTE_KIND_URL) ||
         edge_has_explicit_owner_kind(root_graph, edge, owner,
                                      ATTRIBUTE_KIND_TOOLTIP) ||
         edge_has_explicit_owner_kind(root_graph, edge, owner,
                                      ATTRIBUTE_KIND_TARGET) ||
         edge_has_explicit_owner_kind(root_graph, edge, ATTRIBUTE_OWNER_EDGE,
                                      ATTRIBUTE_KIND_URL);
}

static bool hyperlink_value_is_rendered(Agraph_t *root_graph, Agedge_t *edge,
                                        attribute_owner_t owner,
                                        hyperlink_value_kind_t kind) {
  switch (kind) {
  case HYPERLINK_VALUE_URL: {
    const attribute_inheritance_t *const inheritance = attribute_inheritance(
        hyperlink_value_matrix, ATTRIBUTE_COUNT(hyperlink_value_matrix),
        (attribute_identity_t){.owner = owner, .kind = ATTRIBUTE_KIND_URL});
    const comparable_attribute_value_t url =
        named_inherited_attribute_value(root_graph, edge, inheritance);
    return (owner != ATTRIBUTE_OWNER_LABEL ||
            hyperlink_owner_gate_is_open(edge, owner)) &&
           (url.text[0] != '\0' ||
            edge_has_inherited_attribute(
                root_graph, edge, hyperlink_value_matrix,
                ATTRIBUTE_COUNT(hyperlink_value_matrix),
                (attribute_identity_t){.owner = owner,
                                       .kind = ATTRIBUTE_KIND_TOOLTIP}));
  }
  case HYPERLINK_VALUE_TOOLTIP:
    return hyperlink_tooltip_gate_is_open(root_graph, edge, owner);
  case HYPERLINK_VALUE_TARGET:
    return hyperlink_owner_gate_is_open(edge, owner) &&
           hyperlink_target_anchor_is_present(root_graph, edge, owner);
  case HYPERLINK_VALUE_COUNT:
    return false;
  }
  return false;
}

static const char *hyperlink_value_kind_name(hyperlink_value_kind_t kind) {
  switch (kind) {
  case HYPERLINK_VALUE_URL:
    return "URL";
  case HYPERLINK_VALUE_TOOLTIP:
    return "tooltip";
  case HYPERLINK_VALUE_TARGET:
    return "target";
  case HYPERLINK_VALUE_COUNT:
    return "";
  }
  return "";
}

static attribute_kind_t hyperlink_attribute_kind(hyperlink_value_kind_t kind) {
  switch (kind) {
  case HYPERLINK_VALUE_URL:
    return ATTRIBUTE_KIND_URL;
  case HYPERLINK_VALUE_TOOLTIP:
    return ATTRIBUTE_KIND_TOOLTIP;
  case HYPERLINK_VALUE_TARGET:
    return ATTRIBUTE_KIND_TARGET;
  case HYPERLINK_VALUE_COUNT:
    break;
  }
  return ATTRIBUTE_KIND_URL;
}

static void append_hyperlink_slots(agxbuf *signature, Agraph_t *root_graph,
                                   Agedge_t *edge, bool reverse_orientation) {
  for (attribute_owner_t canonical_owner = ATTRIBUTE_OWNER_EDGE;
       canonical_owner < ATTRIBUTE_OWNER_COUNT; canonical_owner++) {
    const attribute_owner_t source_owner =
        reverse_orientation ? opposite_attribute_owner(canonical_owner)
                            : canonical_owner;
    for (hyperlink_value_kind_t kind = HYPERLINK_VALUE_URL;
         kind < HYPERLINK_VALUE_COUNT; kind++) {
      if (!hyperlink_value_is_rendered(root_graph, edge, source_owner, kind)) {
        continue;
      }

      const attribute_inheritance_t *const inheritance = attribute_inheritance(
          hyperlink_value_matrix, ATTRIBUTE_COUNT(hyperlink_value_matrix),
          (attribute_identity_t){.owner = source_owner,
                                 .kind = hyperlink_attribute_kind(kind)});
      comparable_attribute_value_t value =
          named_inherited_attribute_value(root_graph, edge, inheritance);
      bool tooltip_uses_fallback = false;
      if (kind == HYPERLINK_VALUE_TOOLTIP && value.text[0] == '\0') {
        const attribute_inheritance_t *const url_inheritance =
            attribute_inheritance(
                hyperlink_value_matrix, ATTRIBUTE_COUNT(hyperlink_value_matrix),
                (attribute_identity_t){.owner = source_owner,
                                       .kind = ATTRIBUTE_KIND_URL});
        const comparable_attribute_value_t url =
            named_inherited_attribute_value(root_graph, edge, url_inheritance);
        if (url.text[0] != '\0') {
          value = plain_attribute_value(
              hyperlink_fallback_label(edge, source_owner));
          tooltip_uses_fallback = true;
        }
      }

      agxbuf slot_name = {0};
      agxbprint(&slot_name, "hyperlink:%s:%s",
                attribute_owner_slot_name(canonical_owner),
                hyperlink_value_kind_name(kind));
      if (kind == HYPERLINK_VALUE_TOOLTIP && tooltip_uses_fallback) {
        append_plain_signature_slot(signature, agxbuse(&slot_name), value.text);
      } else if (kind == HYPERLINK_VALUE_TOOLTIP && !tooltip_uses_fallback) {
        char *const preprocessed = preprocessTooltip((char *)value.text, edge);
        char *const substituted = strdup_and_subst_obj(preprocessed, edge);
        append_plain_signature_slot(signature, agxbuse(&slot_name),
                                    substituted);
        free(substituted);
        free(preprocessed);
      } else {
        append_substituted_signature_slot(signature, agxbuse(&slot_name),
                                          value.text, edge);
      }
      agxbfree(&slot_name);
    }

    if ((source_owner == ATTRIBUTE_OWNER_HEAD ||
         source_owner == ATTRIBUTE_OWNER_TAIL) &&
        hyperlink_owner_gate_is_open(edge, source_owner)) {
      const attribute_inheritance_t *const inheritance = attribute_inheritance(
          endpoint_label_url_matrix, ATTRIBUTE_COUNT(endpoint_label_url_matrix),
          (attribute_identity_t){.owner = source_owner,
                                 .kind = ATTRIBUTE_KIND_URL});
      comparable_attribute_value_t value =
          named_inherited_attribute_value(root_graph, edge, inheritance);
      agxbuf slot_name = {0};
      agxbprint(&slot_name, "hyperlink:%s:label-URL",
                attribute_owner_slot_name(canonical_owner));
      append_substituted_signature_slot(signature, agxbuse(&slot_name),
                                        value.text, edge);
      agxbfree(&slot_name);
    }
  }
}

static const char *endpoint_exception_name(const char *canonical_name,
                                           attribute_owner_t source_owner) {
  for (size_t i = 0; i < ATTRIBUTE_COUNT(edge_attribute_exceptions); i++) {
    const edge_attribute_exception_t *const exception =
        &edge_attribute_exceptions[i];
    if (exception->endpoint && strcmp(exception->name, canonical_name) != 0 &&
        exception->owner == source_owner) {
      const edge_attribute_classification_t canonical =
          edge_attribute_classification(canonical_name);
      if (canonical.facts == &exception->facts ||
          (canonical.facts->compound_only == exception->facts.compound_only &&
           canonical.facts->default_kind == exception->facts.default_kind)) {
        return exception->name;
      }
    }
  }
  return canonical_name;
}

static node_t *sameport_node(Agedge_t *edge, const char *attribute_name) {
  if (strcmp(attribute_name, "samehead") == 0) {
    return aghead(edge);
  }
  if (strcmp(attribute_name, "sametail") == 0) {
    return agtail(edge);
  }
  return NULL;
}

static bool sameport_group_has_multiple_members(Agraph_t *graph, Agedge_t *edge,
                                                const char *attribute_name) {
  Agsym_t *const attribute = agfindedgeattr(graph, (char *)attribute_name);
  if (attribute == NULL) {
    return false;
  }

  const char *const group_id = agxget(edge, attribute);
  if (group_id[0] == '\0') {
    return false;
  }

  node_t *const node = sameport_node(edge, attribute_name);
  if (node == NULL || aghead(edge) == agtail(edge)) {
    return false;
  }

  size_t members = 0;
  for (Agedge_t *member = agfstedge(graph, node); member != NULL;
       member = agnxtedge(graph, member, node)) {
    if (aghead(member) == agtail(member)) {
      continue;
    }
    if (sameport_node(member, attribute_name) != node) {
      continue;
    }
    if (strcmp(agxget(member, attribute), group_id) == 0 && ++members > 1) {
      return true;
    }
  }
  return false;
}

static void append_endpoint_attribute_slots(agxbuf *signature,
                                            Agraph_t *root_graph,
                                            Agedge_t *edge,
                                            bool reverse_orientation) {
  for (size_t i = 0; i < ATTRIBUTE_COUNT(edge_attribute_exceptions); i++) {
    const edge_attribute_exception_t *const exception =
        &edge_attribute_exceptions[i];
    const edge_attribute_classification_t classification =
        edge_attribute_classification(exception->name);
    if (!exception->endpoint ||
        !edge_attribute_is_rendered(root_graph, edge, classification)) {
      continue;
    }
    const attribute_owner_t source_owner =
        reverse_orientation ? opposite_attribute_owner(exception->owner)
                            : exception->owner;
    const char *const source_name =
        endpoint_exception_name(exception->name, source_owner);
    if ((strcmp(source_name, "samehead") == 0 ||
         strcmp(source_name, "sametail") == 0) &&
        !sameport_group_has_multiple_members(root_graph, edge, source_name)) {
      continue;
    }
    append_projected_attribute_value(signature, root_graph, edge,
                                     exception->name, source_name,
                                     reverse_orientation);
  }

  for (attribute_owner_t canonical_owner = ATTRIBUTE_OWNER_HEAD;
       canonical_owner <= ATTRIBUTE_OWNER_TAIL; canonical_owner++) {
    const attribute_owner_t source_owner =
        reverse_orientation ? opposite_attribute_owner(canonical_owner)
                            : canonical_owner;
    char slot_name[32];
    char source_name[32];
    composed_attribute_name(canonical_owner, ATTRIBUTE_KIND_CLIP,
                            ATTRIBUTE_NAME_PREFIXED, false, slot_name,
                            sizeof(slot_name));
    composed_attribute_name(source_owner, ATTRIBUTE_KIND_CLIP,
                            ATTRIBUTE_NAME_PREFIXED, false, source_name,
                            sizeof(source_name));
    append_projected_attribute_value(signature, root_graph, edge, slot_name,
                                     source_name, reverse_orientation);
  }
}

static rendered_edge_identity_t
project_rendered_edge_identity(Agedge_t *edge, bool reverse_orientation) {
  Agraph_t *const root_graph = agroot(agraphof(edge));
  agxbuf signature = {0};

  append_structured_label_slots(&signature, edge, reverse_orientation);
  append_structured_port_slots(&signature, edge, reverse_orientation);
  append_ordinary_attribute_slots(&signature, root_graph, edge,
                                  reverse_orientation);
  append_taper_direction_slot(&signature, edge, reverse_orientation);
  append_endpoint_attribute_slots(&signature, root_graph, edge,
                                  reverse_orientation);
  append_hyperlink_slots(&signature, root_graph, edge, reverse_orientation);

  return (rendered_edge_identity_t){.text = agxbdisown(&signature)};
}

static bool edge_rendered_identities_are_equal(Agedge_t *first_edge,
                                               Agedge_t *second_edge,
                                               bool reverse_second_edge) {
  rendered_edge_identity_t first_identity =
      project_rendered_edge_identity(first_edge, false);
  rendered_edge_identity_t second_identity =
      project_rendered_edge_identity(second_edge, reverse_second_edge);
  const bool equal = strcmp(first_identity.text, second_identity.text) == 0;
  free(first_identity.text);
  free(second_identity.text);
  return equal;
}

static bool port_values_are_equal(port first_port, port second_port) {
  return first_port.defined == second_port.defined &&
         (!first_port.defined || (first_port.p.x == second_port.p.x &&
                                  first_port.p.y == second_port.p.y));
}

bool gv_edge_ports_are_equal(Agedge_t *first_edge, Agedge_t *second_edge) {
  for (edge_endpoint_t endpoint = EDGE_TAIL_ENDPOINT;
       endpoint < EDGE_ENDPOINT_COUNT; endpoint++) {
    if (!port_values_are_equal(edge_endpoint_port(first_edge, endpoint),
                               edge_endpoint_port(second_edge, endpoint))) {
      return false;
    }
  }
  return true;
}

bool gv_opposite_edge_ports_are_equal(Agedge_t *first_edge,
                                      Agedge_t *second_edge) {
  for (edge_endpoint_t first_endpoint = EDGE_TAIL_ENDPOINT;
       first_endpoint < EDGE_ENDPOINT_COUNT; first_endpoint++) {
    const edge_endpoint_t second_endpoint = first_endpoint == EDGE_HEAD_ENDPOINT
                                                ? EDGE_TAIL_ENDPOINT
                                                : EDGE_HEAD_ENDPOINT;
    if (!port_values_are_equal(
            edge_endpoint_port(first_edge, first_endpoint),
            edge_endpoint_port(second_edge, second_endpoint))) {
      return false;
    }
  }
  return true;
}

bool gv_edge_attributes_are_equal(Agedge_t *first_edge, Agedge_t *second_edge) {
  return edge_rendered_identities_are_equal(first_edge, second_edge, false);
}

bool gv_opposite_edge_attributes_are_equal(Agedge_t *first_edge,
                                           Agedge_t *second_edge) {
  return edge_rendered_identities_are_equal(first_edge, second_edge, true);
}
