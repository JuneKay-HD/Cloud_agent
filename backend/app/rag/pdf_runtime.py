"""PDFium is process-global and must not be invoked concurrently by threads."""
from threading import RLock

pdfium_lock = RLock()
