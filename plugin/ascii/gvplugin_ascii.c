/// @file
/// @brief Plugin registration for ASCII output.

#include "config.h"

#include <gvc/gvplugin.h>

extern gvplugin_installed_t gvdevice_ascii_lineart_types[];
extern gvplugin_installed_t gvrender_ascii_lineart_types[];

#ifdef HAVE_AALIB
extern gvplugin_installed_t gvdevice_ascii_cairo_types[];
#endif

static gvplugin_api_t apis[] = {
    {API_device, gvdevice_ascii_lineart_types},
#ifdef HAVE_AALIB
    // Equal-quality devices are last-wins, so keep cairo as bare -Tascii.
    {API_device, gvdevice_ascii_cairo_types},
#endif
    {API_render, gvrender_ascii_lineart_types},
    {0},
};

#ifdef GVDLL
#define GVPLUGIN_ASCII_API __declspec(dllexport)
#else
#define GVPLUGIN_ASCII_API
#endif

GVPLUGIN_ASCII_API gvplugin_library_t gvplugin_ascii_LTX_library = {"ascii",
                                                                    apis};
