/// @file
/// @brief Rendered edge identity projection for concentration

/*
 * Concentration projects each edge into two parts: the core rendered identity
 * built here, and the edge's own per-physical-endpoint arrow decorations built
 * in lib/common/arrows.c. The core resolves defaults, aliases, inheritance,
 * substitutions, colors, numbers, HTML flags, and physical endpoint
 * orientation. Unrendered attributes contribute no slot, and reverse
 * projection swaps head and tail at extraction time.
 *
 * The arrow comparison deliberately depends on direction. Same-direction
 * parallels must LOOK identical, so their own decoration records are compared
 * strictly and an absent arrow is rendered state. Using the neutral arrow fold
 * here would merge forward and both edges, and would again let a retained
 * edge's borrowed reverse arrow hide a distinct later parallel edge.
 * Opposite-direction pairs instead must be COMBINABLE onto one bidirectional
 * route, so the candidate's own decorations are folded against the retained
 * edge's accumulated decorations after swapping physical endpoints. Requiring
 * strict equality there would break arrow-plus-no-arrow merges.
 *
 * Arrow shapes, arrowsize, and fillcolor therefore do not appear in the core
 * signature. The decoration record contains the latter two values only when
 * its edge actually draws an arrow, so invisible declarations on arrow-less
 * endpoints cannot block an opposite-direction merge.
 *
 * The classification and hyperlink tables are the slot-extractor contract.
 * Keeping every exception in those rows or in one normalizer is longer than a
 * collection of strcmp() shortcuts, but makes defaults, ownership, and endpoint
 * orientation reviewable without reconstructing a pairwise decision tree.
 */

#include "config.h"

#include <common/colorprocs.h>
#include <common/const.h>
#include <common/edgeattr.h>
#include <common/render.h>
#include <common/utils.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <util/agxbuf.h>

#define ATTRIBUTE_COUNT(attributes)                                            \
  (sizeof(attributes) / sizeof((attributes)[0]))

typedef struct {
  const char *text;
  bool is_html;
} comparable_attribute_value_t;

typedef struct {
  char *text;
} rendered_edge_identity_t;

typedef enum {
  ATTRIBUTE_ALIAS_NONE,
  ATTRIBUTE_ALIAS_URL,
  ATTRIBUTE_ALIAS_TOOLTIP,
  ATTRIBUTE_ALIAS_TARGET,
} attribute_alias_group_t;

typedef enum {
  ATTRIBUTE_DEFAULT_NONE,
  ATTRIBUTE_DEFAULT_ONE,
  ATTRIBUTE_DEFAULT_FONT_SIZE,
  ATTRIBUTE_DEFAULT_LABEL_FONT_SIZE,
  ATTRIBUTE_DEFAULT_LABEL_ANGLE,
  ATTRIBUTE_DEFAULT_FALSE,
} attribute_default_kind_t;

typedef enum {
  ATTRIBUTE_RENDER_ALWAYS,
  ATTRIBUTE_RENDER_ANY_LABEL,
  ATTRIBUTE_RENDER_MAIN_LABEL,
  ATTRIBUTE_RENDER_PRIMARY_LABEL,
  ATTRIBUTE_RENDER_ENDPOINT_LABEL,
  ATTRIBUTE_RENDER_LAYOUT_ONLY,
  ATTRIBUTE_RENDER_ARROW_DECORATION,
  ATTRIBUTE_RENDER_COLOR_TRANSLATOR,
} attribute_render_scope_t;

typedef struct {
  const char *name;
  attribute_alias_group_t alias_group;
  attribute_default_kind_t default_kind;
  attribute_render_scope_t render_scope;
  bool color : 1;
  bool label_color : 1;
  bool clipping : 1;
  bool endpoint_label : 1;
  bool substituted : 1;
  bool fontname : 1;
  bool port : 1;
  bool endpoint : 1;
  bool style : 1;
  bool fill_color : 1;
  bool font_color : 1;
  bool label_font_color : 1;
  bool label_fontname : 1;
  bool compound_only : 1;
  bool base_font : 1;
} edge_attribute_classification_t;

/*
 * Rows classify declared edge attributes into independently projected slots.
 * Attributes absent from the table are ordinary rendered slots. Arrow
 * decoration rows remain explicit so they cannot fall through into identity.
 */
