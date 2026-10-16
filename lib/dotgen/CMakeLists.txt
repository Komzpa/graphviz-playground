add_library(dotgen STATIC
  aspect.c
  acyclic.c
  bundle_load.c
  class1.c
  edge_chains.c
  flat_edge_splines.c
  cluster.c
  compound.c
  conc.c
  concentrate_splines.c
  concentrate_plan.c
  decomp.c
  dotinit.c
  concentrate_junction.c
  dotsplines.c
  fastgr.c
  flat.c
  mincross.c
  polygon_arrow_spread.c
  position.c
  rank.c
  sameport.c
)

target_include_directories(dotgen PRIVATE
  ..
  ../cdt
  ../cgraph
  ../common
  ../gvc
  ../pack
  ../pathplan
)

target_link_libraries(dotgen PRIVATE
  cgraph
  util
)
