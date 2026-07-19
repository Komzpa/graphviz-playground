/*************************************************************************
 * Copyright (c) 2011 AT&T Intellectual Property
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

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

#define ATTRIBUTE_COUNT(attributes) \
  (sizeof(attributes) / sizeof((attributes)[0]))

typedef struct {
  const char *const *target_names;
  size_t target_names_size;
  const char *const *label_names;
  size_t label_names_size;
  const char *const *anchor_names;
  size_t anchor_names_size;
  const char *const *default_anchor_names;
  size_t default_anchor_names_size;
} target_alias_group_t;

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

static comparable_attribute_value_t named_first_nonempty_attribute_value(
    Agraph_t *root_graph, Agedge_t *edge, const char *const *attribute_names,
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

static bool is_stroke_or_fill_color_attribute(const char *name) {
  return strcmp(name, "color") == 0 || strcmp(name, "fillcolor") == 0;
}

static bool is_label_color_attribute(const char *name) {
  return strcmp(name, "fontcolor") == 0 ||
         strcmp(name, "labelfontcolor") == 0;
}

static bool is_color_attribute(const char *name) {
  return is_stroke_or_fill_color_attribute(name) ||
         is_label_color_attribute(name);
}

static bool is_label_only_attribute(const char *name) {
  return strcmp(name, "fontname") == 0 || strcmp(name, "fontsize") == 0 ||
         strcmp(name, "fontcolor") == 0 ||
         strcmp(name, "labelfontname") == 0 ||
         strcmp(name, "labelfontsize") == 0 ||
         strcmp(name, "labelfontcolor") == 0 ||
         strcmp(name, "labelangle") == 0 ||
         strcmp(name, "labeldistance") == 0 ||
         strcmp(name, "decorate") == 0 || strcmp(name, "labelfloat") == 0;
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
  if (strcmp(attribute_name, "fontcolor") == 0) {
    return edge_has_main_label(root_graph, edge) ||
           (edge_has_endpoint_label(root_graph, edge) &&
            named_attribute_value(root_graph, edge, "labelfontcolor")
                    .text[0] == '\0');
  }
  if (strcmp(attribute_name, "labelfontcolor") == 0) {
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

static comparable_attribute_value_t edge_color_default_value(
    Agraph_t *root_graph, Agedge_t *edge, const char *attribute_name) {
  if (strcmp(attribute_name, "fillcolor") == 0) {
    comparable_attribute_value_t color =
        named_attribute_value(root_graph, edge, "color");
    return color.text[0] == '\0' ? plain_attribute_value(DEFAULT_COLOR) : color;
  }
  if (strcmp(attribute_name, "labelfontcolor") == 0) {
    comparable_attribute_value_t fontcolor =
        named_attribute_value(root_graph, edge, "fontcolor");
    return fontcolor.text[0] == '\0' ? plain_attribute_value(DEFAULT_COLOR)
                                     : fontcolor;
  }
  return plain_attribute_value(DEFAULT_COLOR);
}

static comparable_attribute_value_t edge_effective_color_value(
    Agraph_t *root_graph, Agedge_t *edge, const char *attribute_name,
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

  char *const previous_color_scheme = setColorScheme(agget(edge, "colorscheme"));
  const int result = colorxlate(value.text, color, RGBA_BYTE);
  char *const restored_color_scheme = setColorScheme(previous_color_scheme);
  free(previous_color_scheme);
  free(restored_color_scheme);
  return result == COLOR_OK;
}

static bool edge_color_values_are_equal(Agedge_t *first_edge,
                                        comparable_attribute_value_t first_value,
                                        Agedge_t *second_edge,
                                        comparable_attribute_value_t second_value) {
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
  return strcmp(name, "headclip") == 0 || strcmp(name, "tailclip") == 0;
}

static bool is_layout_only_edge_attribute(const char *name) {
  return strcmp(name, "constraint") == 0 || strcmp(name, "weight") == 0 ||
         strcmp(name, "minlen") == 0;
}

static bool is_arrow_attribute(const char *name) {
  return strcmp(name, "arrowhead") == 0 || strcmp(name, "arrowtail") == 0 ||
         strcmp(name, "dir") == 0;
}

static bool is_url_alias_attribute(const char *name) {
  return strcmp(name, "URL") == 0 || strcmp(name, "href") == 0 ||
         strcmp(name, "edgeURL") == 0 || strcmp(name, "edgehref") == 0 ||
         strcmp(name, "labelURL") == 0 || strcmp(name, "labelhref") == 0 ||
         strcmp(name, "headURL") == 0 || strcmp(name, "headhref") == 0 ||
         strcmp(name, "tailURL") == 0 || strcmp(name, "tailhref") == 0;
}

static bool is_tooltip_alias_attribute(const char *name) {
  return strcmp(name, "tooltip") == 0 || strcmp(name, "edgetooltip") == 0;
}

static bool is_target_alias_attribute(const char *name) {
  return strcmp(name, "target") == 0 || strcmp(name, "edgetarget") == 0 ||
         strcmp(name, "labeltarget") == 0 ||
         strcmp(name, "headtarget") == 0 || strcmp(name, "tailtarget") == 0;
}

static bool is_alias_attribute(const char *name) {
  return is_url_alias_attribute(name) || is_tooltip_alias_attribute(name) ||
         is_target_alias_attribute(name);
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
  double default_value;
  double minimum = 0.0;

  if (strcmp(attribute_name, "arrowsize") == 0 ||
      strcmp(attribute_name, "penwidth") == 0 ||
      strcmp(attribute_name, "labeldistance") == 0) {
    default_value = 1.0;
  } else if (strcmp(attribute_name, "fontsize") == 0) {
    default_value = DEFAULT_FONTSIZE;
    minimum = MIN_FONTSIZE;
  } else if (strcmp(attribute_name, "labelfontsize") == 0) {
    default_value = DEFAULT_LABEL_FONTSIZE;
    minimum = MIN_FONTSIZE;
  } else if (strcmp(attribute_name, "labelangle") == 0) {
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

static comparable_attribute_value_t edge_fontname_default_value(
    Agraph_t *root_graph, Agedge_t *edge, const char *attribute_name) {
  if (strcmp(attribute_name, "labelfontname") == 0) {
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

static bool edge_style_values_are_equal(comparable_attribute_value_t first_value,
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

static bool edge_bool_default_values_are_equal(
    const char *attribute_name, comparable_attribute_value_t first_value,
    comparable_attribute_value_t second_value) {
  bool default_value;

  if (strcmp(attribute_name, "decorate") == 0 ||
      strcmp(attribute_name, "labelfloat") == 0) {
    default_value = false;
  } else {
    return false;
  }

  const bool first_bool =
      first_value.text[0] == '\0' ? default_value : mapbool(first_value.text);
  const bool second_bool =
      second_value.text[0] == '\0' ? default_value : mapbool(second_value.text);
  return first_bool == second_bool;
}

static bool is_endpoint_label_attribute(const char *name) {
  return strcmp(name, "headlabel") == 0 || strcmp(name, "taillabel") == 0;
}

static bool is_substituted_attribute(const char *name) {
  return strcmp(name, "id") == 0 || strcmp(name, "labeltooltip") == 0 ||
         strcmp(name, "headtooltip") == 0 ||
         strcmp(name, "tailtooltip") == 0;
}

static bool is_fontname_attribute(const char *name) {
  return strcmp(name, "fontname") == 0 ||
         strcmp(name, "labelfontname") == 0;
}

static bool edge_substituted_values_are_equal(
    Agedge_t *first_edge, comparable_attribute_value_t first_value,
    Agedge_t *second_edge, comparable_attribute_value_t second_value) {
  if (first_value.is_html || second_value.is_html) {
    return comparable_attribute_values_are_equal(first_value, second_value);
  }

  char *const first_text = strdup_and_subst_obj((char *)first_value.text,
                                                first_edge);
  char *const second_text = strdup_and_subst_obj((char *)second_value.text,
                                                 second_edge);
  const bool equal = strcmp(first_text, second_text) == 0;
  free(first_text);
  free(second_text);
  return equal;
}

static bool edge_label_values_are_equal(Agedge_t *first_edge,
                                        comparable_attribute_value_t first_value,
                                        Agedge_t *second_edge,
                                        comparable_attribute_value_t second_value) {
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

  if (strcmp(first_attribute_name, "dir") == 0 &&
      strcmp(second_attribute_name, "dir") == 0) {
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
      ((strcmp(first_attribute_name, "labelfontsize") == 0 &&
        edge_labelfontsize_values_are_equal(first_edge, first_value,
                                            second_edge, second_value)) ||
       edge_numeric_default_values_are_equal(first_attribute_name, first_value,
                                             second_value) ||
       (is_fontname_attribute(first_attribute_name) &&
        edge_fontname_values_are_equal(first_edge, first_attribute_name,
                                       first_value, second_edge,
                                       second_attribute_name, second_value)) ||
       (strcmp(first_attribute_name, "style") == 0 &&
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
  return arrow_flags_can_merge_at_endpoint(first_start_flags, second_end_flags) &&
         arrow_flags_can_merge_at_endpoint(first_end_flags, second_start_flags);
}

static bool named_substituted_attribute_values_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge,
    const char *const *first_names, size_t first_names_size,
    Agedge_t *second_edge, const char *const *second_names,
    size_t second_names_size) {
  const comparable_attribute_value_t first_value =
      named_first_nonempty_attribute_value(root_graph, first_edge, first_names,
                                           first_names_size);
  const comparable_attribute_value_t second_value =
      named_first_nonempty_attribute_value(root_graph, second_edge,
                                           second_names, second_names_size);

  return edge_substituted_values_are_equal(first_edge, first_value, second_edge,
                                           second_value);
}

static bool paired_alias_attribute_values_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge,
    const char *const *first_names, Agedge_t *second_edge,
    const char *const *second_names) {
  static const size_t alias_group_size = 2;
  return named_substituted_attribute_values_are_equal(
      root_graph, first_edge, first_names, alias_group_size, second_edge,
      second_names, alias_group_size);
}

static bool url_alias_group_is_rendered(
    Agraph_t *root_graph, Agedge_t *edge, const char *const *url_names,
    size_t url_names_size, const char *const *label_names,
    size_t label_names_size, const char *const *tooltip_names,
    size_t tooltip_names_size) {
  const bool has_url =
      named_first_nonempty_attribute_value(root_graph, edge, url_names,
                                           url_names_size)
          .text[0] != '\0';
  const bool has_explicit_tooltip = edge_has_any_of_attributes(
      root_graph, edge, tooltip_names, tooltip_names_size);

  if (label_names_size == 0) {
    return has_url || has_explicit_tooltip;
  }
  return edge_has_any_of_attributes(root_graph, edge, label_names,
                                    label_names_size) &&
         (has_url || has_explicit_tooltip);
}

static bool url_alias_groups_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge,
    const char *const *first_url_names, size_t first_url_names_size,
    const char *const *first_label_names, size_t first_label_names_size,
    const char *const *first_tooltip_names, size_t first_tooltip_names_size,
    Agedge_t *second_edge, const char *const *second_url_names,
    size_t second_url_names_size, const char *const *second_label_names,
    size_t second_label_names_size, const char *const *second_tooltip_names,
    size_t second_tooltip_names_size) {
  if (!url_alias_group_is_rendered(
          root_graph, first_edge, first_url_names, first_url_names_size,
          first_label_names, first_label_names_size, first_tooltip_names,
          first_tooltip_names_size) &&
      !url_alias_group_is_rendered(
          root_graph, second_edge, second_url_names, second_url_names_size,
          second_label_names, second_label_names_size, second_tooltip_names,
          second_tooltip_names_size)) {
    return true;
  }

  return named_substituted_attribute_values_are_equal(
      root_graph, first_edge, first_url_names, first_url_names_size, second_edge,
      second_url_names, second_url_names_size);
}

static bool url_alias_attribute_groups_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge, Agedge_t *second_edge,
    bool compare_opposite_endpoints) {
  static const char *const edge_url_names[] = {"edgehref", "edgeURL", "href",
                                               "URL"};
  static const char *const label_url_names[] = {"labelhref", "labelURL", "href",
                                                "URL"};
  static const char *const head_url_names[] = {
      "headhref", "headURL", "edgehref", "edgeURL", "href", "URL"};
  static const char *const tail_url_names[] = {
      "tailhref", "tailURL", "edgehref", "edgeURL", "href", "URL"};
  static const char *const label_names[] = {"label", "xlabel"};
  static const char *const edge_tooltip_names[] = {"tooltip", "edgetooltip"};
  static const char *const label_tooltip_names[] = {"labeltooltip"};
  static const char *const head_tooltip_names[] = {"headtooltip"};
  static const char *const tail_tooltip_names[] = {"tailtooltip"};

  if (!url_alias_groups_are_equal(
          root_graph, first_edge, edge_url_names, ATTRIBUTE_COUNT(edge_url_names),
          NULL, 0, edge_tooltip_names, ATTRIBUTE_COUNT(edge_tooltip_names),
          second_edge, edge_url_names, ATTRIBUTE_COUNT(edge_url_names), NULL, 0,
          edge_tooltip_names, ATTRIBUTE_COUNT(edge_tooltip_names)) ||
      !url_alias_groups_are_equal(
          root_graph, first_edge, label_url_names,
          ATTRIBUTE_COUNT(label_url_names), label_names,
          ATTRIBUTE_COUNT(label_names), label_tooltip_names,
          ATTRIBUTE_COUNT(label_tooltip_names), second_edge, label_url_names,
          ATTRIBUTE_COUNT(label_url_names), label_names,
          ATTRIBUTE_COUNT(label_names), label_tooltip_names,
          ATTRIBUTE_COUNT(label_tooltip_names))) {
    return false;
  }

  if (compare_opposite_endpoints) {
    return url_alias_groups_are_equal(
               root_graph, first_edge, head_url_names,
               ATTRIBUTE_COUNT(head_url_names), NULL, 0, head_tooltip_names,
               ATTRIBUTE_COUNT(head_tooltip_names), second_edge, tail_url_names,
               ATTRIBUTE_COUNT(tail_url_names), NULL, 0, tail_tooltip_names,
               ATTRIBUTE_COUNT(tail_tooltip_names)) &&
           url_alias_groups_are_equal(
               root_graph, first_edge, tail_url_names,
               ATTRIBUTE_COUNT(tail_url_names), NULL, 0, tail_tooltip_names,
               ATTRIBUTE_COUNT(tail_tooltip_names), second_edge, head_url_names,
               ATTRIBUTE_COUNT(head_url_names), NULL, 0, head_tooltip_names,
               ATTRIBUTE_COUNT(head_tooltip_names));
  }

  return url_alias_groups_are_equal(
             root_graph, first_edge, head_url_names,
             ATTRIBUTE_COUNT(head_url_names), NULL, 0, head_tooltip_names,
             ATTRIBUTE_COUNT(head_tooltip_names), second_edge, head_url_names,
             ATTRIBUTE_COUNT(head_url_names), NULL, 0, head_tooltip_names,
             ATTRIBUTE_COUNT(head_tooltip_names)) &&
         url_alias_groups_are_equal(
             root_graph, first_edge, tail_url_names,
             ATTRIBUTE_COUNT(tail_url_names), NULL, 0, tail_tooltip_names,
             ATTRIBUTE_COUNT(tail_tooltip_names), second_edge, tail_url_names,
             ATTRIBUTE_COUNT(tail_url_names), NULL, 0, tail_tooltip_names,
             ATTRIBUTE_COUNT(tail_tooltip_names));
}

static bool tooltip_alias_attributes_are_equal(Agraph_t *root_graph,
                                               Agedge_t *first_edge,
                                               Agedge_t *second_edge) {
  static const char *const tooltip_names[] = {"tooltip", "edgetooltip"};
  return paired_alias_attribute_values_are_equal(
      root_graph, first_edge, tooltip_names, second_edge, tooltip_names);
}

static bool target_alias_group_is_rendered(
    Agraph_t *root_graph, Agedge_t *edge, const target_alias_group_t *group) {
  const bool has_specific_anchor = edge_has_any_of_attributes(
      root_graph, edge, group->anchor_names, group->anchor_names_size);
  if (group->label_names_size == 0) {
    return has_specific_anchor;
  }
  if (!edge_has_any_of_attributes(root_graph, edge, group->label_names,
                                  group->label_names_size)) {
    return false;
  }
  return has_specific_anchor ||
         edge_has_any_of_attributes(root_graph, edge,
                                    group->default_anchor_names,
                                    group->default_anchor_names_size);
}

static bool target_alias_groups_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge,
    const target_alias_group_t *first_group, Agedge_t *second_edge,
    const target_alias_group_t *second_group) {
  if (!target_alias_group_is_rendered(root_graph, first_edge, first_group) &&
      !target_alias_group_is_rendered(root_graph, second_edge, second_group)) {
    return true;
  }

  return named_substituted_attribute_values_are_equal(
      root_graph, first_edge, first_group->target_names,
      first_group->target_names_size, second_edge, second_group->target_names,
      second_group->target_names_size);
}

static bool target_alias_attribute_groups_are_equal(
    Agraph_t *root_graph, Agedge_t *first_edge, Agedge_t *second_edge,
    bool compare_opposite_endpoints) {
  static const char *const default_anchor_names[] = {"href", "URL"};
  static const char *const edge_target_anchor_names[] = {
      "href", "URL", "edgehref", "edgeURL", "tooltip", "edgetooltip"};
  static const char *const label_names[] = {"label", "xlabel"};
  static const char *const head_label_names[] = {"headlabel"};
  static const char *const tail_label_names[] = {"taillabel"};
  static const char *const edge_target_names[] = {"edgetarget", "target"};
  static const char *const label_target_names[] = {"labeltarget", "target"};
  static const char *const head_target_names[] = {"headtarget", "target"};
  static const char *const tail_target_names[] = {"tailtarget", "target"};
  static const char *const label_target_anchor_names[] = {
      "labelhref", "labelURL", "labeltooltip"};
  static const char *const head_target_anchor_names[] = {
      "headhref", "headURL", "headtooltip"};
  static const char *const tail_target_anchor_names[] = {
      "tailhref", "tailURL", "tailtooltip"};
  static const target_alias_group_t edge_target_group = {
      .target_names = edge_target_names,
      .target_names_size = ATTRIBUTE_COUNT(edge_target_names),
      .anchor_names = edge_target_anchor_names,
      .anchor_names_size = ATTRIBUTE_COUNT(edge_target_anchor_names),
  };
  static const target_alias_group_t label_target_group = {
      .target_names = label_target_names,
      .target_names_size = ATTRIBUTE_COUNT(label_target_names),
      .label_names = label_names,
      .label_names_size = ATTRIBUTE_COUNT(label_names),
      .anchor_names = label_target_anchor_names,
      .anchor_names_size = ATTRIBUTE_COUNT(label_target_anchor_names),
      .default_anchor_names = default_anchor_names,
      .default_anchor_names_size = ATTRIBUTE_COUNT(default_anchor_names),
  };
  static const target_alias_group_t head_target_group = {
      .target_names = head_target_names,
      .target_names_size = ATTRIBUTE_COUNT(head_target_names),
      .label_names = head_label_names,
      .label_names_size = ATTRIBUTE_COUNT(head_label_names),
      .anchor_names = head_target_anchor_names,
      .anchor_names_size = ATTRIBUTE_COUNT(head_target_anchor_names),
      .default_anchor_names = default_anchor_names,
      .default_anchor_names_size = ATTRIBUTE_COUNT(default_anchor_names),
  };
  static const target_alias_group_t tail_target_group = {
      .target_names = tail_target_names,
      .target_names_size = ATTRIBUTE_COUNT(tail_target_names),
      .label_names = tail_label_names,
      .label_names_size = ATTRIBUTE_COUNT(tail_label_names),
      .anchor_names = tail_target_anchor_names,
      .anchor_names_size = ATTRIBUTE_COUNT(tail_target_anchor_names),
      .default_anchor_names = default_anchor_names,
      .default_anchor_names_size = ATTRIBUTE_COUNT(default_anchor_names),
  };

  if (!target_alias_groups_are_equal(root_graph, first_edge, &edge_target_group,
                                     second_edge, &edge_target_group) ||
      !target_alias_groups_are_equal(root_graph, first_edge, &label_target_group,
                                     second_edge, &label_target_group)) {
    return false;
  }

  if (compare_opposite_endpoints) {
    return target_alias_groups_are_equal(root_graph, first_edge,
                                         &head_target_group, second_edge,
                                         &tail_target_group) &&
           target_alias_groups_are_equal(root_graph, first_edge,
                                         &tail_target_group, second_edge,
                                         &head_target_group);
  }

  return target_alias_groups_are_equal(root_graph, first_edge,
                                       &head_target_group, second_edge,
                                       &head_target_group) &&
         target_alias_groups_are_equal(root_graph, first_edge,
                                       &tail_target_group, second_edge,
                                       &tail_target_group);
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
  if (strcmp(attribute_name, "fillcolor") == 0 &&
      !edge_has_any_arrows(first_edge) && !edge_has_any_arrows(second_edge)) {
    return false;
  }
  if (strcmp(attribute_name, "arrowsize") == 0 &&
      !edge_has_any_arrows(first_edge) && !edge_has_any_arrows(second_edge)) {
    return false;
  }
  return true;
}

static bool is_port_attribute(const char *name) {
  return strcmp(name, "headport") == 0 || strcmp(name, "tailport") == 0;
}

static port port_for_attribute(Agedge_t *edge, const char *attribute_name) {
  return strcmp(attribute_name, "headport") == 0 ? ED_head_port(edge)
                                                 : ED_tail_port(edge);
}

static bool port_values_are_equal(port first_port, port second_port) {
  return first_port.defined == second_port.defined &&
         (!first_port.defined ||
          (first_port.p.x == second_port.p.x &&
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
  return port_values_are_equal(ED_head_port(first_edge),
                               ED_head_port(second_edge)) &&
         port_values_are_equal(ED_tail_port(first_edge),
                               ED_tail_port(second_edge));
}

bool gv_opposite_edge_ports_are_equal(Agedge_t *first_edge,
                                      Agedge_t *second_edge) {
  return port_values_are_equal(ED_head_port(first_edge),
                               ED_tail_port(second_edge)) &&
         port_values_are_equal(ED_tail_port(first_edge),
                               ED_head_port(second_edge));
}

static bool opposite_endpoint_attributes_are_equal(Agraph_t *root_graph,
                                                   Agedge_t *first_edge,
                                                   Agedge_t *second_edge) {
  static const char *const endpoint_attribute_pairs[][2] = {
      {"headport", "tailport"},
      {"tailport", "headport"},
      {"headclip", "tailclip"},
      {"tailclip", "headclip"},
      {"headlabel", "taillabel"},
      {"taillabel", "headlabel"},
      {"lhead", "ltail"},
      {"ltail", "lhead"},
      {"samehead", "sametail"},
      {"sametail", "samehead"},
      {"headtarget", "tailtarget"},
      {"tailtarget", "headtarget"},
      {"headtooltip", "tailtooltip"},
      {"tailtooltip", "headtooltip"},
  };

  /*
   * Check both sides of each pair explicitly. The root registry may contain
   * only headclip, for example, so merely swapping names while walking that
   * registry would never compare the declared headclip against the other
   * edge's undeclared tailclip.
   */
  for (size_t pair_index = 0;
       pair_index <
       sizeof(endpoint_attribute_pairs) / sizeof(endpoint_attribute_pairs[0]);
       pair_index++) {
    const comparable_attribute_value_t first_value = named_attribute_value(
        root_graph, first_edge, endpoint_attribute_pairs[pair_index][0]);
    const comparable_attribute_value_t second_value = named_attribute_value(
        root_graph, second_edge, endpoint_attribute_pairs[pair_index][1]);
    if (resolved_port_attributes_are_equal(
            first_edge, endpoint_attribute_pairs[pair_index][0], second_edge,
            endpoint_attribute_pairs[pair_index][1])) {
      continue;
    }
    if (!edge_attribute_values_are_equal(
            first_edge, endpoint_attribute_pairs[pair_index][0], first_value,
            second_edge, endpoint_attribute_pairs[pair_index][1],
            second_value)) {
      return false;
    }
  }
  return true;
}

