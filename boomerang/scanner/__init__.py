"""Application scanners."""

from .static import scan_static

def scan(app_dir, emit=None):
    """Run the static B2 pass; emit is reserved for C2 pipeline integration."""
    pam = scan_static(app_dir)
    if emit is not None and hasattr(emit, "emit"):
        emit.emit("scan", "Static scan completed", "success", components=len(pam.components))
    return pam

__all__ = ["scan", "scan_static"]
