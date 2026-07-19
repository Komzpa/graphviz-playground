/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

/*
 * Concentration may replace several input edges with one rendered spline, so
 * this file compares rendered edge identity rather than Cgraph object identity.
 * The comparison uses effective values after graph defaults, documented
 * aliases, color normalization, and string substitutions such as \H and \T have
 * been applied.
 *
 * Attribute symbols and their default refstrings are owned by the root graph;
 * the helpers below read them in root-registry order and never retain pointers
 * beyond the active graph. Same-direction edges compare grammar endpoints
 * directly. Opposite-direction edges compare physical endpoints, so head-owned
 * attributes on one edge pair with tail-owned attributes on the other.
 *
 * The classification and hyperlink tables are intentionally explicit. A shorter
 * collection of strcmp() predicates is tempting, but it hides which attributes
 * are ignored after layout, which are aliases, and which endpoint attributes
 * must be swapped to avoid reintroducing concentrate's historical semantic
 * merges.
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

typedef struct {
  const char *text;
  bool is_html;
} comparable_attribute_value_t;

#define ATTRIBUTE_COUNT(attributes)                                            \
  (sizeof(attributes) / sizeof((attributes)[0]))

typedef unsigned int edge_attribute_flags_t;

enum {
  EDGE_ATTRIBUTE_STROKE_OR_FILL_COLOR = 1u << 0,
  EDGE_ATTRIBUTE_LABEL_COLOR = 1u << 1,
  EDGE_ATTRIBUTE_COLOR = 1u << 2,
  EDGE_ATTRIBUTE_LABEL_ONLY = 1u << 3,
  EDGE_ATTRIBUTE_CLIPPING = 1u << 4,
  EDGE_ATTRIBUTE_LAYOUT_ONLY = 1u << 5,
  EDGE_ATTRIBUTE_ARROW = 1u << 6,
  EDGE_ATTRIBUTE_URL_ALIAS = 1u << 7,
  EDGE_ATTRIBUTE_TOOLTIP_ALIAS = 1u << 8,
  EDGE_ATTRIBUTE_TARGET_ALIAS = 1u << 9,
  EDGE_ATTRIBUTE_ENDPOINT_LABEL = 1u << 10,
  EDGE_ATTRIBUTE_SUBSTITUTED = 1u << 11,
  EDGE_ATTRIBUTE_FONTNAME = 1u << 12,
  EDGE_ATTRIBUTE_PORT = 1u << 13,
  EDGE_ATTRIBUTE_ENDPOINT = 1u << 14,
  EDGE_ATTRIBUTE_NUMERIC_DEFAULT_ONE = 1u << 15,
  EDGE_ATTRIBUTE_NUMERIC_DEFAULT_FONT_SIZE = 1u << 16,
  EDGE_ATTRIBUTE_NUMERIC_DEFAULT_LABEL_FONT_SIZE = 1u << 17,
  EDGE_ATTRIBUTE_NUMERIC_DEFAULT_LABEL_ANGLE = 1u << 18,
  EDGE_ATTRIBUTE_BOOL_DEFAULT_FALSE = 1u << 19,
  EDGE_ATTRIBUTE_STYLE = 1u << 20,
  EDGE_ATTRIBUTE_DIRECTION = 1u << 21,
  EDGE_ATTRIBUTE_FILL_COLOR = 1u << 22,
  EDGE_ATTRIBUTE_ARROW_SIZE = 1u << 23,
  EDGE_ATTRIBUTE_FONT_COLOR = 1u << 24,
  EDGE_ATTRIBUTE_LABEL_FONT_COLOR = 1u << 25,
  EDGE_ATTRIBUTE_LABEL_FONTNAME = 1u << 26,
};

typedef struct {
  const char *name;
  edge_attribute_flags_t flags;
} edge_attribute_classification_t;

#define EDGE_COLOR_FLAGS                                                       \
  (EDGE_ATTRIBUTE_COLOR | EDGE_ATTRIBUTE_STROKE_OR_FILL_COLOR)
#define EDGE_LABEL_COLOR_FLAGS                                                 \
  (EDGE_ATTRIBUTE_COLOR | EDGE_ATTRIBUTE_LABEL_COLOR |                         \
   EDGE_ATTRIBUTE_LABEL_ONLY)

/*
 * This table is the local contract for concentration-specific edge attributes:
 * each row states how a declared attribute participates in rendered identity.
 * Attributes absent from the table are ordinary rendered attributes and compare
 * by their effective text/HTML value.
 */
