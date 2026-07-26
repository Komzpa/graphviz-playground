## Process this file with automake to produce Makefile.in

AM_CPPFLAGS = \
	-I$(top_srcdir)/lib \
	-I$(top_srcdir)/lib/common \
	-I$(top_srcdir)/lib/pack \
	-I$(top_srcdir)/lib/gvc \
	-I$(top_srcdir)/lib/cgraph \
	-I$(top_srcdir)/lib/cdt \
	-I$(top_srcdir)/lib/pathplan

noinst_HEADERS = dot.h dotprocs.h aspect.h bundle_load.h concentrate_plan.h \
	concentrate_splines.h spline_tuning.h
noinst_LTLIBRARIES = libdotgen_C.la

libdotgen_C_la_LDFLAGS = -no-undefined
libdotgen_C_la_SOURCES = acyclic.c bundle_load.c class1.c edge_chains.c \
	cluster.c compound.c conc.c concentrate_plan.c concentrate_splines.c decomp.c fastgr.c flat.c \
	dotinit.c mincross.c position.c rank.c sameport.c dotsplines.c aspect.c
