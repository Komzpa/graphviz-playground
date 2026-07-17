## Process this file with automake to produce Makefile.in

AM_CPPFLAGS = \
	-I$(top_srcdir)/lib \
	-I$(top_srcdir)/lib/common \
	-I$(top_srcdir)/lib/pathplan \
	-I$(top_srcdir)/lib/gvc \
	-I$(top_srcdir)/lib/cgraph \
	-I$(top_srcdir)/lib/cdt

noinst_LTLIBRARIES = libgvplugin_ascii_C.la
if WITH_WIN32
lib_LTLIBRARIES = libgvplugin_ascii.la
else
pkglib_LTLIBRARIES = libgvplugin_ascii.la
endif

libgvplugin_ascii_C_la_SOURCES = \
	gvplugin_ascii.c \
	gvrender_ascii.c

libgvplugin_ascii_la_LDFLAGS = -version-info $(GVPLUGIN_VERSION_INFO)
libgvplugin_ascii_la_SOURCES = $(libgvplugin_ascii_C_la_SOURCES)
libgvplugin_ascii_la_LIBADD = \
	$(top_builddir)/lib/gvc/libgvc.la $(MATH_LIBS)

if WITH_AALIB
AM_CPPFLAGS += -DHAVE_AALIB $(AALIB_CFLAGS)
libgvplugin_ascii_la_SOURCES += gvdevice_ascii.c
libgvplugin_ascii_la_LIBADD += $(AA_LIBS)
endif

if WITH_WIN32
libgvplugin_ascii_la_LDFLAGS += -no-undefined
endif
