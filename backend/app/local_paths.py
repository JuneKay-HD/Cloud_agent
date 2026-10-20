"""Keep long local artifact names readable on Windows without changing names."""
import os
from pathlib import Path


def readable_path(path: Path) -> Path:
    absolute=str(path.resolve())
    if os.name=='nt' and len(absolute)>=240 and not absolute.startswith('\\\\?\\'):
        return Path('\\\\?\\UNC\\'+absolute[2:]) if absolute.startswith('\\\\') else Path('\\\\?\\'+absolute)
    return path
