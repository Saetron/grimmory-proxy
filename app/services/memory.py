import ctypes
import gc
import logging
import sys

logger = logging.getLogger("grimmory_proxy.memory")


def release_memory() -> None:
    """
    Triggers Python garbage collection, clears volatile in-memory image caches,
    and instructs the C memory allocator (glibc malloc on Linux / Docker containers)
    to release unused heap memory back to the operating system via malloc_trim(0).
    """
    try:
        from app.services.image_compressor import image_compressor
        image_compressor.clear_cache()
    except Exception as e:
        logger.debug(f"Error clearing image compression cache during memory release: {e}")

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
