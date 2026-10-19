import ctypes
import gc
import logging
import sys

logger = logging.getLogger("grimmory_proxy.memory")


def release_memory() -> None:
    """
    Triggers Python garbage collection and instructs the C memory allocator
    (glibc malloc on Linux / Docker containers) to release unused heap memory
    back to the operating system via malloc_trim(0).
    """
    try:
        gc.collect()
    except Exception as e:
        logger.debug(f"gc.collect() error: {e}")

    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL("libc.so.6")
            libc.malloc_trim(0)
        except Exception as e:
            logger.debug(f"malloc_trim(0) error: {e}")
