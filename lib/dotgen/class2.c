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
 * dot classifies edges in two different representations. class1() builds the
 * temporary constraint edges used to assign ranks. After those ranks are
 * fixed, class2() materializes the virtual nodes and edge chains consumed by
 * crossing minimization, node positioning, and spline routing.
 *
 * The numbered name is historical: both passes already had these names in the
 * oldest imported Graphviz sources. Keep the name at the external boundary,
 * but describe the post-rank representation explicitly inside this file.
 */

#include "config.h"

#include <common/colorprocs.h>
#include <common/utils.h>
#include <dotgen/dot.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <util/alloc.h>
#include <util/gv_math.h>

typedef struct {
  const char *text;
  bool is_html;
} comparable_attribute_value_t;

static comparable_attribute_value_t
declared_attribute_value(edge_t *edge, Agsym_t *attribute) {
  const char *const text = agxget(edge, attribute);
  return (comparable_attribute_value_t){
      .text = text,
      .is_html = aghtmlstr(text),
  };
}

static comparable_attribute_value_t
named_attribute_value(graph_t *root_graph, edge_t *edge,
                      const char *attribute_name) {
  Agsym_t *const attribute = agfindedgeattr(root_graph, (char *)attribute_name);
  if (attribute == NULL) {
    /*
     * An undeclared attribute has the plain, empty effective value. Keep its
     * representation explicit: aghtmlstr() accepts only Cgraph refstrings, so
     * passing this string literal to it would be undefined.
     */
    return (comparable_attribute_value_t){.text = "", .is_html = false};
  }
  return declared_attribute_value(edge, attribute);
}