static const edge_attribute_classification_t edge_attribute_classifications[] =
    {
        {"URL", EDGE_ATTRIBUTE_URL_ALIAS},
        {"arrowhead", EDGE_ATTRIBUTE_ARROW},
        {"arrowsize",
         EDGE_ATTRIBUTE_NUMERIC_DEFAULT_ONE | EDGE_ATTRIBUTE_ARROW_SIZE},
        {"arrowtail", EDGE_ATTRIBUTE_ARROW},
        {"color", EDGE_COLOR_FLAGS},
        {"constraint", EDGE_ATTRIBUTE_LAYOUT_ONLY},
        {"decorate",
         EDGE_ATTRIBUTE_LABEL_ONLY | EDGE_ATTRIBUTE_BOOL_DEFAULT_FALSE},
        {"dir", EDGE_ATTRIBUTE_ARROW | EDGE_ATTRIBUTE_DIRECTION},
        {"edgeURL", EDGE_ATTRIBUTE_URL_ALIAS},
        {"edgehref", EDGE_ATTRIBUTE_URL_ALIAS},
        {"edgetarget", EDGE_ATTRIBUTE_TARGET_ALIAS},
        {"edgetooltip", EDGE_ATTRIBUTE_TOOLTIP_ALIAS},
        {"fillcolor", EDGE_COLOR_FLAGS | EDGE_ATTRIBUTE_FILL_COLOR},
        {"fontcolor", EDGE_LABEL_COLOR_FLAGS | EDGE_ATTRIBUTE_FONT_COLOR},
        {"fontname", EDGE_ATTRIBUTE_LABEL_ONLY | EDGE_ATTRIBUTE_FONTNAME},
        {"fontsize",
         EDGE_ATTRIBUTE_LABEL_ONLY | EDGE_ATTRIBUTE_NUMERIC_DEFAULT_FONT_SIZE},
        {"headURL", EDGE_ATTRIBUTE_URL_ALIAS | EDGE_ATTRIBUTE_ENDPOINT},
        {"headclip", EDGE_ATTRIBUTE_CLIPPING | EDGE_ATTRIBUTE_ENDPOINT},
        {"headhref", EDGE_ATTRIBUTE_URL_ALIAS | EDGE_ATTRIBUTE_ENDPOINT},
        {"headlabel", EDGE_ATTRIBUTE_ENDPOINT_LABEL | EDGE_ATTRIBUTE_ENDPOINT},
        {"headport", EDGE_ATTRIBUTE_PORT | EDGE_ATTRIBUTE_ENDPOINT},
        {"headtarget", EDGE_ATTRIBUTE_TARGET_ALIAS | EDGE_ATTRIBUTE_ENDPOINT},
        {"headtooltip", EDGE_ATTRIBUTE_TOOLTIP_ALIAS |
                            EDGE_ATTRIBUTE_SUBSTITUTED |
                            EDGE_ATTRIBUTE_ENDPOINT},
        {"href", EDGE_ATTRIBUTE_URL_ALIAS},
        {"id", EDGE_ATTRIBUTE_SUBSTITUTED},
        {"labelURL", EDGE_ATTRIBUTE_URL_ALIAS},
        {"labelangle", EDGE_ATTRIBUTE_LABEL_ONLY |
                           EDGE_ATTRIBUTE_NUMERIC_DEFAULT_LABEL_ANGLE},
        {"labeldistance",
         EDGE_ATTRIBUTE_LABEL_ONLY | EDGE_ATTRIBUTE_NUMERIC_DEFAULT_ONE},
        {"labelfloat",
         EDGE_ATTRIBUTE_LABEL_ONLY | EDGE_ATTRIBUTE_BOOL_DEFAULT_FALSE},
        {"labelfontcolor",
         EDGE_LABEL_COLOR_FLAGS | EDGE_ATTRIBUTE_LABEL_FONT_COLOR},
        {"labelfontname", EDGE_ATTRIBUTE_LABEL_ONLY | EDGE_ATTRIBUTE_FONTNAME |
                              EDGE_ATTRIBUTE_LABEL_FONTNAME},
        {"labelfontsize", EDGE_ATTRIBUTE_LABEL_ONLY |
                              EDGE_ATTRIBUTE_NUMERIC_DEFAULT_LABEL_FONT_SIZE},
        {"labelhref", EDGE_ATTRIBUTE_URL_ALIAS},
        {"labeltarget", EDGE_ATTRIBUTE_TARGET_ALIAS},
        {"labeltooltip",
         EDGE_ATTRIBUTE_TOOLTIP_ALIAS | EDGE_ATTRIBUTE_SUBSTITUTED},
        {"lhead", EDGE_ATTRIBUTE_ENDPOINT},
        {"ltail", EDGE_ATTRIBUTE_ENDPOINT},
        {"minlen", EDGE_ATTRIBUTE_LAYOUT_ONLY},
        {"penwidth", EDGE_ATTRIBUTE_NUMERIC_DEFAULT_ONE},
        {"samehead", EDGE_ATTRIBUTE_ENDPOINT},
        {"sametail", EDGE_ATTRIBUTE_ENDPOINT},
        {"style", EDGE_ATTRIBUTE_STYLE},
        {"tailURL", EDGE_ATTRIBUTE_URL_ALIAS | EDGE_ATTRIBUTE_ENDPOINT},
        {"tailclip", EDGE_ATTRIBUTE_CLIPPING | EDGE_ATTRIBUTE_ENDPOINT},
        {"tailhref", EDGE_ATTRIBUTE_URL_ALIAS | EDGE_ATTRIBUTE_ENDPOINT},
        {"taillabel", EDGE_ATTRIBUTE_ENDPOINT_LABEL | EDGE_ATTRIBUTE_ENDPOINT},
        {"tailport", EDGE_ATTRIBUTE_PORT | EDGE_ATTRIBUTE_ENDPOINT},
        {"tailtarget", EDGE_ATTRIBUTE_TARGET_ALIAS | EDGE_ATTRIBUTE_ENDPOINT},
        {"tailtooltip", EDGE_ATTRIBUTE_TOOLTIP_ALIAS |
                            EDGE_ATTRIBUTE_SUBSTITUTED |
                            EDGE_ATTRIBUTE_ENDPOINT},
        {"target", EDGE_ATTRIBUTE_TARGET_ALIAS},
        {"tooltip", EDGE_ATTRIBUTE_TOOLTIP_ALIAS},
        {"weight", EDGE_ATTRIBUTE_LAYOUT_ONLY},
};

#undef EDGE_COLOR_FLAGS
#undef EDGE_LABEL_COLOR_FLAGS

typedef struct {
  const char *names[2];
} endpoint_attribute_names_t;

typedef enum {
  EDGE_TAIL_ENDPOINT,
  EDGE_HEAD_ENDPOINT,
  EDGE_ENDPOINT_COUNT,
} edge_endpoint_t;

