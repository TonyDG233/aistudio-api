/*
 * fstat64i32 CRT shim for Zig + SpiderMonkey static linking on Windows.
 *
 * SpiderMonkey's prebuilt Rust static library references fstat64i32, but the
 * MinGW CRT shipped with Zig 0.13.0 does not export it. Forward the call to the
 * real _fstat64i32 provided by the CRT.
 */
#include <sys/stat.h>
#include <errno.h>

#undef fstat64i32
int fstat64i32(int fd, struct _stat64i32 *buf) {
    return _fstat64i32(fd, buf);
}