static comparable_attribute_value_t named_first_nonempty_attribute_value(
    graph_t *root_graph, edge_t *edge, const char *const *attribute_names,
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

static bool is_color_attribute(const char *name) {
  return strcmp(name, "color") == 0 || strcmp(name, "fillcolor") == 0 ||
         strcmp(name, "fontcolor") == 0 ||
         strcmp(name, "labelfontcolor") == 0;
}

static bool edge_color_value(edge_t *edge, comparable_attribute_value_t value,
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

static bool edge_color_values_are_equal(edge_t *first_edge,
                                        comparable_attribute_value_t first_value,
                                        edge_t *second_edge,
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

static bool edge_clip_value(comparable_attribute_value_t value) {
  return value.text[0] == '\0' || mapbool(value.text);
}

static const char *edge_direction_value(edge_t *edge,
                                        comparable_attribute_value_t value) {
  if (value.text[0] == '\0') {
    return agisdirected(agraphof(edge)) ? "forward" : "none";
  }
  return value.text;
}

static bool edge_attribute_values_are_equal(
    edge_t *first_edge, const char *first_attribute_name,
    comparable_attribute_value_t first_value, edge_t *second_edge,
    const char *second_attribute_name,
    comparable_attribute_value_t second_value) {
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
      is_color_attribute(second_attribute_name) &&
      edge_color_values_are_equal(first_edge, first_value, second_edge,
                                  second_value)) {
    return true;
  }

  return comparable_attribute_values_are_equal(first_value, second_value);
}

static bool arrow_flags_can_merge_at_endpoint(uint32_t first_flags,
                                              uint32_t second_flags) {
  return first_flags == 0 || second_flags == 0 || first_flags == second_flags;
}

static bool opposite_edge_arrows_are_compatible(edge_t *first_edge,
                                                edge_t *second_edge) {
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

static bool url_alias_attributes_are_equal(graph_t *root_graph,
                                           edge_t *first_edge,
                                           const char *const *first_names,
                                           edge_t *second_edge,
                                           const char *const *second_names) {
  static const size_t url_alias_group_size = 2;
  const comparable_attribute_value_t first_value =
      named_first_nonempty_attribute_value(root_graph, first_edge, first_names,
                                           url_alias_group_size);
  const comparable_attribute_value_t second_value =
      named_first_nonempty_attribute_value(root_graph, second_edge,
                                           second_names, url_alias_group_size);

  return comparable_attribute_values_are_equal(first_value, second_value);
}

static bool url_alias_attribute_groups_are_equal(
    graph_t *root_graph, edge_t *first_edge, edge_t *second_edge,
    bool compare_opposite_endpoints) {
  static const char *const default_url_names[] = {"href", "URL"};
  static const char *const edge_url_names[] = {"edgehref", "edgeURL"};
  static const char *const label_url_names[] = {"labelhref", "labelURL"};
  static const char *const head_url_names[] = {"headhref", "headURL"};
  static const char *const tail_url_names[] = {"tailhref", "tailURL"};

  if (!url_alias_attributes_are_equal(root_graph, first_edge, default_url_names,
                                      second_edge, default_url_names) ||
      !url_alias_attributes_are_equal(root_graph, first_edge, edge_url_names,
                                      second_edge, edge_url_names) ||
      !url_alias_attributes_are_equal(root_graph, first_edge, label_url_names,
                                      second_edge, label_url_names)) {
    return false;
  }

  if (compare_opposite_endpoints) {
    return url_alias_attributes_are_equal(root_graph, first_edge,
                                          head_url_names, second_edge,
                                          tail_url_names) &&
           url_alias_attributes_are_equal(root_graph, first_edge,
                                          tail_url_names, second_edge,
                                          head_url_names);
  }

  return url_alias_attributes_are_equal(root_graph, first_edge, head_url_names,
                                        second_edge, head_url_names) &&
         url_alias_attributes_are_equal(root_graph, first_edge, tail_url_names,
                                        second_edge, tail_url_names);
}

static bool same_direction_edge_arrows_are_equal(edge_t *first_edge,
                                                 edge_t *second_edge) {
  uint32_t first_start_flags;
  uint32_t first_end_flags;
  uint32_t second_start_flags;
  uint32_t second_end_flags;

  edge_arrow_flags(first_edge, &first_start_flags, &first_end_flags);
  edge_arrow_flags(second_edge, &second_start_flags, &second_end_flags);

  return first_start_flags == second_start_flags &&
         first_end_flags == second_end_flags;
}

static bool is_port_attribute(const char *name) {
  return strcmp(name, "headport") == 0 || strcmp(name, "tailport") == 0;
}

static port port_for_attribute(edge_t *edge, const char *attribute_name) {
  return strcmp(attribute_name, "headport") == 0 ? ED_head_port(edge)
                                                 : ED_tail_port(edge);
}

static bool resolved_port_values_are_equal(port first_port, port second_port) {
  return first_port.defined && second_port.defined &&
         first_port.p.x == second_port.p.x && first_port.p.y == second_port.p.y;
}

static bool resolved_port_attributes_are_equal(edge_t *first_edge,
                                               const char *first_attribute_name,
                                               edge_t *second_edge,
                                               const char *second_attribute_name) {
  return is_port_attribute(first_attribute_name) &&
         is_port_attribute(second_attribute_name) &&
         resolved_port_values_are_equal(
             port_for_attribute(first_edge, first_attribute_name),
             port_for_attribute(second_edge, second_attribute_name));
}

static bool opposite_endpoint_attributes_are_equal(graph_t *root_graph,
                                                   edge_t *first_edge,
                                                   edge_t *second_edge) {
  static const char *const endpoint_attribute_pairs[][2] = {
      {"headport", "tailport"},
      {"tailport", "headport"},
      {"headclip", "tailclip"},
      {"tailclip", "headclip"},
      {"headlabel", "taillabel"},
      {"taillabel", "headlabel"},
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
         strcmp(name, "samehead") == 0 || strcmp(name, "sametail") == 0 ||
         strcmp(name, "headtarget") == 0 || strcmp(name, "tailtarget") == 0 ||
         strcmp(name, "headtooltip") == 0 ||
         strcmp(name, "tailtooltip") == 0;
}

static bool edge_attributes_are_equal_with_endpoint_orientation(
    edge_t *first_edge, edge_t *second_edge, bool compare_opposite_endpoints) {
  graph_t *const root_graph = agroot(agraphof(first_edge));

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
                                            compare_opposite_endpoints)) {
    return false;
  }

  Agsym_t *attribute = agnxtattr(root_graph, AGEDGE, NULL);
  while (attribute != NULL) {
    if (!is_layout_only_edge_attribute(attribute->name) &&
        !is_arrow_attribute(attribute->name) &&
        !is_url_alias_attribute(attribute->name) &&
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

bool edge_attributes_are_equal(edge_t *first_edge, edge_t *second_edge) {
  return edge_attributes_are_equal_with_endpoint_orientation(
      first_edge, second_edge, false);
}

bool opposite_edge_attributes_are_equal(edge_t *first_edge,
                                        edge_t *second_edge) {
  return edge_attributes_are_equal_with_endpoint_orientation(first_edge,
                                                             second_edge, true);
}

static node_t *make_label_virtual_node(graph_t *graph, edge_t *original_edge) {
  const pointf label_dimensions = ED_label(original_edge)->dimen;
  node_t *const label_node = virtual_node(graph);
  ND_label(label_node) = ED_label(original_edge);
  ND_lw(label_node) = GD_nodesep(agroot(label_node));
  if (!ED_label_ontop(original_edge)) {
    if (GD_flip(agroot(graph))) {
      ND_ht(label_node) = label_dimensions.x;
      ND_rw(label_node) = label_dimensions.y;
    } else {
      ND_ht(label_node) = label_dimensions.y;
      ND_rw(label_node) = label_dimensions.x;
    }
  }
  return label_node;
}

static void widen_virtual_node(graph_t *graph, node_t *virtual_node) {
  const int half_node_separation = GD_nodesep(graph) / 2;
  ND_lw(virtual_node) += half_node_separation;
  ND_rw(virtual_node) += half_node_separation;
}

static node_t *make_plain_virtual_node(graph_t *graph) {
  node_t *const plain_node = virtual_node(graph);
  widen_virtual_node(graph, plain_node);
  return plain_node;
}

static node_t *rank_leader(node_t *node) {
  if (ND_ranktype(node) != CLUSTER) {
    return UF_find(node);
  }

  graph_t *const cluster = ND_clust(node);
  return GD_rankleader(cluster)[ND_rank(node)];
}

/// Create a rank-by-rank chain of virtual edges for an original edge.
static void make_virtual_edge_chain(graph_t *graph, node_t *first_node,
                                    node_t *last_node, edge_t *original_edge) {
  const int label_rank = ED_label(original_edge)
                             ? (ND_rank(first_node) + ND_rank(last_node)) / 2
                             : -1;
  node_t *chain_tail = first_node;

  assert(ED_to_virt(original_edge) == NULL);
  for (int rank = ND_rank(first_node) + 1; rank <= ND_rank(last_node); rank++) {
    node_t *chain_head;
    if (rank < ND_rank(last_node)) {
      chain_head = rank == label_rank
                       ? make_label_virtual_node(graph, original_edge)
                       : make_plain_virtual_node(graph);
      ND_rank(chain_head) = rank;
    } else {
      chain_head = last_node;
    }

    edge_t *const virtual_segment =
        virtual_edge(chain_tail, chain_head, original_edge);
    virtual_weight(virtual_segment);
    chain_tail = chain_head;
  }
  assert(ED_to_virt(original_edge) != NULL);
}

static void represent_intercluster_edge(graph_t *graph, edge_t *original_edge) {
  node_t *tail_leader = rank_leader(agtail(original_edge));
  node_t *head_leader = rank_leader(aghead(original_edge));
  if (ND_rank(tail_leader) > ND_rank(head_leader)) {
    SWAP(&tail_leader, &head_leader);
  }
  if (ND_clust(tail_leader) != ND_clust(head_leader)) {
    edge_t *virtual_edge = find_fast_edge(tail_leader, head_leader);
    if (virtual_edge != NULL) {
      merge_chain(graph, original_edge, virtual_edge, true);
      return;
    }
    if (ND_rank(tail_leader) == ND_rank(head_leader)) {
      return;
    }
    make_virtual_edge_chain(graph, tail_leader, head_leader, original_edge);

    /* The chain remains distinguishable while cluster expansion rewrites it. */
    virtual_edge = ED_to_virt(original_edge);
    while (virtual_edge != NULL &&
           ND_rank(aghead(virtual_edge)) <= ND_rank(head_leader)) {
      ED_edge_type(virtual_edge) = CLUSTER_EDGE;
      virtual_edge = ND_out(aghead(virtual_edge)).list[0];
    }
  }
  /* else ignore intra-cluster edges at this point */
}

static bool is_cluster_edge(edge_t *edge) {
  return ND_ranktype(agtail(edge)) == CLUSTER ||
         ND_ranktype(aghead(edge)) == CLUSTER;
}

void merge_chain(graph_t *graph, edge_t *original_edge,
                 edge_t *first_virtual_edge, bool update_count) {
  const int last_rank =
      MAX(ND_rank(agtail(original_edge)), ND_rank(aghead(original_edge)));

  assert(ED_to_virt(original_edge) == NULL);
  ED_to_virt(original_edge) = first_virtual_edge;
  edge_t *representative_edge = first_virtual_edge;
  do {
    /* interclust multi-edges are not counted now */
    if (update_count) {
      ED_count(representative_edge) += ED_count(original_edge);
    }
    ED_xpenalty(representative_edge) += ED_xpenalty(original_edge);
    ED_weight(representative_edge) += ED_weight(original_edge);
    if (ND_rank(aghead(representative_edge)) == last_rank) {
      break;
    }
    widen_virtual_node(graph, aghead(representative_edge));
    representative_edge = ND_out(aghead(representative_edge)).list[0];
  } while (representative_edge != NULL);
}

bool mergeable(edge_t *first_edge, edge_t *second_edge) {
  return first_edge != NULL && second_edge != NULL &&
         agtail(first_edge) == agtail(second_edge) &&
         aghead(first_edge) == aghead(second_edge) &&
         ED_label(first_edge) == ED_label(second_edge) &&
         ports_eq(first_edge, second_edge);
}

static bool has_prior_concentrated_equivalent(graph_t *graph, edge_t *edge) {
  /*
   * class2() visits a node's outgoing edges in Cgraph order. Only an earlier
   * edge can already own the virtual chain that this edge would duplicate, so
   * stop at the current edge and never let later input affect the decision.
   */
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_owns_chain = ED_to_virt(prior_edge) != NULL;
    const bool both_edges_are_unlabeled =
        ED_label(prior_edge) == NULL && ED_label(edge) == NULL;

    if (same_endpoints && prior_edge_owns_chain && both_edges_are_unlabeled &&
        ports_eq(prior_edge, edge) &&
        edge_attributes_are_equal(prior_edge, edge)) {
      return true;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return false;
}

static edge_t *find_prior_parallel_route(graph_t *graph, edge_t *edge) {
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_owns_chain = ED_to_virt(prior_edge) != NULL;
    const bool both_edges_are_unlabeled =
        ED_label(prior_edge) == NULL && ED_label(edge) == NULL;

    if (same_endpoints && prior_edge_owns_chain && both_edges_are_unlabeled &&
        ports_eq(prior_edge, edge)) {
      return prior_edge;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return NULL;
}

static edge_t *find_prior_flat_concentrated_equivalent(graph_t *graph,
                                                       edge_t *edge) {
  edge_t *prior_edge = agfstout(graph, agtail(edge));
  while (prior_edge != NULL && prior_edge != edge) {
    const bool same_endpoints = aghead(prior_edge) == aghead(edge);
    const bool prior_edge_is_flat =
        ND_rank(agtail(prior_edge)) == ND_rank(aghead(prior_edge));
    const bool both_edges_are_unlabeled =
        ED_label(prior_edge) == NULL && ED_label(edge) == NULL;

    if (same_endpoints && prior_edge_is_flat &&
        ED_edge_type(prior_edge) == NORMAL && ED_edge_type(edge) == NORMAL &&
        both_edges_are_unlabeled && ports_eq(prior_edge, edge) &&
        edge_attributes_are_equal(prior_edge, edge)) {
      return prior_edge;
    }

    prior_edge = agnxtout(graph, prior_edge);
  }
  return NULL;
}

/*
 * Suppression is a state transition, not merely an equivalence query. Keep the
 * decision and the IGNORED assignment together so callers cannot accidentally
 * recognize a duplicate without removing its redundant virtual chain.
 */
static bool route_concentrated_parallel_edge(graph_t *graph, edge_t *edge) {
  if (!Concentrate) {
    return false;
  }

  if (has_prior_concentrated_equivalent(graph, edge)) {
    ED_edge_type(edge) = IGNORED;
    return true;
  }

  edge_t *const representative_edge = find_prior_parallel_route(graph, edge);
  if (representative_edge == NULL) {
    return false;
  }

  /*
   * A distinct edge still needs the ordinary multi-edge route. Giving it a
   * separate main virtual chain makes the spline router treat it as a separate
   * concentrated path and the rendered edges collapse onto the same centerline.
   */
  merge_chain(graph, edge, ED_to_virt(representative_edge), true);
  other_edge(edge);
  return true;
}

bool opposite_edge_ports_are_equal(edge_t *edge, edge_t *opposite_edge) {
  /*
   * These edges connect the same physical endpoints in reverse directions.
   * A head port on one edge is therefore comparable to the tail port on the
   * other edge, not to its head port. portcmp() also distinguishes an omitted
   * port from an explicitly defined port before comparing coordinates.
   */
  return portcmp(ED_head_port(edge), ED_tail_port(opposite_edge)) == 0 &&
         portcmp(ED_tail_port(edge), ED_head_port(opposite_edge)) == 0;
}

/*
 * A backward edge can share the chain of a previously classified edge running
 * in the other direction. When concentration is enabled, the opposite edge
 * must also be semantically equal because the backward edge will disappear.
 * Without concentration, both edges remain visible and share only their route.
 */
static bool merge_backward_edge_with_opposite(graph_t *graph,
                                              edge_t *backward_edge) {
  if (Concentrate && has_prior_concentrated_equivalent(graph, backward_edge)) {
    ED_edge_type(backward_edge) = IGNORED;
    return true;
  }

  edge_t *opposite_edge = agfstout(graph, aghead(backward_edge));
  edge_t *route_candidate = NULL;

  while (opposite_edge != NULL) {
    const bool connects_same_nodes =
        aghead(opposite_edge) == agtail(backward_edge);
    const bool is_self_edge = aghead(opposite_edge) == aghead(backward_edge);
    const bool is_available = ED_edge_type(opposite_edge) != IGNORED;

    if (connects_same_nodes && !is_self_edge && is_available) {
      const bool compatible_endpoints =
          ED_label(backward_edge) == NULL && ED_label(opposite_edge) == NULL &&
          opposite_edge_ports_are_equal(backward_edge, opposite_edge);
      if (compatible_endpoints) {
        if (Concentrate &&
            opposite_edge_attributes_are_equal(backward_edge, opposite_edge)) {
          /*
           * Materialize only the selected representative ahead of its normal
           * turn. Creating chains for every candidate while scanning would
           * pre-classify later parallel edges before duplicate suppression can
           * compare them.
           */
          if (ED_to_virt(opposite_edge) == NULL) {
            make_virtual_edge_chain(graph, agtail(opposite_edge),
                                    aghead(opposite_edge), opposite_edge);
          }
          /*
           * Preserve the suppressed edge's identity so arrow rendering can
           * recover the endpoint arrows for this exact pair.
           */
          ED_edge_type(backward_edge) = IGNORED;
          ED_conc_opp_flag(opposite_edge) = true;
          remember_suppressed_opposite_edge(opposite_edge, backward_edge);
          return true;
        }
        if (!Concentrate) {
          if (ED_to_virt(opposite_edge) == NULL) {
            make_virtual_edge_chain(graph, agtail(opposite_edge),
                                    aghead(opposite_edge), opposite_edge);
          }
          other_edge(backward_edge);
          merge_chain(graph, backward_edge, ED_to_virt(opposite_edge), true);
          return true;
        }
        if (route_candidate == NULL) {
          route_candidate = opposite_edge;
        } else {
          /*
           * Keep scanning in concentrate mode. A later opposite edge may be
           * the semantic mate that should be suppressed rather than drawn.
           */
        }
      }
    }

    opposite_edge = agnxtout(graph, opposite_edge);
  }
  if (route_candidate != NULL) {
    if (ED_to_virt(route_candidate) == NULL) {
      make_virtual_edge_chain(graph, agtail(route_candidate),
                              aghead(route_candidate), route_candidate);
    }
    other_edge(backward_edge);
    merge_chain(graph, backward_edge, ED_to_virt(route_candidate), true);
    return true;
  }
  return false;
}

void class2(graph_t *graph) {
  GD_nlist(graph) = NULL;

  /* Cluster skeletons stand in for collapsed cluster contents in this pass. */
  mark_clusters(graph);
  for (int cluster_index = 1; cluster_index <= GD_n_cluster(graph);
       cluster_index++) {
    build_skeleton(graph, GD_clust(graph)[cluster_index]);
  }

  /*
   * The weight class is a saturated incident-edge count. Exact high degrees
   * are not useful to the later low-degree heuristic, so stop incrementing
   * after the value has reached its three-or-more bucket.
   */
  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    for (edge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {
      if (ND_weight_class(aghead(edge)) <= 2) {
        ND_weight_class(aghead(edge))++;
      }
      if (ND_weight_class(agtail(edge)) <= 2) {
        ND_weight_class(agtail(edge))++;
      }
    }
  }

  for (node_t *node = agfstnode(graph); node != NULL;
       node = agnxtnode(graph, node)) {
    if (ND_clust(node) == NULL && node == UF_find(node)) {
      fast_node(graph, node);
    }
    edge_t *previous_edge = NULL;
    for (edge_t *edge = agfstout(graph, node); edge != NULL;
         edge = agnxtout(graph, edge)) {

      /* A prior cluster operation has already attached this representation. */
      if (ED_to_virt(edge) != NULL) {
        previous_edge = edge;
        continue;
      }

      /* Edges incident to a collapsed sub-cluster use its rank skeleton. */
      if (is_cluster_edge(edge)) {
        if (mergeable(previous_edge, edge)) {
          if (ED_to_virt(previous_edge) != NULL) {
            merge_chain(graph, edge, ED_to_virt(previous_edge), false);
            other_edge(edge);
          } else if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
            merge_oneway(edge, previous_edge);
            other_edge(edge);
          }
          /* An intra-cluster edge needs no representation at this level. */
          continue;
        }
        represent_intercluster_edge(graph, edge);
        previous_edge = edge;
        continue;
      }

      /* Parallel input edges may share one virtual routing representation. */
      if (previous_edge != NULL && agtail(edge) == agtail(previous_edge) &&
          aghead(edge) == aghead(previous_edge)) {
        if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
          edge_t *representative_edge = previous_edge;
          if (Concentrate) {
            edge_t *const equivalent_edge =
                find_prior_flat_concentrated_equivalent(graph, edge);
            if (equivalent_edge != NULL) {
              representative_edge = equivalent_edge;
            }
          }
          merge_oneway(edge, representative_edge);
          other_edge(edge);
          continue;
        }
        if (ED_label(edge) == NULL && ED_label(previous_edge) == NULL &&
            ports_eq(edge, previous_edge)) {
          if (Concentrate) {
            if (route_concentrated_parallel_edge(graph, edge)) {
              continue;
            }
          } else {
            merge_chain(graph, edge, ED_to_virt(previous_edge), true);
            other_edge(edge);
            continue;
          }
        }
        /* A semantically distinct parallel edge gets its own chain below. */
      }

      /* Self edges bypass rank-spanning chains and are routed from ND_other. */
      if (agtail(edge) == aghead(edge)) {
        other_edge(edge);
        previous_edge = edge;
        continue;
      }

      node_t *const tail_leader = UF_find(agtail(edge));
      node_t *const head_leader = UF_find(aghead(edge));

      /* Leaf-set members are represented through their union-find leaders. */
      if (agtail(edge) != tail_leader || aghead(edge) != head_leader) {
        /* FIX need to merge stuff */
        continue;
      }

      /* A flat edge stays within one rank and uses the flat-edge lists. */
      if (ND_rank(agtail(edge)) == ND_rank(aghead(edge))) {
        flat_edge(graph, edge);
        previous_edge = edge;
        continue;
      }

      if (ND_rank(aghead(edge)) > ND_rank(agtail(edge))) {
        if (route_concentrated_parallel_edge(graph, edge)) {
          continue;
        }
        make_virtual_edge_chain(graph, agtail(edge), aghead(edge), edge);
        previous_edge = edge;
        continue;
      }

      /* Store every rank-spanning chain from lower rank to higher rank. */
      if (merge_backward_edge_with_opposite(graph, edge)) {
        continue;
      }
      if (route_concentrated_parallel_edge(graph, edge)) {
        continue;
      }
      make_virtual_edge_chain(graph, aghead(edge), agtail(edge), edge);
      previous_edge = edge;
    }
  }

  /* decompose() is not called on subgraphs, so publish their sole component. */
  if (graph != dot_root(graph)) {
    free(GD_comp(graph).list);
    GD_comp(graph).list = gv_alloc(sizeof(node_t *));
    GD_comp(graph).list[0] = GD_nlist(graph);
  }
}