static edge_attribute_flags_t edge_attribute_flags(const char *name) {
  for (size_t attribute_index = 0;
       attribute_index < ATTRIBUTE_COUNT(edge_attribute_classifications);
       attribute_index++) {
    const edge_attribute_classification_t *const classification =
        &edge_attribute_classifications[attribute_index];
    if (strcmp(name, classification->name) == 0) {
      return classification->flags;
    }
  }
  return 0;
}

static bool edge_attribute_has_flag(const char *name,
                                    edge_attribute_flags_t flag) {
  return (edge_attribute_flags(name) & flag) != 0;
}

static bool edge_attribute_has_any_alias_flag(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_URL_ALIAS |
                                           EDGE_ATTRIBUTE_TOOLTIP_ALIAS |
                                           EDGE_ATTRIBUTE_TARGET_ALIAS);
}

static comparable_attribute_value_t plain_attribute_value(const char *text) {
  return (comparable_attribute_value_t){.text = text, .is_html = false};
}

static comparable_attribute_value_t
declared_attribute_value(Agedge_t *edge, Agsym_t *attribute) {
  const char *const text = agxget(edge, attribute);
  return (comparable_attribute_value_t){
      .text = text,
      .is_html = aghtmlstr(text),
  };
}

static comparable_attribute_value_t
named_attribute_value(Agraph_t *root_graph, Agedge_t *edge,
                      const char *attribute_name) {
  Agsym_t *const attribute = agfindedgeattr(root_graph, (char *)attribute_name);
  if (attribute == NULL) {
    /*
     * An undeclared attribute has the plain, empty effective value. Keep its
     * representation explicit: aghtmlstr() accepts only Cgraph refstrings, so
     * passing this string literal to it would be undefined.
     */
    return plain_attribute_value("");
  }
  return declared_attribute_value(edge, attribute);
}

static comparable_attribute_value_t
named_first_nonempty_attribute_value(Agraph_t *root_graph, Agedge_t *edge,
                                     const char *const *attribute_names,
                                     size_t attribute_names_size) {
  comparable_attribute_value_t empty_value = {.text = "", .is_html = false};

  for (size_t i = 0; i < attribute_names_size; i++) {
    const comparable_attribute_value_t value =
        named_attribute_value(root_graph, edge, attribute_names[i]);
    if (value.text[0] != '\0') {
      return value;
    }
    empty_value = value;
  }
  return empty_value;
}

static bool comparable_attribute_values_are_equal(
    comparable_attribute_value_t first_value,
    comparable_attribute_value_t second_value) {
  return first_value.is_html == second_value.is_html &&
         strcmp(first_value.text, second_value.text) == 0;
}

static bool is_label_color_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_LABEL_COLOR);
}

static bool is_color_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_COLOR);
}

static bool is_label_only_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_LABEL_ONLY);
}

static bool edge_has_any_of_attributes(Agraph_t *root_graph, Agedge_t *edge,
                                       const char *const *attribute_names,
                                       size_t attribute_names_size) {
  for (size_t i = 0; i < attribute_names_size; i++) {
    if (named_attribute_value(root_graph, edge, attribute_names[i]).text[0] !=
        '\0') {
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

static bool edge_has_any_label(Agraph_t *root_graph, Agedge_t *edge) {
  return edge_has_main_label(root_graph, edge) ||
         edge_has_endpoint_label(root_graph, edge);
}

static bool edge_uses_label_color(Agraph_t *root_graph, Agedge_t *edge,
                                  const char *attribute_name) {
  if (edge_attribute_has_flag(attribute_name, EDGE_ATTRIBUTE_FONT_COLOR)) {
    return edge_has_main_label(root_graph, edge) ||
           (edge_has_endpoint_label(root_graph, edge) &&
            named_attribute_value(root_graph, edge, "labelfontcolor").text[0] ==
                '\0');
  }
  if (edge_attribute_has_flag(attribute_name,
                              EDGE_ATTRIBUTE_LABEL_FONT_COLOR)) {
    return edge_has_endpoint_label(root_graph, edge);
  }
  return true;
}

static bool label_color_attribute_is_rendered(Agraph_t *root_graph,
                                              Agedge_t *first_edge,
                                              Agedge_t *second_edge,
                                              const char *attribute_name) {
  return !is_label_color_attribute(attribute_name) ||
         edge_uses_label_color(root_graph, first_edge, attribute_name) ||
         edge_uses_label_color(root_graph, second_edge, attribute_name);
}

static comparable_attribute_value_t
edge_color_default_value(Agraph_t *root_graph, Agedge_t *edge,
                         const char *attribute_name) {
  if (edge_attribute_has_flag(attribute_name, EDGE_ATTRIBUTE_FILL_COLOR)) {
    comparable_attribute_value_t color =
        named_attribute_value(root_graph, edge, "color");
    return color.text[0] == '\0' ? plain_attribute_value(DEFAULT_COLOR) : color;
  }
  if (edge_attribute_has_flag(attribute_name,
                              EDGE_ATTRIBUTE_LABEL_FONT_COLOR)) {
    comparable_attribute_value_t fontcolor =
        named_attribute_value(root_graph, edge, "fontcolor");
    return fontcolor.text[0] == '\0' ? plain_attribute_value(DEFAULT_COLOR)
                                     : fontcolor;
  }
  return plain_attribute_value(DEFAULT_COLOR);
}

static comparable_attribute_value_t
edge_effective_color_value(Agraph_t *root_graph, Agedge_t *edge,
                           const char *attribute_name,
                           comparable_attribute_value_t value) {
  if (is_color_attribute(attribute_name) && value.text[0] == '\0') {
    return edge_color_default_value(root_graph, edge, attribute_name);
  }
  return value;
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

static bool edge_color_values_are_equal(
    Agedge_t *first_edge, comparable_attribute_value_t first_value,
    Agedge_t *second_edge, comparable_attribute_value_t second_value) {
  gvcolor_t first_color;
  gvcolor_t second_color;

  if (!edge_color_value(first_edge, first_value, &first_color) ||
      !edge_color_value(second_edge, second_value, &second_color)) {
    return false;
  }
  return memcmp(first_color.u.rgba, second_color.u.rgba,
                sizeof(first_color.u.rgba)) == 0;
}

static bool is_clipping_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_CLIPPING);
}

static bool is_layout_only_edge_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_LAYOUT_ONLY);
}

