add_library(gvplugin_ascii
  gvplugin_ascii.c
  gvrender_ascii.c
)

target_include_directories(gvplugin_ascii PRIVATE
  ../../lib
  ../../lib/cdt
  ../../lib/cgraph
  ../../lib/common
  ../../lib/gvc
  ../../lib/pathplan
)

target_link_libraries(gvplugin_ascii PRIVATE cgraph gvc xdot)

if(AA_FOUND)
  target_sources(gvplugin_ascii PRIVATE gvdevice_ascii.c)
  target_compile_definitions(gvplugin_ascii PRIVATE HAVE_AALIB=1)
  target_include_directories(gvplugin_ascii SYSTEM PRIVATE ${AA_INCLUDE_DIRS})
  target_link_libraries(gvplugin_ascii PRIVATE ${AA_LIBRARIES})
endif()

if(BUILD_SHARED_LIBS)
  # Installation location of library files
  install(
    TARGETS gvplugin_ascii
    RUNTIME DESTINATION ${BINARY_INSTALL_DIR}
    LIBRARY DESTINATION ${PLUGIN_INSTALL_DIR}
    ARCHIVE DESTINATION ${LIBRARY_INSTALL_DIR}
  )
endif()

# Specify library version and soversion
set_target_properties(gvplugin_ascii PROPERTIES
  VERSION ${GVPLUGIN_CURRENT}.0.${GVPLUGIN_REVISION}
  SOVERSION ${GVPLUGIN_CURRENT}
)

if(MINGW)
  # work around https://gitlab.kitware.com/cmake/cmake/-/issues/21716
  set_target_properties(gvplugin_ascii PROPERTIES
    RUNTIME_OUTPUT_NAME gvplugin_ascii-${GVPLUGIN_CURRENT}
  )
endif()
