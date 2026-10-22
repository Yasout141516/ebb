"""Plain formatting of sizes, shared by the notes checks and the terminal (pure, no imports)."""


def format_megabytes(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MB"