static bool is_arrow_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_ARROW);
}

static bool is_alias_attribute(const char *name) {
  return edge_attribute_has_any_alias_flag(name);
}

static bool edge_clip_value(comparable_attribute_value_t value) {
  return value.text[0] == '\0' || mapbool(value.text);
}

static const char *edge_direction_value(Agedge_t *edge,
                                        comparable_attribute_value_t value) {
  if (value.text[0] == '\0') {
    return agisdirected(agraphof(edge)) ? "forward" : "none";
  }
  return value.text;
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

static bool edge_numeric_default_values_are_equal(
    const char *attribute_name, comparable_attribute_value_t first_value,
    comparable_attribute_value_t second_value) {
  const edge_attribute_flags_t flags = edge_attribute_flags(attribute_name);
  double default_value;
  double minimum = 0.0;

  if ((flags & EDGE_ATTRIBUTE_NUMERIC_DEFAULT_ONE) != 0) {
    default_value = 1.0;
  } else if ((flags & EDGE_ATTRIBUTE_NUMERIC_DEFAULT_FONT_SIZE) != 0) {
    default_value = DEFAULT_FONTSIZE;
    minimum = MIN_FONTSIZE;
  } else if ((flags & EDGE_ATTRIBUTE_NUMERIC_DEFAULT_LABEL_FONT_SIZE) != 0) {
    default_value = DEFAULT_LABEL_FONTSIZE;
    minimum = MIN_FONTSIZE;
  } else if ((flags & EDGE_ATTRIBUTE_NUMERIC_DEFAULT_LABEL_ANGLE) != 0) {
    default_value = PORT_LABEL_ANGLE;
    minimum = -180.0;
  } else {
    return false;
  }

  double first_number;
  double second_number;
  return edge_numeric_attribute_value(first_value, default_value, minimum,
                                      &first_number) &&
         edge_numeric_attribute_value(second_value, default_value, minimum,
                                      &second_number) &&
         first_number == second_number;
}

static bool edge_fontsize_value(Agraph_t *root_graph, Agedge_t *edge,
                                double *number) {
  return edge_numeric_attribute_value(
      named_attribute_value(root_graph, edge, "fontsize"), DEFAULT_FONTSIZE,
      MIN_FONTSIZE, number);
}

static bool edge_labelfontsize_values_are_equal(
    Agedge_t *first_edge, comparable_attribute_value_t first_value,
    Agedge_t *second_edge, comparable_attribute_value_t second_value) {
  Agraph_t *const root_graph = agroot(agraphof(first_edge));
  double first_default;
  double second_default;
  double first_number;
  double second_number;

  return edge_fontsize_value(root_graph, first_edge, &first_default) &&
         edge_fontsize_value(root_graph, second_edge, &second_default) &&
         edge_numeric_attribute_value(first_value, first_default, MIN_FONTSIZE,
                                      &first_number) &&
         edge_numeric_attribute_value(second_value, second_default,
                                      MIN_FONTSIZE, &second_number) &&
         first_number == second_number;
}

static comparable_attribute_value_t
edge_fontname_default_value(Agraph_t *root_graph, Agedge_t *edge,
                            const char *attribute_name) {
  if (edge_attribute_has_flag(attribute_name, EDGE_ATTRIBUTE_LABEL_FONTNAME)) {
    const comparable_attribute_value_t fontname =
        named_attribute_value(root_graph, edge, "fontname");
    return fontname.text[0] == '\0' ? plain_attribute_value(DEFAULT_FONTNAME)
                                    : fontname;
  }
  return plain_attribute_value(DEFAULT_FONTNAME);
}

static bool edge_fontname_values_are_equal(
    Agedge_t *first_edge, const char *first_attribute_name,
    comparable_attribute_value_t first_value, Agedge_t *second_edge,
    const char *second_attribute_name,
    comparable_attribute_value_t second_value) {
  Agraph_t *const root_graph = agroot(agraphof(first_edge));

  if (first_value.text[0] == '\0') {
    first_value = edge_fontname_default_value(root_graph, first_edge,
                                              first_attribute_name);
  }
  if (second_value.text[0] == '\0') {
    second_value = edge_fontname_default_value(root_graph, second_edge,
                                               second_attribute_name);
  }
  return comparable_attribute_values_are_equal(first_value, second_value);
}

static bool
edge_style_values_are_equal(comparable_attribute_value_t first_value,
                            comparable_attribute_value_t second_value) {
  if (first_value.is_html || second_value.is_html) {
    return false;
  }
  const char *const first_style =
      first_value.text[0] == '\0' ? "solid" : first_value.text;
  const char *const second_style =
      second_value.text[0] == '\0' ? "solid" : second_value.text;
  return strcmp(first_style, second_style) == 0;
}

static bool
edge_bool_default_values_are_equal(const char *attribute_name,
                                   comparable_attribute_value_t first_value,
                                   comparable_attribute_value_t second_value) {
  if (!edge_attribute_has_flag(attribute_name,
                               EDGE_ATTRIBUTE_BOOL_DEFAULT_FALSE)) {
    return false;
  }

  const bool first_bool =
      first_value.text[0] == '\0' ? false : mapbool(first_value.text);
  const bool second_bool =
      second_value.text[0] == '\0' ? false : mapbool(second_value.text);
  return first_bool == second_bool;
}

static bool is_endpoint_label_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_ENDPOINT_LABEL);
}

