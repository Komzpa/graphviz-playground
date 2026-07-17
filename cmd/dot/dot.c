/**
 * @file
 * @brief main rendering program for various layouts of graphs and output formats
 */

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
 * Written by Stephen North and Eleftherios Koutsofios.
 */

#include "config.h"

#include <cgraph/cgraph.h>
#include <gvc/gvc.h>
#include <gvc/gvio.h>
#include <util/exit.h>

#include <common/globals.h>

#include <ctype.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static GVC_t *Gvc;
static graph_t * G;

typedef struct {
    int argc;
    char **argv;
} batch_args_t;

#ifndef _WIN32
#ifndef NO_FPERR
static void fperr(int s)
{
    fprintf(stderr, "caught SIGFPE %d\n", s);
    /* signal (s, SIG_DFL); raise (s); */
    graphviz_exit(1);
}
#endif
#endif

static void free_batch_args(batch_args_t *args)
{
    for (int i = 1; i < args->argc; ++i)
        free(args->argv[i]);
    free(args->argv);
    args->argv = NULL;
    args->argc = 0;
}

static int parse_batch_line(const char *line, const char *argv0,
                            batch_args_t *args)
{
    size_t argv_cap = 8;
    args->argc = 1;
    args->argv = calloc(argv_cap, sizeof(char *));
    if (!args->argv) {
        fprintf(stderr, "out of memory parsing batch line\n");
        return 1;
    }
    args->argv[0] = (char *)argv0;

    const unsigned char *p = (const unsigned char *)line;
    while (*p && isspace(*p))
        ++p;
    if (*p == '\0' || *p == '#')
        return 0;

    while (*p) {
        while (*p && isspace(*p))
            ++p;
        if (*p == '\0' || *p == '#')
            break;

        const size_t token_cap = strlen((const char *)p) + 1;
        char *token = malloc(token_cap);
        if (!token) {
            fprintf(stderr, "out of memory parsing batch line\n");
            return 1;
        }
        size_t token_len = 0;
        unsigned char quote = 0;

        while (*p) {
            const unsigned char c = *p++;
            if (quote) {
                if (c == quote) {
                    quote = 0;
                } else if (c == '\\' && (*p == quote || *p == '\\')) {
                    token[token_len++] = (char)*p++;
                } else {
                    token[token_len++] = (char)c;
                }
            } else if (isspace(c)) {
                break;
            } else if (c == '\'' || c == '"') {
                quote = c;
            } else if (c == '\\' && (*p == '\\' || isspace(*p) || *p == '\'' ||
                       *p == '"' || *p == '#')) {
                token[token_len++] = (char)*p++;
            } else {
                token[token_len++] = (char)c;
            }
        }

        if (quote) {
            fprintf(stderr, "unterminated quote in batch line: %s\n", line);
            free(token);
            return 1;
        }
        token[token_len] = '\0';

        if ((size_t)args->argc + 1 >= argv_cap) {
            argv_cap *= 2;
            char **new_argv = realloc(args->argv, argv_cap * sizeof(char *));
            if (!new_argv) {
                fprintf(stderr, "out of memory parsing batch line\n");
                free(token);
                return 1;
            }
            args->argv = new_argv;
        }
        args->argv[args->argc++] = token;
        args->argv[args->argc] = NULL;
    }

    return 0;
}

static int run_dot_invocation(int argc, char **argv)
{
    graph_t *prev = NULL;
    int r, rc = 0;

    Gvc = gvContextPlugins(lt_preloaded_symbols, DEMAND_LOADING);
    GvExitOnUsage = 1;
    gvParseArgs(Gvc, argc, argv);
#ifndef _WIN32
    signal(SIGUSR1, gvToggle);
#ifndef NO_FPERR
    signal(SIGFPE, fperr);
#endif
#endif

    if ((G = gvPluginsGraph(Gvc))) {
	    gvLayoutJobs(Gvc, G);  /* take layout engine from command line */
	    gvRenderJobs(Gvc, G);
    }
    else {
	while ((G = gvNextInputGraph(Gvc))) {
	    if (prev) {
		gvFreeLayout(Gvc, prev);
		agclose(prev);
	    }
	    gvLayoutJobs(Gvc, G);  /* take layout engine from command line */
	    gvRenderJobs(Gvc, G);
	    r = agreseterrors();
	    rc = MAX(rc,r);
	    prev = G;
	}
    }
    gvFinalize(Gvc);
    r = gvFreeContext(Gvc);
    return MAX(rc, r);
}

