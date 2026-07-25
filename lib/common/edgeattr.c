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

static unsigned char html_table_identity_pad(const htmltbl_t *table) {
  return (table->data.flags & PAD_SET) ? table->data.pad
                                       : DEFAULT_HTML_CELLPADDING;
}

static unsigned short html_table_identity_flags(const htmltbl_t *table) {
  unsigned short flags = table->data.flags;
  if (html_table_identity_pad(table) == DEFAULT_HTML_CELLPADDING) {
    flags &= (unsigned short)~PAD_SET;
  }
  if (table->data.space == DEFAULT_HTML_CELLSPACING) {
    flags &= (unsigned short)~SPACE_SET;
  }
  if (table->data.border == DEFAULT_HTML_BORDER) {
    flags &= (unsigned short)~BORDER_SET;
  }
  if (table->data.border == 0) {
    flags &= (unsigned short)~BORDER_MASK;
  }
  return flags;
}

static unsigned short html_data_effective_flags(const htmldata_t *data) {
  unsigned short flags = data->flags;
  if (data->pad == DEFAULT_HTML_CELLPADDING) {
    flags &= (unsigned short)~PAD_SET;
  }
  if (data->border == DEFAULT_HTML_BORDER) {
    flags &= (unsigned short)~BORDER_SET;
  }
  if (data->border == 0) {
    flags &= (unsigned short)~BORDER_MASK;
  }
  return flags;
}

static void append_html_bgcolor_identity_slot(agxbuf *signature,
                                              const char *slot_name,
                                              Agedge_t *edge,
                                              const char *bgcolor) {
  if (bgcolor == NULL || bgcolor[0] == '\0') {
    append_plain_signature_slot(signature, slot_name, "");
    return;
  }

  char *colors[2] = {0};
  double fraction = 0.0;
  if (findStopColor(bgcolor, colors, &fraction)) {
    agxbuf rendered_gradient = {0};
    char *const previous_color_scheme =
        setColorScheme(agget(edge, "colorscheme"));
    append_color_segment(
        &rendered_gradient,
        (strview_t){.data = colors[0], .size = strlen(colors[0])});
    agxbputc(&rendered_gradient, ':');
    const char *const stop = colors[1] != NULL ? colors[1] : DEFAULT_COLOR;
    append_color_segment(&rendered_gradient,
                         (strview_t){.data = stop, .size = strlen(stop)});
    agxbprint(&rendered_gradient, ";%a", fraction);
    char *const restored_color_scheme = setColorScheme(previous_color_scheme);
    free(previous_color_scheme);
    free(restored_color_scheme);
    append_plain_signature_slot(signature, slot_name,
                                agxbuse(&rendered_gradient));
    agxbfree(&rendered_gradient);
    free(colors[0]);
    free(colors[1]);
    return;
  }

  append_edge_color_value(signature, edge, slot_name,
                          plain_attribute_value(bgcolor), false, false);
}