static bool is_substituted_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_SUBSTITUTED);
}

static bool is_fontname_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_FONTNAME);
}

static bool edge_substituted_values_are_equal(
    Agedge_t *first_edge, comparable_attribute_value_t first_value,
    Agedge_t *second_edge, comparable_attribute_value_t second_value) {
  if (first_value.is_html || second_value.is_html) {
    return comparable_attribute_values_are_equal(first_value, second_value);
  }

  char *const first_text =
      strdup_and_subst_obj((char *)first_value.text, first_edge);
  char *const second_text =
      strdup_and_subst_obj((char *)second_value.text, second_edge);
  const bool equal = strcmp(first_text, second_text) == 0;
  free(first_text);
  free(second_text);
  return equal;
}

static bool edge_label_values_are_equal(
    Agedge_t *first_edge, comparable_attribute_value_t first_value,
    Agedge_t *second_edge, comparable_attribute_value_t second_value) {
  return edge_substituted_values_are_equal(first_edge, first_value, second_edge,
                                           second_value);
}

static bool edge_attribute_values_are_equal(
    Agedge_t *first_edge, const char *first_attribute_name,
    comparable_attribute_value_t first_value, Agedge_t *second_edge,
    const char *second_attribute_name,
    comparable_attribute_value_t second_value) {
  if (is_endpoint_label_attribute(first_attribute_name) &&
      is_endpoint_label_attribute(second_attribute_name)) {
    return edge_label_values_are_equal(first_edge, first_value, second_edge,
                                       second_value);
  }

  if (is_substituted_attribute(first_attribute_name) &&
      is_substituted_attribute(second_attribute_name)) {
    return edge_substituted_values_are_equal(first_edge, first_value,
                                             second_edge, second_value);
  }

  if (is_clipping_attribute(first_attribute_name) &&
      is_clipping_attribute(second_attribute_name)) {
    return edge_clip_value(first_value) == edge_clip_value(second_value);
  }

  if (edge_attribute_has_flag(first_attribute_name, EDGE_ATTRIBUTE_DIRECTION) &&
      edge_attribute_has_flag(second_attribute_name,
                              EDGE_ATTRIBUTE_DIRECTION)) {
    return strcmp(edge_direction_value(first_edge, first_value),
                  edge_direction_value(second_edge, second_value)) == 0;
  }

  if (is_color_attribute(first_attribute_name) &&
      is_color_attribute(second_attribute_name)) {
    Agraph_t *const root_graph = agroot(agraphof(first_edge));
    first_value = edge_effective_color_value(root_graph, first_edge,
                                             first_attribute_name, first_value);
    second_value = edge_effective_color_value(
        root_graph, second_edge, second_attribute_name, second_value);
    if (edge_color_values_are_equal(first_edge, first_value, second_edge,
                                    second_value)) {
      return true;
    }
  }

  if (is_color_attribute(first_attribute_name) &&
      is_color_attribute(second_attribute_name) &&
      comparable_attribute_values_are_equal(first_value, second_value)) {
    return true;
  }

  if (strcmp(first_attribute_name, second_attribute_name) == 0 &&
      ((edge_attribute_has_flag(
            first_attribute_name,
            EDGE_ATTRIBUTE_NUMERIC_DEFAULT_LABEL_FONT_SIZE) &&
        edge_labelfontsize_values_are_equal(first_edge, first_value,
                                            second_edge, second_value)) ||
       edge_numeric_default_values_are_equal(first_attribute_name, first_value,
                                             second_value) ||
       (is_fontname_attribute(first_attribute_name) &&
        edge_fontname_values_are_equal(first_edge, first_attribute_name,
                                       first_value, second_edge,
                                       second_attribute_name, second_value)) ||
       (edge_attribute_has_flag(first_attribute_name, EDGE_ATTRIBUTE_STYLE) &&
        edge_style_values_are_equal(first_value, second_value)) ||
       edge_bool_default_values_are_equal(first_attribute_name, first_value,
                                          second_value))) {
    return true;
  }

  return comparable_attribute_values_are_equal(first_value, second_value);
}

static bool arrow_flags_can_merge_at_endpoint(uint32_t first_flags,
                                              uint32_t second_flags) {
  return first_flags == 0 || second_flags == 0 || first_flags == second_flags;
}

static bool opposite_edge_arrows_are_compatible(Agedge_t *first_edge,
                                                Agedge_t *second_edge) {
  uint32_t first_start_flags;
  uint32_t first_end_flags;
  uint32_t second_start_flags;
  uint32_t second_end_flags;

  arrow_flags(first_edge, &first_start_flags, &first_end_flags);
  arrow_flags(second_edge, &second_start_flags, &second_end_flags);

  /*
   * Opposite edges swap physical endpoints. The second edge's start is at the
   * first edge's end, and the second edge's end is at the first edge's start.
   * A shared spline can represent one arrow flag set per physical endpoint.
   */
  return arrow_flags_can_merge_at_endpoint(first_start_flags,
                                           second_end_flags) &&
         arrow_flags_can_merge_at_endpoint(first_end_flags, second_start_flags);
}

