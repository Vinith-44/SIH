/*
 * syscalls.c - newlib stubs.  Nothing on the node uses files or stdio (every
 * byte to the Pi goes through the UART task), but newlib's exit/stdio support
 * objects still reference these; defining them keeps the link warning-free.
 */
#include <errno.h>
#include <sys/stat.h>

int _close(int fd)
{
    (void)fd;
    return -1;
}

int _lseek(int fd, int ptr, int dir)
{
    (void)fd;
    (void)ptr;
    (void)dir;
    return 0;
}

int _read(int fd, char *ptr, int len)
{
    (void)fd;
    (void)ptr;
    (void)len;
    errno = ENOSYS;
    return -1;
}

int _write(int fd, char *ptr, int len)
{
    (void)fd;
    (void)ptr;
    return len;                 /* swallowed: there is no console */
}