static void append_html_data_identity_slots(
    agxbuf *signature, const char *slot_prefix, Agedge_t *edge,
    const htmldata_t *data, signed char layout_space, unsigned char layout_pad,
    unsigned short layout_flags, bool pencolor_visible) {
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
                              (layout_flags & BORDER_SET) ? "true" : "false");

  agxbclear(&field);
  agxbprint(&field, "%s:pencolor", slot_prefix);
  const bool paint_visible = !data->style.invisible;
  append_edge_color_value(signature, edge, agxbuse(&field),
                          plain_attribute_value(!paint_visible ||
                                                        !pencolor_visible ||
                                                        data->pencolor == NULL
                                                    ? ""
                                                    : data->pencolor),
                          false, false);

  agxbclear(&field);
  agxbprint(&field, "%s:bgcolor", slot_prefix);
  append_html_bgcolor_identity_slot(
      signature, agxbuse(&field), edge,
      !paint_visible || data->bgcolor == NULL ? "" : data->bgcolor);

  agxbclear(&field);
  agxbprint(&field, "%s:gradientangle", slot_prefix);
  agxbprint(&rendered_number, "%d",
            !paint_visible || data->bgcolor == NULL || data->bgcolor[0] == '\0'
                ? 0
                : data->gradientangle);
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
  agxbprint(&rendered_number, "%d:%u:%hhu:%hhu:%a:%a", layout_space,
            data->border, layout_pad, data->sides, data->box.UR.x,
            data->box.UR.y);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbclear(&rendered_number);

  agxbclear(&field);
  agxbprint(&field, "%s:flags", slot_prefix);
  agxbprint(&rendered_number, "%hu", layout_flags);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));
  agxbclear(&rendered_number);

  agxbclear(&field);
  agxbprint(&field, "%s:style", slot_prefix);
  const bool border_style_visible = data->border > 0 && !data->style.invisible;
  const bool fill_style_visible = data->bgcolor != NULL &&
                                  data->bgcolor[0] != '\0' &&
                                  !data->style.invisible;
  agxbprint(&rendered_number, "%d:%d:%d:%d:%d",
            fill_style_visible && data->style.radial,
            data->style.rounded && (border_style_visible || fill_style_visible),
            data->style.invisible, border_style_visible && data->style.dotted,
            border_style_visible && data->style.dashed);
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
static bool html_label_has_visible_pen(const htmllabel_t *label);
static bool html_label_may_use_fallback_font(const htmllabel_t *label);

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
      append_signature_slot(
          signature, agxbuse(&field),
          (comparable_attribute_value_t){.text = item->str, .is_html = true});
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
  const char *const scale =
      image->scale != NULL ? image->scale : fallback_imagescale;
  const char *canonical_scale = "false";
  if (scale != NULL) {
    if (strcasecmp(scale, "width") == 0) {
      canonical_scale = "width";
    } else if (strcasecmp(scale, "height") == 0) {
      canonical_scale = "height";
    } else if (strcasecmp(scale, "both") == 0) {
      canonical_scale = "both";
    } else if (mapbool(scale)) {
      canonical_scale = "true";
    }
  }
  append_plain_signature_slot(signature, agxbuse(&field), canonical_scale);
  agxbfree(&field);
}