static bool named_substituted_attribute_values_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge, const char *const *first_names,
    size_t first_names_size, Agedge_t *second_edge,
    const char *const *second_names, size_t second_names_size) {
  const comparable_attribute_value_t first_value =
      named_first_nonempty_attribute_value(root_graph, first_edge, first_names,
                                           first_names_size);
  const comparable_attribute_value_t second_value =
      named_first_nonempty_attribute_value(root_graph, second_edge,
                                           second_names, second_names_size);

  return edge_substituted_values_are_equal(first_edge, first_value, second_edge,
                                           second_value);
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
  const char *const *tooltip_names;
  size_t tooltip_names_size;
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
static const char *const head_url_names[] = {"headhref", "headURL", "edgehref",
                                             "edgeURL",  "href",    "URL"};
static const char *const tail_url_names[] = {"tailhref", "tailURL", "edgehref",
                                             "edgeURL",  "href",    "URL"};

static const char *const edge_tooltip_names[] = {"tooltip", "edgetooltip"};
static const char *const label_tooltip_names[] = {"labeltooltip"};
static const char *const head_tooltip_names[] = {"headtooltip"};
static const char *const tail_tooltip_names[] = {"tailtooltip"};

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
 * Hyperlink attributes form four rendered layers. Each layer resolves aliases
 * in first-nonempty order, and reverse concentration compares the second edge's
 * mate layer for endpoint-owned values. Target anchors intentionally stay
 * separate from URL chains because endpoint targets inherit href/URL but not
 * edgeURL/edgehref; merging the lists would make headtarget/tailtarget visible
 * in cases Graphviz currently ignores.
 */
static const hyperlink_layer_t hyperlink_layers[] = {
    [HYPERLINK_LAYER_EDGE] =
        {
            .name = "edge",
            .url_names = edge_url_names,
            .url_names_size = ATTRIBUTE_COUNT(edge_url_names),
            .tooltip_names = edge_tooltip_names,
            .tooltip_names_size = ATTRIBUTE_COUNT(edge_tooltip_names),
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
            .tooltip_names = head_tooltip_names,
            .tooltip_names_size = ATTRIBUTE_COUNT(head_tooltip_names),
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
            .tooltip_names = tail_tooltip_names,
            .tooltip_names_size = ATTRIBUTE_COUNT(tail_tooltip_names),
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

static bool hyperlink_layer_url_is_rendered(Agraph_t *root_graph,
                                            Agedge_t *edge,
                                            const hyperlink_layer_t *layer) {
  if (layer->url_uses_label_gate &&
      !hyperlink_layer_gate_is_open(root_graph, edge, layer)) {
    return false;
  }

  const comparable_attribute_value_t url_value =
      named_first_nonempty_attribute_value(root_graph, edge, layer->url_names,
                                           layer->url_names_size);
  const bool has_explicit_tooltip = edge_has_any_of_attributes(
      root_graph, edge, layer->tooltip_names, layer->tooltip_names_size);
  return url_value.text[0] != '\0' || has_explicit_tooltip;
}

static bool hyperlink_layer_target_is_rendered(Agraph_t *root_graph,
                                               Agedge_t *edge,
                                               const hyperlink_layer_t *layer) {
  return hyperlink_layer_gate_is_open(root_graph, edge, layer) &&
         edge_has_any_of_attributes(root_graph, edge,
                                    layer->target_anchor_names,
                                    layer->target_anchor_names_size);
}

static const char *const *
hyperlink_layer_names_for_kind(const hyperlink_layer_t *layer,
                               hyperlink_value_kind_t kind,
                               size_t *names_size) {
  switch (kind) {
  case HYPERLINK_VALUE_URL:
    *names_size = layer->url_names_size;
    return layer->url_names;
  case HYPERLINK_VALUE_TOOLTIP:
    *names_size = layer->tooltip_names_size;
    return layer->tooltip_names;
  case HYPERLINK_VALUE_TARGET:
    *names_size = layer->target_names_size;
    return layer->target_names;
  case HYPERLINK_VALUE_COUNT:
    break;
  }
  *names_size = 0;
  return NULL;
}

static bool hyperlink_layer_value_is_rendered(Agraph_t *root_graph,
                                              Agedge_t *edge,
                                              const hyperlink_layer_t *layer,
                                              hyperlink_value_kind_t kind) {
  switch (kind) {
  case HYPERLINK_VALUE_URL:
    return hyperlink_layer_url_is_rendered(root_graph, edge, layer);
  case HYPERLINK_VALUE_TOOLTIP:
    /*
     * tooltip/edgetooltip aliases and the label/head/tail tooltip attributes
     * were already part of the semantic comparison. Keep them ungated here so
     * the alias table owns the old behavior instead of leaking it back into the
     * root attribute walk.
     */
    return true;
  case HYPERLINK_VALUE_TARGET:
    return hyperlink_layer_target_is_rendered(root_graph, edge, layer);
  case HYPERLINK_VALUE_COUNT:
    break;
  }
  return false;
}

static bool hyperlink_layer_values_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge,
    const hyperlink_layer_t *first_layer, Agedge_t *second_edge,
    const hyperlink_layer_t *second_layer, hyperlink_value_kind_t kind) {
  const bool first_value_is_rendered = hyperlink_layer_value_is_rendered(
      root_graph, first_edge, first_layer, kind);
  const bool second_value_is_rendered = hyperlink_layer_value_is_rendered(
      root_graph, second_edge, second_layer, kind);
  if (!first_value_is_rendered && !second_value_is_rendered) {
    return true;
  }

  size_t first_names_size;
  const char *const *const first_names =
      hyperlink_layer_names_for_kind(first_layer, kind, &first_names_size);
  size_t second_names_size;
  const char *const *const second_names =
      hyperlink_layer_names_for_kind(second_layer, kind, &second_names_size);

  return named_substituted_attribute_values_are_equal(
      root_graph, first_edge, first_names, first_names_size, second_edge,
      second_names, second_names_size);
}

static bool hyperlink_layer_attribute_groups_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge, Agedge_t *second_edge,
    bool compare_opposite_endpoints) {
  for (size_t layer_index = 0; layer_index < ATTRIBUTE_COUNT(hyperlink_layers);
       layer_index++) {
    const hyperlink_layer_t *const first_layer = &hyperlink_layers[layer_index];
    const hyperlink_layer_id_t second_layer_index =
        compare_opposite_endpoints ? first_layer->opposite_layer
                                   : (hyperlink_layer_id_t)layer_index;
    const hyperlink_layer_t *const second_layer =
        &hyperlink_layers[second_layer_index];

    for (hyperlink_value_kind_t kind = 0; kind < HYPERLINK_VALUE_COUNT;
         kind++) {
      if (!hyperlink_layer_values_are_equal(root_graph, first_edge, first_layer,
                                            second_edge, second_layer, kind)) {
        return false;
      }
    }
  }
  return true;
}

static bool same_direction_edge_arrows_are_equal(Agedge_t *first_edge,
                                                 Agedge_t *second_edge) {
  uint32_t first_start_flags;
  uint32_t first_end_flags;
  uint32_t second_start_flags;
  uint32_t second_end_flags;

  edge_arrow_flags(first_edge, &first_start_flags, &first_end_flags);
  edge_arrow_flags(second_edge, &second_start_flags, &second_end_flags);

  return first_start_flags == second_start_flags &&
         first_end_flags == second_end_flags;
}

static bool edge_has_any_arrows(Agedge_t *edge) {
  uint32_t start_flags;
  uint32_t end_flags;
  edge_arrow_flags(edge, &start_flags, &end_flags);
  return start_flags != 0 || end_flags != 0;
}

static bool attribute_affects_rendered_edge(Agraph_t *root_graph,
                                            Agedge_t *first_edge,
                                            Agedge_t *second_edge,
                                            const char *attribute_name) {
  if (is_label_only_attribute(attribute_name) &&
      !edge_has_any_label(root_graph, first_edge) &&
      !edge_has_any_label(root_graph, second_edge)) {
    return false;
  }
  if (!label_color_attribute_is_rendered(root_graph, first_edge, second_edge,
                                         attribute_name)) {
    return false;
  }
  if ((edge_attribute_has_flag(attribute_name, EDGE_ATTRIBUTE_FILL_COLOR) ||
       edge_attribute_has_flag(attribute_name, EDGE_ATTRIBUTE_ARROW_SIZE)) &&
      !edge_has_any_arrows(first_edge) && !edge_has_any_arrows(second_edge)) {
    return false;
  }
  return true;
}

static bool is_port_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_PORT);
}

