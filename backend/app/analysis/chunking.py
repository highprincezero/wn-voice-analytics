def chunk_text(text: str, size: int) -> list[str]:
    if size < 1:
        raise ValueError("chunk size must be positive")
    cleaned = text.strip()
    if len(cleaned) <= size:
        return [cleaned] if cleaned else []
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + size)
        if end < len(cleaned):
            split = cleaned.rfind(" ", start, end)
            if split > start + size // 2:
                end = split
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end <= start:
            end = start + size
        start = end
    return chunks
