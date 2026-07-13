## Process this file with automake to produce Makefile.in

CGRAPHPP_VERSION = "0:1:0"

AM_CPPFLAGS = \
	-I$(top_srcdir)/lib \
	-I$(top_srcdir)/lib/cdt \
	-I$(top_srcdir)/lib/cgraph

if WITH_WIN32
AM_CXXFLAGS = -Dcgraph___EXPORTS
endif

pkginclude_HEADERS = AGraph.h
lib_LTLIBRARIES = libcgraph++.la

libcgraph___la_SOURCES = AGraph.cpp
libcgraph___la_LDFLAGS = -version-info $(CGRAPHPP_VERSION) -no-undefined
libcgraph___la_LIBADD = $(top_builddir)/lib/cgraph/libcgraph.la