static bool is_endpoint_attribute(const char *name) {
  return strcmp(name, "headport") == 0 || strcmp(name, "tailport") == 0 ||
         strcmp(name, "headclip") == 0 || strcmp(name, "tailclip") == 0 ||
         strcmp(name, "headlabel") == 0 || strcmp(name, "taillabel") == 0 ||
         strcmp(name, "headURL") == 0 || strcmp(name, "tailURL") == 0 ||
         strcmp(name, "headhref") == 0 || strcmp(name, "tailhref") == 0 ||
         strcmp(name, "lhead") == 0 || strcmp(name, "ltail") == 0 ||
         strcmp(name, "samehead") == 0 || strcmp(name, "sametail") == 0 ||
         strcmp(name, "headtarget") == 0 || strcmp(name, "tailtarget") == 0 ||
         strcmp(name, "headtooltip") == 0 ||
         strcmp(name, "tailtooltip") == 0;
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
  if (!url_alias_attribute_groups_are_equal(root_graph, first_edge,
                                            second_edge,
                                            compare_opposite_endpoints) ||
      !tooltip_alias_attributes_are_equal(root_graph, first_edge,
                                          second_edge) ||
      !target_alias_attribute_groups_are_equal(root_graph, first_edge,
                                               second_edge,
                                               compare_opposite_endpoints)) {
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
      if (!edge_attribute_values_are_equal(
              first_edge, attribute->name, first_value, second_edge,
              attribute->name, second_value)) {
        return false;
      }
    }

    attribute = agnxtattr(root_graph, AGEDGE, attribute);
  }
  return true;
}

bool gv_edge_attributes_are_equal(Agedge_t *first_edge,
                                  Agedge_t *second_edge) {
  return edge_attributes_are_equal_with_endpoint_orientation(
      first_edge, second_edge, false);
}

bool gv_opposite_edge_attributes_are_equal(Agedge_t *first_edge,
                                           Agedge_t *second_edge) {
  return edge_attributes_are_equal_with_endpoint_orientation(first_edge,
                                                             second_edge, true);
}