static const endpoint_attribute_names_t endpoint_attribute_names[] = {
    {.names = {"tailport", "headport"}},
    {.names = {"tailclip", "headclip"}},
    {.names = {"taillabel", "headlabel"}},
    {.names = {"ltail", "lhead"}},
    {.names = {"sametail", "samehead"}},
    {.names = {"tailtarget", "headtarget"}},
    {.names = {"tailtooltip", "headtooltip"}},
};

static edge_endpoint_t opposite_endpoint(edge_endpoint_t endpoint) {
  return endpoint == EDGE_HEAD_ENDPOINT ? EDGE_TAIL_ENDPOINT
                                        : EDGE_HEAD_ENDPOINT;
}

static port edge_endpoint_port(Agedge_t *edge, edge_endpoint_t endpoint) {
  return endpoint == EDGE_HEAD_ENDPOINT ? ED_head_port(edge)
                                        : ED_tail_port(edge);
}

static edge_endpoint_t endpoint_for_port_attribute(const char *attribute_name) {
  return strcmp(attribute_name, "headport") == 0 ? EDGE_HEAD_ENDPOINT
                                                 : EDGE_TAIL_ENDPOINT;
}

static port port_for_attribute(Agedge_t *edge, const char *attribute_name) {
  return edge_endpoint_port(edge, endpoint_for_port_attribute(attribute_name));
}

static bool port_values_are_equal(port first_port, port second_port) {
  return first_port.defined == second_port.defined &&
         (!first_port.defined || (first_port.p.x == second_port.p.x &&
                                  first_port.p.y == second_port.p.y));
}

static bool resolved_port_values_are_equal(port first_port, port second_port) {
  return first_port.defined && second_port.defined &&
         first_port.p.x == second_port.p.x && first_port.p.y == second_port.p.y;
}

static bool resolved_port_attributes_are_equal(
    Agedge_t *first_edge, const char *first_attribute_name,
    Agedge_t *second_edge, const char *second_attribute_name) {
  return is_port_attribute(first_attribute_name) &&
         is_port_attribute(second_attribute_name) &&
         resolved_port_values_are_equal(
             port_for_attribute(first_edge, first_attribute_name),
             port_for_attribute(second_edge, second_attribute_name));
}

bool gv_edge_ports_are_equal(Agedge_t *first_edge, Agedge_t *second_edge) {
  for (size_t endpoint_index = 0; endpoint_index < EDGE_ENDPOINT_COUNT;
       endpoint_index++) {
    const edge_endpoint_t endpoint = (edge_endpoint_t)endpoint_index;
    if (!port_values_are_equal(edge_endpoint_port(first_edge, endpoint),
                               edge_endpoint_port(second_edge, endpoint))) {
      return false;
    }
  }
  return true;
}