static const edge_attribute_classification_t edge_attribute_classifications[] =
    {
        {.name = "URL", .alias_group = ATTRIBUTE_ALIAS_URL},
        {.name = "arrowhead",
         .render_scope = ATTRIBUTE_RENDER_ARROW_DECORATION},
        {.name = "arrowsize",
         .render_scope = ATTRIBUTE_RENDER_ARROW_DECORATION},
        {.name = "arrowtail",
         .render_scope = ATTRIBUTE_RENDER_ARROW_DECORATION},
        {.name = "color", .color = true},
        {.name = "colorscheme",
         .render_scope = ATTRIBUTE_RENDER_COLOR_TRANSLATOR},
        {.name = "constraint", .render_scope = ATTRIBUTE_RENDER_LAYOUT_ONLY},
        /* emit_end_edge() attaches decorate splines only to label/xlabel. */
        {.name = "decorate",
         .default_kind = ATTRIBUTE_DEFAULT_FALSE,
         .render_scope = ATTRIBUTE_RENDER_MAIN_LABEL},
        {.name = "dir", .render_scope = ATTRIBUTE_RENDER_ARROW_DECORATION},
        {.name = "edgeURL", .alias_group = ATTRIBUTE_ALIAS_URL},
        {.name = "edgehref", .alias_group = ATTRIBUTE_ALIAS_URL},
        {.name = "edgetarget", .alias_group = ATTRIBUTE_ALIAS_TARGET},
        {.name = "edgetooltip", .alias_group = ATTRIBUTE_ALIAS_TOOLTIP},
        {.name = "fillcolor",
         .render_scope = ATTRIBUTE_RENDER_ARROW_DECORATION,
         .color = true,
         .fill_color = true},
        {.name = "fontcolor",
         .render_scope = ATTRIBUTE_RENDER_ANY_LABEL,
         .color = true,
         .label_color = true,
         .font_color = true},
        {.name = "fontname",
         .render_scope = ATTRIBUTE_RENDER_ANY_LABEL,
         .fontname = true,
         .base_font = true},
        {.name = "fontsize",
         .default_kind = ATTRIBUTE_DEFAULT_FONT_SIZE,
         .render_scope = ATTRIBUTE_RENDER_ANY_LABEL,
         .base_font = true},
        {.name = "headURL",
         .alias_group = ATTRIBUTE_ALIAS_URL,
         .endpoint = true},
        {.name = "headclip", .clipping = true, .endpoint = true},
        {.name = "headhref",
         .alias_group = ATTRIBUTE_ALIAS_URL,
         .endpoint = true},
        {.name = "headlabel", .endpoint_label = true, .endpoint = true},
        {.name = "headport", .port = true, .endpoint = true},
        {.name = "headtarget",
         .alias_group = ATTRIBUTE_ALIAS_TARGET,
         .endpoint = true},
        {.name = "headtooltip",
         .alias_group = ATTRIBUTE_ALIAS_TOOLTIP,
         .substituted = true,
         .endpoint = true},
        {.name = "href", .alias_group = ATTRIBUTE_ALIAS_URL},
        {.name = "id", .substituted = true},
        {.name = "labelURL", .alias_group = ATTRIBUTE_ALIAS_URL},
        /* place_portlabel() reads these only for headlabel/taillabel. */
        {.name = "labelangle",
         .default_kind = ATTRIBUTE_DEFAULT_LABEL_ANGLE,
         .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL},
        {.name = "labeldistance",
         .default_kind = ATTRIBUTE_DEFAULT_ONE,
         .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL},
        /* common_init_edge() reads labelfloat only while creating ED_label. */
        {.name = "labelfloat",
         .default_kind = ATTRIBUTE_DEFAULT_FALSE,
         .render_scope = ATTRIBUTE_RENDER_PRIMARY_LABEL},
        /* initFontLabelEdgeAttr() supplies only headlabel/taillabel fonts. */
        {.name = "labelfontcolor",
         .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL,
         .color = true,
         .label_color = true,
         .label_font_color = true},
        {.name = "labelfontname",
         .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL,
         .fontname = true,
         .label_fontname = true},
        {.name = "labelfontsize",
         .default_kind = ATTRIBUTE_DEFAULT_LABEL_FONT_SIZE,
         .render_scope = ATTRIBUTE_RENDER_ENDPOINT_LABEL},
        {.name = "labelhref", .alias_group = ATTRIBUTE_ALIAS_URL},
        {.name = "labeltarget", .alias_group = ATTRIBUTE_ALIAS_TARGET},
        {.name = "labeltooltip",
         .alias_group = ATTRIBUTE_ALIAS_TOOLTIP,
         .substituted = true},
        {.name = "lhead", .endpoint = true, .compound_only = true},
        {.name = "ltail", .endpoint = true, .compound_only = true},
        {.name = "minlen", .render_scope = ATTRIBUTE_RENDER_LAYOUT_ONLY},
        {.name = "penwidth", .default_kind = ATTRIBUTE_DEFAULT_ONE},
        {.name = "samehead", .endpoint = true},
        {.name = "sametail", .endpoint = true},
        {.name = "style", .style = true},
        {.name = "tailURL",
         .alias_group = ATTRIBUTE_ALIAS_URL,
         .endpoint = true},
        {.name = "tailclip", .clipping = true, .endpoint = true},
        {.name = "tailhref",
         .alias_group = ATTRIBUTE_ALIAS_URL,
         .endpoint = true},
        {.name = "taillabel", .endpoint_label = true, .endpoint = true},
        {.name = "tailport", .port = true, .endpoint = true},
        {.name = "tailtarget",
         .alias_group = ATTRIBUTE_ALIAS_TARGET,
         .endpoint = true},
        {.name = "tailtooltip",
         .alias_group = ATTRIBUTE_ALIAS_TOOLTIP,
         .substituted = true,
         .endpoint = true},
        {.name = "target", .alias_group = ATTRIBUTE_ALIAS_TARGET},
        {.name = "tooltip", .alias_group = ATTRIBUTE_ALIAS_TOOLTIP},
        {.name = "weight", .render_scope = ATTRIBUTE_RENDER_LAYOUT_ONLY},
};

