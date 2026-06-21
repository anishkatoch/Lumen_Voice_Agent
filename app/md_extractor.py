"""
Extract clean readable text from Markdown files using mistune's AST.
Tables are converted to natural language sentences.
"""
import mistune


def _render_node(node: dict) -> str:
    """Recursively extract text from a mistune AST node."""
    node_type = node.get("type", "")
    children = node.get("children", [])

    if node_type == "text":
        return node.get("raw", "")

    if node_type == "softline_break" or node_type == "linebreak":
        return " "

    if node_type == "codespan":
        return node.get("raw", "")

    if node_type == "table":
        return _render_table(node)

    if node_type == "list":
        items = []
        for child in children:
            items.append(_render_node(child))
        return ". ".join(items)

    # Recurse for paragraph, heading body, emphasis, strong, etc.
    return " ".join(_render_node(c) for c in children).strip()


def _render_table(node: dict) -> str:
    """Convert a markdown table to header:value sentences."""
    head = node.get("children", [{}])[0]  # table_head
    body_rows = node.get("children", [])[1:]  # table_body rows

    headers = []
    for cell in head.get("children", []):
        headers.append(_render_node(cell).strip())

    sentences = []
    for row in body_rows:
        parts = []
        for i, cell in enumerate(row.get("children", [])):
            header = headers[i] if i < len(headers) else f"Col{i}"
            value = _render_node(cell).strip()
            parts.append(f"{header}: {value}")
        sentences.append(". ".join(parts) + ".")

    return " ".join(sentences)


def extract_chunks(markdown_text: str) -> list[dict]:
    """
    Parse markdown and split into chunks by ## heading.
    Returns list of {section_title, chunk_text}.
    """
    md = mistune.create_markdown(renderer=None)  # AST renderer
    tokens = md(markdown_text)

    chunks: list[dict] = []
    current_title = "Introduction"
    current_parts: list[str] = []

    for token in tokens:
        token_type = token.get("type", "")

        if token_type == "heading":
            # Save current chunk before starting new one
            text = " ".join(current_parts).strip()
            if text:
                chunks.append({"section_title": current_title, "chunk_text": text})
            current_parts = []
            current_title = _render_node(token).strip()

        else:
            rendered = _render_node(token).strip()
            if rendered:
                current_parts.append(rendered)

    # Save last chunk
    text = " ".join(current_parts).strip()
    if text:
        chunks.append({"section_title": current_title, "chunk_text": text})

    return chunks
