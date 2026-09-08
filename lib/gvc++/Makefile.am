## Process this file with automake to produce Makefile.in

GVCPP_VERSION = "0:1:0"

AM_CPPFLAGS = \
	-I$(top_srcdir)/lib \
	-I$(top_srcdir)/lib/cdt \
	-I$(top_srcdir)/lib/cgraph \
	-I$(top_srcdir)/lib/cgraph++ \
	-I$(top_srcdir)/lib/common \
	-I$(top_srcdir)/lib/gvc \
	-I$(top_srcdir)/lib/pathplan

if WITH_WIN32
AM_CXXFLAGS = -Dgvc___EXPORTS
endif

pkginclude_HEADERS = GVContext.h GVLayout.h GVRenderData.h
lib_LTLIBRARIES = libgvc++.la

libgvc___la_SOURCES = GVContext.cpp GVLayout.cpp GVRenderData.cpp
libgvc___la_LDFLAGS = -version-info $(GVCPP_VERSION) -no-undefined
libgvc___la_LIBADD = \
	$(top_builddir)/lib/cgraph++/libcgraph++.la \
	$(top_builddir)/lib/gvc/libgvc.la