static void append_html_table_identity_slots(agxbuf *signature, Agedge_t *edge,
                                             const char *slot_prefix,
                                             const htmltbl_t *table,
                                             const char *fallback_imagescale) {
  append_html_data_identity_slots(
      signature, slot_prefix, edge, &table->data, table->data.space,
      html_table_identity_pad(table), html_table_identity_flags(table), true);

  agxbuf field = {0};
  agxbprint(&field, "%s:shape", slot_prefix);
  agxbuf rendered_number = {0};
  agxbprint(&rendered_number, "%zu:%zu:%d:%d", table->row_count,
            table->column_count, table->hrule, table->vrule);
  append_plain_signature_slot(signature, agxbuse(&field),
                              agxbuse(&rendered_number));

  if (!table->data.style.invisible && table->cells != NULL) {
    for (htmlcell_t **cell = table->cells; *cell != NULL; cell++) {
      agxbclear(&field);
      agxbprint(&field, "%s:cell:%u:%u", slot_prefix, (*cell)->row,
                (*cell)->col);
      const bool cell_pencolor_visible =
          !(*cell)->data.style.invisible &&
          ((*cell)->data.border > 0 ||
           ((*cell)->child.kind == HTML_TBL &&
            html_label_has_visible_pen(&(*cell)->child)));
      append_html_data_identity_slots(
          signature, agxbuse(&field), edge, &(*cell)->data, table->data.space,
          (*cell)->data.pad, html_data_effective_flags(&(*cell)->data),
          cell_pencolor_visible);
      agxbclear(&rendered_number);
      agxbprint(&rendered_number, "%u:%u:%d:%d", (*cell)->rowspan,
                (*cell)->colspan, (*cell)->hruled, (*cell)->vruled);
      agxbput(&field, ":span");
      append_plain_signature_slot(signature, agxbuse(&field),
                                  agxbuse(&rendered_number));

      if (!(*cell)->data.style.invisible) {
        agxbclear(&field);
        agxbprint(&field, "%s:cell:%u:%u:child", slot_prefix, (*cell)->row,
                  (*cell)->col);
        append_html_label_identity_slots(signature, edge, agxbuse(&field),
                                         &(*cell)->child, fallback_imagescale);
      }
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

static bool html_text_may_use_fallback_font(const htmltxt_t *text) {
  for (size_t i = 0; i < text->nspans; i++) {
    const htextspan_t *span = &text->spans[i];
    for (size_t j = 0; j < span->nitems; j++) {
      const textspan_t *item = &span->items[j];
      if (item->font == NULL || item->font->name == NULL ||
          item->font->color == NULL || item->font->size == 0.0) {
        return true;
      }
    }
  }
  return false;
}

static bool html_table_may_use_fallback_font(const htmltbl_t *table) {
  if (table->cells == NULL) {
    return false;
  }
  for (htmlcell_t **cell = table->cells; *cell != NULL; cell++) {
    if (html_label_may_use_fallback_font(&(*cell)->child)) {
      return true;
    }
  }
  return false;
}

static bool html_label_may_use_fallback_font(const htmllabel_t *label) {
  switch (label->kind) {
  case HTML_TBL:
    return html_table_may_use_fallback_font(label->u.tbl);
  case HTML_TEXT:
    return html_text_may_use_fallback_font(label->u.txt);
  case HTML_IMAGE:
  case HTML_UNSET:
    return false;
  }
  return false;
}

static bool color_may_use_colorscheme(const char *color) {
  if (color == NULL) {
    return false;
  }
  for (const char *item = color; *item != '\0'; item++) {
    if (item == color || item[-1] == ':') {
      while (*item == ' ' || *item == '\t') {
        item++;
      }
      if (*item >= '0' && *item <= '9') {
        return true;
      }
    }
  }
  return false;
}

static bool html_data_may_use_colorscheme(const htmldata_t *data) {
  return color_may_use_colorscheme(data->pencolor) ||
         color_may_use_colorscheme(data->bgcolor);
}

static bool html_label_may_use_colorscheme(const htmllabel_t *label);

static bool html_text_may_use_colorscheme(const htmltxt_t *text) {
  for (size_t i = 0; i < text->nspans; i++) {
    const htextspan_t *span = &text->spans[i];
    for (size_t j = 0; j < span->nitems; j++) {
      const textspan_t *item = &span->items[j];
      if (item->font != NULL && color_may_use_colorscheme(item->font->color)) {
        return true;
      }
    }
  }
  return false;
}

static bool html_table_may_use_colorscheme(const htmltbl_t *table) {
  if (html_data_may_use_colorscheme(&table->data) ||
      (table->font != NULL && color_may_use_colorscheme(table->font->color))) {
    return true;
  }
  if (table->cells == NULL) {
    return false;
  }
  for (htmlcell_t **cell = table->cells; *cell != NULL; cell++) {
    if (html_data_may_use_colorscheme(&(*cell)->data) ||
        html_label_may_use_colorscheme(&(*cell)->child)) {
      return true;
    }
  }
  return false;
}

static bool html_label_may_use_colorscheme(const htmllabel_t *label) {
  switch (label->kind) {
  case HTML_TBL:
    return html_table_may_use_colorscheme(label->u.tbl);
  case HTML_TEXT:
    return html_text_may_use_colorscheme(label->u.txt);
  case HTML_IMAGE:
  case HTML_UNSET:
    return false;
  }
  return false;
}

static bool html_data_has_visible_pen(const htmldata_t *data) {
  return !data->style.invisible && data->border > 0;
}

static bool html_table_has_visible_pen(const htmltbl_t *table) {
  if (html_data_has_visible_pen(&table->data)) {
    return true;
  }
  if (table->cells == NULL) {
    return false;
  }
  for (htmlcell_t **cell = table->cells; *cell != NULL; cell++) {
    if (html_data_has_visible_pen(&(*cell)->data) ||
        html_label_has_visible_pen(&(*cell)->child)) {
      return true;
    }
  }
  return false;
}

static bool html_label_has_visible_pen(const htmllabel_t *label) {
  switch (label->kind) {
  case HTML_TBL:
    return html_table_has_visible_pen(label->u.tbl);
  case HTML_TEXT:
  case HTML_IMAGE:
  case HTML_UNSET:
    return false;
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
                                              bool parent_anchor_open,
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
                                             bool parent_anchor_open,
                                             size_t *anchor_index) {
  const bool has_url = data->href != NULL && data->href[0] != '\0';
  const bool has_tooltip = data->title != NULL && data->title[0] != '\0';
  const bool has_target = data->target != NULL && data->target[0] != '\0';
  if (!has_url && !has_tooltip && (!parent_anchor_open || !has_target)) {
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
                                              bool parent_anchor_open,
                                              size_t *anchor_index) {
  const bool table_anchor_open =
      parent_anchor_open ||
      (table->data.href != NULL && table->data.href[0] != '\0') ||
      (table->data.title != NULL && table->data.title[0] != '\0');
  append_html_data_hyperlink_slots(signature, edge, slot_prefix, &table->data,
                                   parent_anchor_open, anchor_index);
  if (table->cells == NULL) {
    return;
  }
  agxbuf field = {0};
  for (htmlcell_t **cell = table->cells; *cell != NULL; cell++) {
    const bool cell_anchor_open =
        table_anchor_open ||
        ((*cell)->data.href != NULL && (*cell)->data.href[0] != '\0') ||
        ((*cell)->data.title != NULL && (*cell)->data.title[0] != '\0');
    agxbclear(&field);
    agxbprint(&field, "%s:cell:%u:%u", slot_prefix, (*cell)->row, (*cell)->col);
    append_html_data_hyperlink_slots(signature, edge, agxbuse(&field),
                                     &(*cell)->data, table_anchor_open,
                                     anchor_index);
    agxbput(&field, ":child");
    append_html_label_hyperlink_slots(signature, edge, agxbuse(&field),
                                      &(*cell)->child, cell_anchor_open,
                                      anchor_index);
  }
  agxbfree(&field);
}

static void append_html_label_hyperlink_slots(agxbuf *signature, Agedge_t *edge,
                                              const char *slot_prefix,
                                              const htmllabel_t *label,
                                              bool parent_anchor_open,
                                              size_t *anchor_index) {
  switch (label->kind) {
  case HTML_TBL:
    append_html_table_hyperlink_slots(signature, edge, slot_prefix,
                                      label->u.tbl, parent_anchor_open,
                                      anchor_index);
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
normalized_color_segments(const char *color_list, bool skip_empty_segments,
                          size_t *segment_count) {
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
    if (skip_empty_segments && color.size == 0) {
      if (segment_end == NULL) {
        break;
      }
      segment_start = segment_end + 1;
      continue;
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

static size_t raw_color_lane_count(const char *color_list) {
  size_t lane_count = 1;
  for (const char *p = color_list; *p != '\0'; p++) {
    if (*p == ':') {
      lane_count++;
    }
  }
  return lane_count;
}

static bool color_list_renders_as_segmented_multicolor(const char *color_list) {
  return color_list != NULL && strchr(color_list, ';') != NULL &&
         strchr(color_list, ':') != NULL;
}

static void append_edge_color_list_value(agxbuf *signature, Agedge_t *edge,
                                         const char *slot_name,
                                         const char *color_list,
                                         bool reverse_orientation) {
  const bool has_explicit_segments =
      color_list_has_explicit_segments(color_list);
  const size_t raw_lane_count =
      has_explicit_segments ? 0 : raw_color_lane_count(color_list);
  size_t segment_count = 0;
  normalized_color_segment_t *const segments = normalized_color_segments(
      color_list, !has_explicit_segments, &segment_count);
  if (segments == NULL) {
    append_plain_signature_slot(signature, slot_name, color_list);
    return;
  }

  agxbuf rendered_list = {0};
  char *const previous_color_scheme =
      setColorScheme(agget(edge, "colorscheme"));

  const bool renders_as_single_color = segment_count == 1 &&
                                       segments[0].fraction > 1.0 - 1E-5 &&
                                       segments[0].fraction < 1.0 + 1E-5;
  if (renders_as_single_color) {
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
  if (raw_lane_count > 1) {
    agxbuf count_slot_name = {0};
    agxbprint(&count_slot_name, "%s:lane-count", slot_name);
    agxbuf rendered_count = {0};
    agxbprint(&rendered_count, "%zu", raw_lane_count);
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
    if (html_label_may_use_colorscheme(label->u.html)) {
      agxbclear(&slot_name);
      agxbprint(&slot_name, "%s:colorscheme", slot_prefix);
      const char *colorscheme = agget(edge, "colorscheme");
      append_plain_signature_slot(signature, agxbuse(&slot_name),
                                  colorscheme == NULL ? "" : colorscheme);
    }
    size_t anchor_index = 0;
    append_html_label_hyperlink_slots(signature, edge, slot_prefix,
                                      label->u.html, false, &anchor_index);
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

  if (label->html && !html_label_may_use_fallback_font(label->u.html)) {
    agxbfree(&slot_name);
    return;
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
    *number = default_value;
    return true;
  }
  if (!isfinite(parsed_value)) {
    *number = default_value;
    return true;
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

static bool
edge_uses_simple_color_ortho_branch(Agraph_t *root_graph, Agedge_t *edge,
                                    comparable_attribute_value_t style);

static bool edge_style_token_sets_pen_pattern(const char *style) {
  return strcmp(style, "solid") == 0 || strcmp(style, "dashed") == 0 ||
         strcmp(style, "dotted") == 0 || strcmp(style, "invis") == 0;
}

static const char *canonical_edge_style_pen_pattern(const char *style) {
  return style;
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
      pen_pattern = canonical_edge_style_pen_pattern(*item);
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

