import re

from bs4 import BeautifulSoup
from bs4 import Tag

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _is_flat_clean(body) -> bool:
    for node in body.descendants:
        if isinstance(node, Tag):
            if node.name == "br" or node.name in HEADING_TAGS:
                continue
            return False
    return True


def normalize_html(content: bytes) -> bytes:
    text = content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(text, "html.parser")
    body = soup.body
    if body is None:
        return content
    if not _is_flat_clean(body):
        return content

    for br in body.find_all("br"):
        br.replace_with("\n")

    parts = []
    for node in body.children:
        name = getattr(node, "name", None)
        if name in HEADING_TAGS:
            parts.append((name, node.get_text()))
        elif hasattr(node, "get_text"):
            parts.append((None, node.get_text()))
        else:
            parts.append((None, str(node)))

    body.clear()
    for tag, text_content in parts:
        if tag:
            element = soup.new_tag(tag)
            element.string = re.sub(r"\s+", " ", text_content).strip()
            body.append(element)
        else:
            for para in re.split(r"\n\s*\n+", text_content):
                para = re.sub(r"[ \t]+", " ", para).strip()
                if para:
                    element = soup.new_tag("p")
                    element.string = para
                    body.append(element)
    return str(soup).encode("utf-8")
