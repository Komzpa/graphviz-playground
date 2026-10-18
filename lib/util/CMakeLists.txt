add_library(util STATIC
  arena.c
  base64.c
  gv_find_me.c
  gv_fopen.c
  list.c
  random.c
  xml.c
)

target_include_directories(util PRIVATE ..)

if(WIN32 AND NOT MINGW)
  target_include_directories(util PRIVATE ../../windows/include/unistd)
endif()

# From https://gitlab.com/graphviz/graphviz/-/issues/1613 @mdwies 20093010
if(APPLE)
  target_compile_options(util PRIVATE "-fno-common")
endif()

# static consumers of libpathplan must link this private archive themselves,
# because static archives carry no dependency metadata
if(NOT BUILD_SHARED_LIBS)
  install(
    TARGETS util
    ARCHIVE DESTINATION ${LIBRARY_INSTALL_DIR}
  )
endif()