static const char *batch_filename(int argc, char **argv)
{
    const char *batch = NULL;
    int batch_argc = 0;

    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "-B") == 0) {
            if (batch) {
                fprintf(stderr, "-B batch mode cannot be specified more than once\n");
                graphviz_exit(1);
            }
            if (i + 1 >= argc) {
                fprintf(stderr, "Missing argument for -B flag\n");
                graphviz_exit(1);
            }
            batch = argv[++i];
            batch_argc += 2;
        } else if (strncmp(argv[i], "-B", 2) == 0 && argv[i][2] != '\0') {
            if (batch) {
                fprintf(stderr, "-B batch mode cannot be specified more than once\n");
                graphviz_exit(1);
            }
            batch = argv[i] + 2;
            ++batch_argc;
        } else if (strncmp(argv[i], "--batch=", strlen("--batch=")) == 0) {
            if (batch) {
                fprintf(stderr, "-B batch mode cannot be specified more than once\n");
                graphviz_exit(1);
            }
            batch = argv[i] + strlen("--batch=");
            ++batch_argc;
        }
    }

    if (batch && batch_argc + 1 != argc) {
        fprintf(stderr, "-B batch mode cannot be combined with other arguments\n");
        graphviz_exit(1);
    }

    return batch;
}

static int run_batch(char **argv, const char *filename)
{
    FILE *batch = fopen(filename, "r");
    if (!batch) {
        fprintf(stderr, "%s: can't open %s: %s\n", argv[0], filename, strerror(errno));
        return 1;
    }

    char line[65536];
    int rc = 0;
    size_t line_number = 0;
    while (fgets(line, sizeof(line), batch)) {
        ++line_number;
        if (!strchr(line, '\n') && !feof(batch)) {
            fprintf(stderr, "%s:%zu: batch line is too long\n", filename,
                    line_number);
            rc = MAX(rc, 1);
            break;
        }

        batch_args_t args = {0};
        if (parse_batch_line(line, argv[0], &args)) {
            rc = MAX(rc, 1);
            free_batch_args(&args);
            continue;
        }
        if (args.argc > 1)
            rc = MAX(rc, run_dot_invocation(args.argc, args.argv));
        free_batch_args(&args);
    }

    if (ferror(batch)) {
        fprintf(stderr, "%s: error reading %s: %s\n", argv[0], filename,
                strerror(errno));
        rc = MAX(rc, 1);
    }

    fclose(batch);
    return rc;
}

int main(int argc, char **argv)
{
    const char *batch = batch_filename(argc, argv);
    if (batch)
        graphviz_exit(run_batch(argv, batch));

    graphviz_exit(run_dot_invocation(argc, argv));
}

/**
 * @dir .
 * @brief main rendering program for various layouts of graphs and output formats
 */

/**
 * @mainpage
 *
 * %Hierarchy:\n
 * -# Applications
 *   - @ref cmd/dot – main rendering application for various layouts of graphs and output formats
 *   - @ref cmd – directory of applications
 *   - @ref dot.demo "dot.demo" – demo programs
 * -# @ref plugin – Plugins of graph layout engines and output formats
 * -# Core libraries
 *   -# @ref engines
 *     -# @ref common_render – rendering for layout engines
 *     -# @ref common_utils – low level utilities for layout engines
 *   -# @ref lib – miscellaneous libraries
 *   -# @ref cgraph – abstract graph C library
 *     -# @ref cgraph_app – uncoupled application specific functions
 *     -# @ref cgraph_core – highly cohesive core
 * -# Low level utility libraries
 *   - @ref cgraph_utils – low level cgraph utilities
 *   - @ref lib/cdt – Container Data Types library
 */