bool gv_opposite_edge_ports_are_equal(Agedge_t *first_edge,
                                      Agedge_t *second_edge) {
  for (size_t endpoint_index = 0; endpoint_index < EDGE_ENDPOINT_COUNT;
       endpoint_index++) {
    const edge_endpoint_t first_endpoint = (edge_endpoint_t)endpoint_index;
    const edge_endpoint_t second_endpoint = opposite_endpoint(first_endpoint);
    if (!port_values_are_equal(
            edge_endpoint_port(first_edge, first_endpoint),
            edge_endpoint_port(second_edge, second_endpoint))) {
      return false;
    }
  }
  return true;
}

static bool opposite_endpoint_attributes_are_equal(Agraph_t *root_graph,
                                                   Agedge_t *first_edge,
                                                   Agedge_t *second_edge) {
  /*
   * Check both sides of each pair explicitly. The root registry may contain
   * only headclip, for example, so merely swapping names while walking that
   * registry would never compare the declared headclip against the other
   * edge's undeclared tailclip.
   */
  for (size_t attribute_index = 0;
       attribute_index < ATTRIBUTE_COUNT(endpoint_attribute_names);
       attribute_index++) {
    const endpoint_attribute_names_t *const attribute_names =
        &endpoint_attribute_names[attribute_index];

    for (size_t endpoint_index = 0; endpoint_index < EDGE_ENDPOINT_COUNT;
         endpoint_index++) {
      const edge_endpoint_t first_endpoint = (edge_endpoint_t)endpoint_index;
      const edge_endpoint_t second_endpoint = opposite_endpoint(first_endpoint);
      const char *const first_attribute_name =
          attribute_names->names[first_endpoint];
      const char *const second_attribute_name =
          attribute_names->names[second_endpoint];

      const comparable_attribute_value_t first_value =
          named_attribute_value(root_graph, first_edge, first_attribute_name);
      const comparable_attribute_value_t second_value =
          named_attribute_value(root_graph, second_edge, second_attribute_name);
      if (resolved_port_attributes_are_equal(first_edge, first_attribute_name,
                                             second_edge,
                                             second_attribute_name)) {
        continue;
      }
      if (!edge_attribute_values_are_equal(
              first_edge, first_attribute_name, first_value, second_edge,
              second_attribute_name, second_value)) {
        return false;
      }
    }
  }
  return true;
}

static bool is_endpoint_attribute(const char *name) {
  return edge_attribute_has_flag(name, EDGE_ATTRIBUTE_ENDPOINT);
}

static bool edge_attributes_are_equal_with_endpoint_orientation(
    Agedge_t *first_edge, Agedge_t *second_edge,
    bool compare_opposite_endpoints) {
  Agraph_t *const root_graph = agroot(agraphof(first_edge));

  /*
   * Attribute symbols belong to the root graph, and their defaults apply even
   * when an edge does not mention the attribute in the DOT source. Walk the
   * root registry so this comparison covers every effective edge attribute.
   *
   * Cgraph also records whether a string is HTML-like separately from its
   * bytes. Plain text "<B>x</B>" and HTML <<B>x</B>> therefore have different
   * rendering semantics even though strcmp() sees the same characters.
   *
   * For edges running in opposite directions, head and tail exchange
   * grammatical roles while still naming the same physical endpoints. Compare
   * endpoint-owned attributes crosswise and compare arrow flags by physical
   * endpoint so the retained edge can borrow the suppressed edge's compatible
   * arrows without OR-ing incompatible packed arrow types. Same-direction
   * arrow attributes are also compared as computed flags, not as raw strings,
   * so explicit defaults and inactive arrowtail/arrowhead attributes do not
   * block concentration.
   */
  if (compare_opposite_endpoints && !opposite_endpoint_attributes_are_equal(
                                        root_graph, first_edge, second_edge)) {
    return false;
  }
  if (compare_opposite_endpoints &&
      !opposite_edge_arrows_are_compatible(first_edge, second_edge)) {
    return false;
  }
  if (!compare_opposite_endpoints &&
      !same_direction_edge_arrows_are_equal(first_edge, second_edge)) {
    return false;
  }
  if (!hyperlink_layer_attribute_groups_are_equal(
          root_graph, first_edge, second_edge, compare_opposite_endpoints)) {
    return false;
  }

  Agsym_t *attribute = agnxtattr(root_graph, AGEDGE, NULL);
  while (attribute != NULL) {
    if (!is_layout_only_edge_attribute(attribute->name) &&
        !is_arrow_attribute(attribute->name) &&
        !is_alias_attribute(attribute->name) &&
        attribute_affects_rendered_edge(root_graph, first_edge, second_edge,
                                        attribute->name) &&
        !resolved_port_attributes_are_equal(first_edge, attribute->name,
                                            second_edge, attribute->name) &&
        (!compare_opposite_endpoints ||
         !is_endpoint_attribute(attribute->name))) {
      const comparable_attribute_value_t first_value =
          declared_attribute_value(first_edge, attribute);
      const comparable_attribute_value_t second_value =
          declared_attribute_value(second_edge, attribute);
      if (!edge_attribute_values_are_equal(first_edge, attribute->name,
                                           first_value, second_edge,
                                           attribute->name, second_value)) {
        return false;
      }
    }

    attribute = agnxtattr(root_graph, AGEDGE, attribute);
  }
  return true;
}

bool gv_edge_attributes_are_equal(Agedge_t *first_edge, Agedge_t *second_edge) {
  return edge_attributes_are_equal_with_endpoint_orientation(
      first_edge, second_edge, false);
}

bool gv_opposite_edge_attributes_are_equal(Agedge_t *first_edge,
                                           Agedge_t *second_edge) {
  return edge_attributes_are_equal_with_endpoint_orientation(first_edge,
                                                             second_edge, true);
}
