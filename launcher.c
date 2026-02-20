/*
 * Plume native launcher.
 *
 * Compiled binary that embeds the Python interpreter so macOS TCC
 * attributes permissions (Microphone, Accessibility) to "Plume"
 * instead of "python3.13".
 *
 * Paths are baked in at compile time via -D flags.
 */

#include <Python.h>
#include <stdlib.h>
#include <stdio.h>

#ifndef SCRIPT_PATH
#error "SCRIPT_PATH must be defined at compile time"
#endif
#ifndef VENV_SITE_PACKAGES
#error "VENV_SITE_PACKAGES must be defined at compile time"
#endif
#ifndef VENV_PREFIX
#error "VENV_PREFIX must be defined at compile time"
#endif

#define _S(x) #x
#define S(x) _S(x)

int main(int argc, char *argv[]) {
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    setenv("PYTHONPATH", S(VENV_SITE_PACKAGES), 1);
    setenv("VIRTUAL_ENV", S(VENV_PREFIX), 1);

    char *py_argv[] = {"Plume", "-u", S(SCRIPT_PATH), NULL};
    return Py_BytesMain(3, py_argv);
}
