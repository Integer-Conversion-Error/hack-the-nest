/* Keep Mesa's hardcoded LLVM resources inside the extracted userspace tree. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static const char *redirect_path(const char *path, char *buffer)
{
    const char *root = getenv("GPU_PROBE_EXTRACT_ROOT");
    const char clc_prefix[] = "/usr/lib/clc/";
    const char clang_prefix[] = "/usr/lib/llvm-21/lib/clang/21/";
    if (!path) {
        errno = EFAULT;
        return NULL;
    }
    if (!root ||
        (strncmp(path, clc_prefix, sizeof(clc_prefix) - 1) &&
         strncmp(path, clang_prefix, sizeof(clang_prefix) - 1))) {
        return path;
    }
    int length = snprintf(buffer, PATH_MAX, "%s%s", root, path);
    if (length < 0 || length >= PATH_MAX) {
        errno = ENAMETOOLONG;
        return NULL;
    }
    return buffer;
}

/* Resolve per call, avoiding a shared lazy-initialization data race. */
#define RESOLVE(name, result_type, arguments, failure) \
    result_type (*original) arguments = dlsym(RTLD_NEXT, #name); \
    if (!original) { errno = ENOSYS; return failure; } \
    char buffer[PATH_MAX]; \
    const char *redirected = redirect_path(path, buffer); \
    if (!redirected) return failure

#define OPEN_WRAPPER(name) \
int name(const char *path, int flags, ...) \
{ \
    RESOLVE(name, int, (const char *, int, ...), -1); \
    mode_t mode = 0; \
    if ((flags & O_CREAT) || ((flags & O_TMPFILE) == O_TMPFILE)) { \
        va_list args; \
        va_start(args, flags); \
        mode = (mode_t)va_arg(args, int); \
        va_end(args); \
    } \
    return original(redirected, flags, mode); \
}
OPEN_WRAPPER(open)
OPEN_WRAPPER(open64)

#define FOPEN_WRAPPER(name) \
FILE *name(const char *path, const char *mode) \
{ \
    RESOLVE(name, FILE *, (const char *, const char *), NULL); \
    return original(redirected, mode); \
}
FOPEN_WRAPPER(fopen)
FOPEN_WRAPPER(fopen64)

#define STAT_WRAPPER(name, stat_type) \
int name(const char *path, stat_type *value) \
{ \
    RESOLVE(name, int, (const char *, stat_type *), -1); \
    return original(redirected, value); \
}
STAT_WRAPPER(stat, struct stat)
STAT_WRAPPER(stat64, struct stat64)
STAT_WRAPPER(lstat, struct stat)
STAT_WRAPPER(lstat64, struct stat64)

int access(const char *path, int mode)
{
    RESOLVE(access, int, (const char *, int), -1);
    return original(redirected, mode);
}
