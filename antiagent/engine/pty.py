"""Cross-platform PTY and terminal transport primitives for AntiAgent.

Provides reusable abstractions for:
- PTY allocation and process spawning
- Window size synchronization (SIGWINCH)
- Safe raw terminal context management with guaranteed restoration
- Non-blocking I/O helpers
"""

import contextlib
import os
import signal
import subprocess
import sys
from typing import Dict, Iterator, List, Optional, Sequence, Tuple


def is_pty_supported() -> bool:
    """Returns True if the current platform and environment support POSIX PTYs."""
    if sys.platform == "win32":
        return False
    try:
        import pty
        import termios
        import tty

        return True
    except ImportError:
        return False


def open_pty() -> Tuple[int, int]:
    """Allocates a master/slave pseudo-terminal pair.

    Raises:
        RuntimeError: if PTY is unsupported on this system or allocation fails.
    """
    if not is_pty_supported():
        raise RuntimeError("PTY is not supported on this platform.")
    import pty

    return pty.openpty()


def sync_window_size(source_fd: int, target_fds: Sequence[int]) -> None:
    """Synchronizes terminal window size (lines, cols) from source_fd to target_fds."""
    if sys.platform == "win32":
        return
    try:
        import fcntl
        import termios

        buf = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, b"\x00" * 8)
        for target_fd in target_fds:
            try:
                fcntl.ioctl(target_fd, termios.TIOCSWINSZ, buf)
            except OSError:
                pass
    except Exception:
        pass


@contextlib.contextmanager
def raw_terminal_context(fd: Optional[int] = None) -> Iterator[bool]:
    """Context manager setting raw terminal mode and guaranteeing attribute restoration.

    Yields True if raw mode was successfully enabled, False otherwise (e.g. non-TTY).
    """
    if fd is None:
        if hasattr(sys.stdin, "fileno"):
            try:
                fd = sys.stdin.fileno()
            except Exception:
                fd = None

    if fd is None or sys.platform == "win32":
        yield False
        return

    try:
        if not os.isatty(fd):
            yield False
            return
    except Exception:
        yield False
        return

    import termios
    import tty

    has_setraw = False
    old_mode = None
    try:
        old_mode = termios.tcgetattr(fd)
        tty.setraw(fd)
        has_setraw = True
        yield True
    except Exception:
        yield False
    finally:
        if has_setraw and old_mode is not None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_mode)
            except Exception:
                pass


def spawn_pty_process(
    argv: List[str],
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> Tuple[subprocess.Popen, int]:
    """Spawns a child process attached to a new PTY slave.

    Returns:
        (process, master_fd)
    Closes slave_fd in the parent process.
    """
    if not is_pty_supported():
        raise RuntimeError("PTY process spawning is not supported on this platform.")

    import pty

    master_fd, slave_fd = pty.openpty()

    try:
        proc = subprocess.Popen(
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
            cwd=cwd,
        )
    finally:
        # Parent process does not need the slave fd open
        try:
            os.close(slave_fd)
        except OSError:
            pass

    return proc, master_fd