typedef enum {
  EDGE_TAIL_ENDPOINT,
  EDGE_HEAD_ENDPOINT,
  EDGE_ENDPOINT_COUNT,
} edge_endpoint_t;

typedef struct {
  const char *names[EDGE_ENDPOINT_COUNT];
} endpoint_attribute_names_t;

static const endpoint_attribute_names_t endpoint_attribute_names[] = {
    {.names = {"tailURL", "headURL"}},
    {.names = {"tailclip", "headclip"}},
    {.names = {"tailhref", "headhref"}},
    {.names = {"taillabel", "headlabel"}},
    {.names = {"tailport", "headport"}},
    {.names = {"ltail", "lhead"}},
    {.names = {"sametail", "samehead"}},
    {.names = {"tailtarget", "headtarget"}},
    {.names = {"tailtooltip", "headtooltip"}},
};

static const edge_attribute_classification_t *
edge_attribute_classification(const char *name) {
  for (size_t attribute_index = 0;
       attribute_index < ATTRIBUTE_COUNT(edge_attribute_classifications);
       attribute_index++) {
    const edge_attribute_classification_t *const classification =
        &edge_attribute_classifications[attribute_index];
    if (strcmp(name, classification->name) == 0) {
      return classification;
    }
  }
  return NULL;
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

static comparable_attribute_value_t
named_first_nonempty_attribute_value(Agraph_t *root_graph, Agedge_t *edge,
                                     const char *const *attribute_names,
                                     size_t attribute_names_size) {
  comparable_attribute_value_t empty_value = plain_attribute_value("");

  for (size_t attribute_index = 0; attribute_index < attribute_names_size;
       attribute_index++) {
    const comparable_attribute_value_t value = named_attribute_value(
        root_graph, edge, attribute_names[attribute_index]);
    if (value.text[0] != '\0') {
      return value;
    }
    empty_value = value;
  }
  return empty_value;
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

static bool edge_has_any_of_attributes(Agraph_t *root_graph, Agedge_t *edge,
                                       const char *const *attribute_names,
                                       size_t attribute_names_size) {
  for (size_t attribute_index = 0; attribute_index < attribute_names_size;
       attribute_index++) {
    if (named_attribute_value(root_graph, edge,
                              attribute_names[attribute_index])
            .text[0] != '\0') {
      return true;
    }
  }
  return false;
}

static bool edge_has_main_label(Agraph_t *root_graph, Agedge_t *edge) {
  static const char *const main_labels[] = {"label", "xlabel"};
  return edge_has_any_of_attributes(root_graph, edge, main_labels,
                                    ATTRIBUTE_COUNT(main_labels));
}

static bool edge_has_endpoint_label(Agraph_t *root_graph, Agedge_t *edge) {
  static const char *const endpoint_labels[] = {"headlabel", "taillabel"};
  return edge_has_any_of_attributes(root_graph, edge, endpoint_labels,
                                    ATTRIBUTE_COUNT(endpoint_labels));
}

static bool
edge_uses_label_color(Agraph_t *root_graph, Agedge_t *edge,
                      const edge_attribute_classification_t *classification) {
  if (classification->font_color) {
    return edge_has_main_label(root_graph, edge) ||
           (edge_has_endpoint_label(root_graph, edge) &&
            named_attribute_value(root_graph, edge, "labelfontcolor").text[0] ==
                '\0');
  }
  if (classification->label_font_color) {
    return edge_has_endpoint_label(root_graph, edge);
  }
  return true;
}

static bool
edge_uses_base_font(Agraph_t *root_graph, Agedge_t *edge,
                    const edge_attribute_classification_t *classification) {
  if (edge_has_main_label(root_graph, edge)) {
    return true;
  }
  if (!edge_has_endpoint_label(root_graph, edge)) {
    return false;
  }

  const char *const endpoint_override =
      classification->fontname ? "labelfontname" : "labelfontsize";
  return agfindedgeattr(root_graph, (char *)endpoint_override) == NULL;
}

static bool edge_attribute_is_rendered(
    Agraph_t *root_graph, Agedge_t *edge,
    const edge_attribute_classification_t *classification) {
  if (classification == NULL) {
    return true;
  }
  if (classification->alias_group != ATTRIBUTE_ALIAS_NONE ||
      classification->render_scope == ATTRIBUTE_RENDER_LAYOUT_ONLY ||
      classification->render_scope == ATTRIBUTE_RENDER_ARROW_DECORATION ||
      classification->render_scope == ATTRIBUTE_RENDER_COLOR_TRANSLATOR) {
    return false;
  }
  if (classification->render_scope == ATTRIBUTE_RENDER_ANY_LABEL &&
      !edge_has_main_label(root_graph, edge) &&
      !edge_has_endpoint_label(root_graph, edge)) {
    return false;
  }
  if (classification->render_scope == ATTRIBUTE_RENDER_MAIN_LABEL &&
      !edge_has_main_label(root_graph, edge)) {
    return false;
  }
  if (classification->render_scope == ATTRIBUTE_RENDER_PRIMARY_LABEL &&
      named_attribute_value(root_graph, edge, "label").text[0] == '\0') {
    return false;
  }
  if (classification->render_scope == ATTRIBUTE_RENDER_ENDPOINT_LABEL &&
      !edge_has_endpoint_label(root_graph, edge)) {
    return false;
  }
  if (classification->label_color &&
      !edge_uses_label_color(root_graph, edge, classification)) {
    return false;
  }
  if (classification->base_font &&
      !edge_uses_base_font(root_graph, edge, classification)) {
    return false;
  }
  /* dotLayout() calls dot_compoundEdges() only for a truthy compound graph. */
  if (classification->compound_only &&
      !mapbool(agget(root_graph, "compound"))) {
    return false;
  }
  return true;
}

static comparable_attribute_value_t edge_color_default_value(
    Agraph_t *root_graph, Agedge_t *edge,
    const edge_attribute_classification_t *classification) {
  if (classification->fill_color) {
    const comparable_attribute_value_t color =
        named_attribute_value(root_graph, edge, "color");
    return color.text[0] == '\0' ? plain_attribute_value(DEFAULT_COLOR) : color;
  }
  if (classification->label_font_color) {
    const comparable_attribute_value_t fontcolor =
        named_attribute_value(root_graph, edge, "fontcolor");
    return fontcolor.text[0] == '\0' ? plain_attribute_value(DEFAULT_COLOR)
                                     : fontcolor;
  }
  return plain_attribute_value(DEFAULT_COLOR);
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

static void append_edge_color_list_value(agxbuf *signature, Agedge_t *edge,
                                         const char *slot_name,
                                         const char *color_list) {
  agxbuf rendered_list = {0};
  char *const previous_color_scheme =
      setColorScheme(agget(edge, "colorscheme"));

  for (const char *segment = color_list; segment != NULL;) {
    const char *const separator = strchr(segment, ':');
    const char *const segment_end =
        separator == NULL ? segment + strlen(segment) : separator;
    const char *const fraction = memchr(segment, ';', segment_end - segment);
    const char *const color_end = fraction == NULL ? segment_end : fraction;

    agxbuf color_name = {0};
    agxbput_n(&color_name, segment, color_end - segment);
    gvcolor_t color;
    if (colorxlate(agxbuse(&color_name), &color, RGBA_BYTE) == COLOR_OK) {
      agxbprint(&rendered_list, "#%02x%02x%02x%02x", color.u.rgba[0],
                color.u.rgba[1], color.u.rgba[2], color.u.rgba[3]);
    } else {
      agxbput_n(&rendered_list, segment, color_end - segment);
    }
    agxbfree(&color_name);
    agxbput_n(&rendered_list, color_end, segment_end - color_end);

    if (separator == NULL) {
      break;
    }
    agxbputc(&rendered_list, ':');
    segment = separator + 1;
  }

  char *const restored_color_scheme = setColorScheme(previous_color_scheme);
  free(previous_color_scheme);
  free(restored_color_scheme);
  append_plain_signature_slot(signature, slot_name, agxbuse(&rendered_list));
  agxbfree(&rendered_list);
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
  if (end == value.text || *end != '\0') {
    return false;
  }
  *number = parsed_value < minimum ? minimum : parsed_value;
  return true;
}

static bool edge_numeric_projected_value(
    Agraph_t *root_graph, Agedge_t *edge,
    const edge_attribute_classification_t *classification,
    comparable_attribute_value_t value, double *number) {
  if (classification == NULL) {
    return false;
  }
  double default_value;
  double minimum = 0.0;

  if (classification->default_kind == ATTRIBUTE_DEFAULT_ONE) {
    default_value = 1.0;
  } else if (classification->default_kind == ATTRIBUTE_DEFAULT_FONT_SIZE) {
    default_value = DEFAULT_FONTSIZE;
    minimum = MIN_FONTSIZE;
  } else if (classification->default_kind ==
             ATTRIBUTE_DEFAULT_LABEL_FONT_SIZE) {
    if (!edge_numeric_attribute_value(
            named_attribute_value(root_graph, edge, "fontsize"),
            DEFAULT_FONTSIZE, MIN_FONTSIZE, &default_value)) {
      return false;
    }
    minimum = MIN_FONTSIZE;
  } else if (classification->default_kind == ATTRIBUTE_DEFAULT_LABEL_ANGLE) {
    default_value = PORT_LABEL_ANGLE;
    minimum = -180.0;
  } else {
    return false;
  }

  return edge_numeric_attribute_value(value, default_value, minimum, number);
}

static comparable_attribute_value_t edge_fontname_default_value(
    Agraph_t *root_graph, Agedge_t *edge,
    const edge_attribute_classification_t *classification) {
  if (classification->label_fontname) {
    const comparable_attribute_value_t fontname =
        named_attribute_value(root_graph, edge, "fontname");
    return fontname.text[0] == '\0' ? plain_attribute_value(DEFAULT_FONTNAME)
                                    : fontname;
  }
  return plain_attribute_value(DEFAULT_FONTNAME);
}

static port edge_endpoint_port(Agedge_t *edge, edge_endpoint_t endpoint) {
  return endpoint == EDGE_HEAD_ENDPOINT ? ED_head_port(edge)
                                        : ED_tail_port(edge);
}

static void append_projected_attribute_value(agxbuf *signature,
                                             Agraph_t *root_graph,
                                             Agedge_t *edge,
                                             const char *slot_name,
                                             const char *attribute_name) {
  comparable_attribute_value_t value =
      named_attribute_value(root_graph, edge, attribute_name);
  const edge_attribute_classification_t *const classification =
      edge_attribute_classification(attribute_name);

  if (classification != NULL && classification->port) {
    const edge_endpoint_t endpoint = strcmp(attribute_name, "headport") == 0
                                         ? EDGE_HEAD_ENDPOINT
                                         : EDGE_TAIL_ENDPOINT;
    const port resolved_port = edge_endpoint_port(edge, endpoint);
    if (resolved_port.defined) {
      agxbuf resolved_value = {0};
      agxbprint(&resolved_value, "%a,%a", resolved_port.p.x, resolved_port.p.y);
      append_plain_signature_slot(signature, slot_name,
                                  agxbuse(&resolved_value));
      agxbfree(&resolved_value);
      return;
    }
  }

  if (classification != NULL &&
      (classification->endpoint_label || classification->substituted) &&
      !value.is_html) {
    char *const substituted = strdup_and_subst_obj((char *)value.text, edge);
    append_plain_signature_slot(signature, slot_name, substituted);
    free(substituted);
    return;
  }

  if (classification != NULL && classification->clipping) {
    append_plain_signature_slot(
        signature, slot_name,
        value.text[0] == '\0' || mapbool(value.text) ? "true" : "false");
    return;
  }

  if (classification != NULL && classification->color) {
    if (value.text[0] == '\0') {
      value = edge_color_default_value(root_graph, edge, classification);
    }
    if (!value.is_html && strchr(value.text, ':') != NULL) {
      append_edge_color_list_value(signature, edge, slot_name, value.text);
      return;
    }
    gvcolor_t color;
    if (edge_color_value(edge, value, &color)) {
      agxbuf rendered_color = {0};
      agxbprint(&rendered_color, "#%02x%02x%02x%02x", color.u.rgba[0],
                color.u.rgba[1], color.u.rgba[2], color.u.rgba[3]);
      append_plain_signature_slot(signature, slot_name,
                                  agxbuse(&rendered_color));
      agxbfree(&rendered_color);
      return;
    }
  }

  double number;
  if (edge_numeric_projected_value(root_graph, edge, classification, value,
                                   &number)) {
    agxbuf rendered_number = {0};
    agxbprint(&rendered_number, "%a", number);
    append_plain_signature_slot(signature, slot_name,
                                agxbuse(&rendered_number));
    agxbfree(&rendered_number);
    return;
  }

  if (classification != NULL && classification->fontname &&
      value.text[0] == '\0') {
    value = edge_fontname_default_value(root_graph, edge, classification);
  }

  if (classification != NULL && classification->style && !value.is_html &&
      value.text[0] == '\0') {
    value = plain_attribute_value("solid");
  }

  if (classification != NULL &&
      classification->default_kind == ATTRIBUTE_DEFAULT_FALSE) {
    append_plain_signature_slot(
        signature, slot_name,
        value.text[0] != '\0' && mapbool(value.text) ? "true" : "false");
    return;
  }

  append_signature_slot(signature, slot_name, value);
}

static const char *opposite_endpoint_attribute_name(const char *name) {
  for (size_t pair_index = 0;
       pair_index < ATTRIBUTE_COUNT(endpoint_attribute_names); pair_index++) {
    const endpoint_attribute_names_t *const pair =
        &endpoint_attribute_names[pair_index];
    if (strcmp(name, pair->names[EDGE_TAIL_ENDPOINT]) == 0) {
      return pair->names[EDGE_HEAD_ENDPOINT];
    }
    if (strcmp(name, pair->names[EDGE_HEAD_ENDPOINT]) == 0) {
      return pair->names[EDGE_TAIL_ENDPOINT];
    }
  }
  return name;
}

static void append_ordinary_attribute_slots(agxbuf *signature,
                                            Agraph_t *root_graph,
                                            Agedge_t *edge) {
  for (Agsym_t *attribute = agnxtattr(root_graph, AGEDGE, NULL);
       attribute != NULL;
       attribute = agnxtattr(root_graph, AGEDGE, attribute)) {
    const edge_attribute_classification_t *const classification =
        edge_attribute_classification(attribute->name);
    if ((classification != NULL && classification->endpoint) ||
        !edge_attribute_is_rendered(root_graph, edge, classification)) {
      continue;
    }
    append_projected_attribute_value(signature, root_graph, edge,
                                     attribute->name, attribute->name);
  }
}

static void append_endpoint_attribute_slots(agxbuf *signature,
                                            Agraph_t *root_graph,
                                            Agedge_t *edge,
                                            bool reverse_orientation) {
  /*
   * Iterate canonical physical slots, then select the grammatical source.
   * This emits the undeclared mate too, so a graph declaring only headclip
   * still compares it against tailclip on a reversed candidate.
   */
  for (size_t attribute_index = 0;
       attribute_index < ATTRIBUTE_COUNT(edge_attribute_classifications);
       attribute_index++) {
    const edge_attribute_classification_t *const classification =
        &edge_attribute_classifications[attribute_index];
    if (!classification->endpoint ||
        !edge_attribute_is_rendered(root_graph, edge, classification)) {
      continue;
    }

    const char *const source_name =
        reverse_orientation
            ? opposite_endpoint_attribute_name(classification->name)
            : classification->name;
    append_projected_attribute_value(signature, root_graph, edge,
                                     classification->name, source_name);
  }
}

typedef enum {
  HYPERLINK_VALUE_URL,
  HYPERLINK_VALUE_TOOLTIP,
  HYPERLINK_VALUE_TARGET,
  HYPERLINK_VALUE_COUNT,
} hyperlink_value_kind_t;

typedef enum {
  HYPERLINK_LAYER_EDGE,
  HYPERLINK_LAYER_LABEL,
  HYPERLINK_LAYER_HEAD,
  HYPERLINK_LAYER_TAIL,
  HYPERLINK_LAYER_COUNT,
} hyperlink_layer_id_t;

typedef struct {
  const char *name;
  const char *const *url_names;
  size_t url_names_size;
  const char *const *label_url_names;
  size_t label_url_names_size;
  const char *const *tooltip_names;
  size_t tooltip_names_size;
  const char *const *tooltip_fallback_names;
  size_t tooltip_fallback_names_size;
  const char *const *target_names;
  size_t target_names_size;
  const char *const *label_gate_names;
  size_t label_gate_names_size;
  bool url_uses_label_gate;
  const char *const *target_anchor_names;
  size_t target_anchor_names_size;
  hyperlink_layer_id_t opposite_layer;
} hyperlink_layer_t;

static const char *const edge_url_names[] = {"edgehref", "edgeURL", "href",
                                             "URL"};
static const char *const label_url_names[] = {"labelhref", "labelURL", "href",
                                              "URL"};
/* nodeIntersect() falls back from explicit endpoint URLs to obj->url. */
static const char *const head_url_names[] = {"headhref", "headURL", "edgehref",
                                             "edgeURL",  "href",    "URL"};
static const char *const tail_url_names[] = {"tailhref", "tailURL", "edgehref",
                                             "edgeURL",  "href",    "URL"};
/* emit_begin_edge() gives endpoint labels only the href/URL default. */
static const char *const head_label_url_names[] = {"headhref", "headURL",
                                                   "href", "URL"};
static const char *const tail_label_url_names[] = {"tailhref", "tailURL",
                                                   "href", "URL"};

static const char *const edge_tooltip_names[] = {"tooltip", "edgetooltip"};
static const char *const label_tooltip_names[] = {"labeltooltip"};
static const char *const head_tooltip_names[] = {"headtooltip"};
static const char *const tail_tooltip_names[] = {"tailtooltip"};
static const char *const edge_tooltip_fallback_names[] = {"label"};
static const char *const head_tooltip_fallback_names[] = {"headlabel"};
static const char *const tail_tooltip_fallback_names[] = {"taillabel"};

static const char *const edge_target_names[] = {"edgetarget", "target"};
static const char *const label_target_names[] = {"labeltarget", "target"};
static const char *const head_target_names[] = {"headtarget", "target"};
static const char *const tail_target_names[] = {"tailtarget", "target"};

static const char *const label_gate_names[] = {"label", "xlabel"};
static const char *const head_label_gate_names[] = {"headlabel"};
static const char *const tail_label_gate_names[] = {"taillabel"};

static const char *const edge_target_anchor_names[] = {
    "href", "URL", "edgehref", "edgeURL", "tooltip", "edgetooltip"};
static const char *const label_target_anchor_names[] = {
    "labelhref", "labelURL", "labeltooltip", "href", "URL"};
static const char *const head_target_anchor_names[] = {
    "headhref", "headURL", "headtooltip", "href", "URL"};
static const char *const tail_target_anchor_names[] = {
    "tailhref", "tailURL", "tailtooltip", "href", "URL"};

/*
 * Four rendered hyperlink layers each extract URL, tooltip, and target slots.
 * Endpoint layers select their opposite row during reversed projection. Target
 * anchors stay separate because endpoint targets do not inherit edgeURL.
 */
static const hyperlink_layer_t hyperlink_layers[HYPERLINK_LAYER_COUNT] = {
    [HYPERLINK_LAYER_EDGE] =
        {
            .name = "edge",
            .url_names = edge_url_names,
            .url_names_size = ATTRIBUTE_COUNT(edge_url_names),
            .tooltip_names = edge_tooltip_names,
            .tooltip_names_size = ATTRIBUTE_COUNT(edge_tooltip_names),
            .tooltip_fallback_names = edge_tooltip_fallback_names,
            .tooltip_fallback_names_size =
                ATTRIBUTE_COUNT(edge_tooltip_fallback_names),
            .target_names = edge_target_names,
            .target_names_size = ATTRIBUTE_COUNT(edge_target_names),
            .target_anchor_names = edge_target_anchor_names,
            .target_anchor_names_size =
                ATTRIBUTE_COUNT(edge_target_anchor_names),
            .opposite_layer = HYPERLINK_LAYER_EDGE,
        },
    [HYPERLINK_LAYER_LABEL] =
        {
            .name = "label",
            .url_names = label_url_names,
            .url_names_size = ATTRIBUTE_COUNT(label_url_names),
            .tooltip_names = label_tooltip_names,
            .tooltip_names_size = ATTRIBUTE_COUNT(label_tooltip_names),
            .tooltip_fallback_names = edge_tooltip_fallback_names,
            .tooltip_fallback_names_size =
                ATTRIBUTE_COUNT(edge_tooltip_fallback_names),
            .target_names = label_target_names,
            .target_names_size = ATTRIBUTE_COUNT(label_target_names),
            .label_gate_names = label_gate_names,
            .label_gate_names_size = ATTRIBUTE_COUNT(label_gate_names),
            .url_uses_label_gate = true,
            .target_anchor_names = label_target_anchor_names,
            .target_anchor_names_size =
                ATTRIBUTE_COUNT(label_target_anchor_names),
            .opposite_layer = HYPERLINK_LAYER_LABEL,
        },
    [HYPERLINK_LAYER_HEAD] =
        {
            .name = "head",
            .url_names = head_url_names,
            .url_names_size = ATTRIBUTE_COUNT(head_url_names),
            .label_url_names = head_label_url_names,
            .label_url_names_size = ATTRIBUTE_COUNT(head_label_url_names),
            .tooltip_names = head_tooltip_names,
            .tooltip_names_size = ATTRIBUTE_COUNT(head_tooltip_names),
            .tooltip_fallback_names = head_tooltip_fallback_names,
            .tooltip_fallback_names_size =
                ATTRIBUTE_COUNT(head_tooltip_fallback_names),
            .target_names = head_target_names,
            .target_names_size = ATTRIBUTE_COUNT(head_target_names),
            .label_gate_names = head_label_gate_names,
            .label_gate_names_size = ATTRIBUTE_COUNT(head_label_gate_names),
            .target_anchor_names = head_target_anchor_names,
            .target_anchor_names_size =
                ATTRIBUTE_COUNT(head_target_anchor_names),
            .opposite_layer = HYPERLINK_LAYER_TAIL,
        },
    [HYPERLINK_LAYER_TAIL] =
        {
            .name = "tail",
            .url_names = tail_url_names,
            .url_names_size = ATTRIBUTE_COUNT(tail_url_names),
            .label_url_names = tail_label_url_names,
            .label_url_names_size = ATTRIBUTE_COUNT(tail_label_url_names),
            .tooltip_names = tail_tooltip_names,
            .tooltip_names_size = ATTRIBUTE_COUNT(tail_tooltip_names),
            .tooltip_fallback_names = tail_tooltip_fallback_names,
            .tooltip_fallback_names_size =
                ATTRIBUTE_COUNT(tail_tooltip_fallback_names),
            .target_names = tail_target_names,
            .target_names_size = ATTRIBUTE_COUNT(tail_target_names),
            .label_gate_names = tail_label_gate_names,
            .label_gate_names_size = ATTRIBUTE_COUNT(tail_label_gate_names),
            .target_anchor_names = tail_target_anchor_names,
            .target_anchor_names_size =
                ATTRIBUTE_COUNT(tail_target_anchor_names),
            .opposite_layer = HYPERLINK_LAYER_HEAD,
        },
};

static bool hyperlink_layer_gate_is_open(Agraph_t *root_graph, Agedge_t *edge,
                                         const hyperlink_layer_t *layer) {
  return layer->label_gate_names_size == 0 ||
         edge_has_any_of_attributes(root_graph, edge, layer->label_gate_names,
                                    layer->label_gate_names_size);
}

static void append_hyperlink_slots(agxbuf *signature, Agraph_t *root_graph,
                                   Agedge_t *edge, bool reverse_orientation) {
  for (size_t layer_index = 0; layer_index < HYPERLINK_LAYER_COUNT;
       layer_index++) {
    const hyperlink_layer_t *const canonical_layer =
        &hyperlink_layers[layer_index];
    const hyperlink_layer_t *const source_layer =
        reverse_orientation ? &hyperlink_layers[canonical_layer->opposite_layer]
                            : canonical_layer;

    for (hyperlink_value_kind_t kind = HYPERLINK_VALUE_URL;
         kind < HYPERLINK_VALUE_COUNT; kind++) {
      const char *const *names = NULL;
      size_t names_size = 0;
      const char *kind_name = NULL;
      bool rendered = false;
      switch (kind) {
      case HYPERLINK_VALUE_URL: {
        names = source_layer->url_names;
        names_size = source_layer->url_names_size;
        kind_name = "URL";
        const comparable_attribute_value_t url =
            named_first_nonempty_attribute_value(root_graph, edge, names,
                                                 names_size);
        rendered =
            (!source_layer->url_uses_label_gate ||
             hyperlink_layer_gate_is_open(root_graph, edge, source_layer)) &&
            (url.text[0] != '\0' ||
             edge_has_any_of_attributes(root_graph, edge,
                                        source_layer->tooltip_names,
                                        source_layer->tooltip_names_size));
        break;
      }
      case HYPERLINK_VALUE_TOOLTIP:
        names = source_layer->tooltip_names;
        names_size = source_layer->tooltip_names_size;
        kind_name = "tooltip";
        rendered = hyperlink_layer_gate_is_open(root_graph, edge, source_layer);
        break;
      case HYPERLINK_VALUE_TARGET:
        names = source_layer->target_names;
        names_size = source_layer->target_names_size;
        kind_name = "target";
        rendered =
            hyperlink_layer_gate_is_open(root_graph, edge, source_layer) &&
            edge_has_any_of_attributes(root_graph, edge,
                                       source_layer->target_anchor_names,
                                       source_layer->target_anchor_names_size);
        break;
      case HYPERLINK_VALUE_COUNT:
        break;
      }
      if (!rendered) {
        continue;
      }

      comparable_attribute_value_t value = named_first_nonempty_attribute_value(
          root_graph, edge, names, names_size);
      bool tooltip_uses_fallback = false;
      if (kind == HYPERLINK_VALUE_TOOLTIP && value.text[0] == '\0') {
        const comparable_attribute_value_t url =
            named_first_nonempty_attribute_value(root_graph, edge,
                                                 source_layer->url_names,
                                                 source_layer->url_names_size);
        if (url.text[0] != '\0') {
          value = named_first_nonempty_attribute_value(
              root_graph, edge, source_layer->tooltip_fallback_names,
              source_layer->tooltip_fallback_names_size);
          tooltip_uses_fallback = true;
        }
      }

      agxbuf slot_name = {0};
      agxbprint(&slot_name, "hyperlink:%s:%s", canonical_layer->name,
                kind_name);
      if (kind == HYPERLINK_VALUE_TOOLTIP && !tooltip_uses_fallback) {
        char *const preprocessed = preprocessTooltip((char *)value.text, edge);
        char *const substituted = strdup_and_subst_obj(preprocessed, edge);
        append_plain_signature_slot(signature, agxbuse(&slot_name),
                                    substituted);
        free(substituted);
        free(preprocessed);
      } else if (value.is_html) {
        append_signature_slot(signature, agxbuse(&slot_name), value);
      } else {
        char *const substituted =
            strdup_and_subst_obj((char *)value.text, edge);
        append_plain_signature_slot(signature, agxbuse(&slot_name),
                                    substituted);
        free(substituted);
      }
      agxbfree(&slot_name);
    }

    if (source_layer->label_url_names_size != 0 &&
        hyperlink_layer_gate_is_open(root_graph, edge, source_layer)) {
      comparable_attribute_value_t value = named_first_nonempty_attribute_value(
          root_graph, edge, source_layer->label_url_names,
          source_layer->label_url_names_size);
      agxbuf slot_name = {0};
      agxbprint(&slot_name, "hyperlink:%s:label-URL", canonical_layer->name);
      if (value.is_html) {
        append_signature_slot(signature, agxbuse(&slot_name), value);
      } else {
        char *const substituted =
            strdup_and_subst_obj((char *)value.text, edge);
        append_plain_signature_slot(signature, agxbuse(&slot_name),
                                    substituted);
        free(substituted);
      }
      agxbfree(&slot_name);
    }
  }
}

static rendered_edge_identity_t
project_rendered_edge_identity(Agedge_t *edge, bool reverse_orientation) {
  Agraph_t *const root_graph = agroot(agraphof(edge));
  agxbuf signature = {0};

  append_ordinary_attribute_slots(&signature, root_graph, edge);
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
