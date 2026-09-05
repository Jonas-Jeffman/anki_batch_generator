from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, Iterator, List, Optional, Union


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class Element:
    tag: str
    attrs: Dict[str, str] = field(default_factory=dict)
    parent: Optional["Element"] = None
    children: List[Union["Element", str]] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def has_classes(self, *names: str) -> bool:
        return all(name in self.classes for name in names)

    def descendants(self, tag: str = "") -> Iterator["Element"]:
        for child in self.children:
            if not isinstance(child, Element):
                continue
            if not tag or child.tag == tag:
                yield child
            yield from child.descendants(tag)

    def find_first(self, tag: str = "", *classes: str) -> Optional["Element"]:
        for node in self.descendants(tag):
            if node.has_classes(*classes):
                return node
        return None

    def text_content(self) -> str:
        parts: List[str] = []
        for child in self.children:
            parts.append(child.text_content() if isinstance(child, Element) else child)
        return "".join(parts)

    def following_element_siblings(self) -> Iterator["Element"]:
        if not self.parent:
            return
        found = False
        for child in self.parent.children:
            if child is self:
                found = True
                continue
            if found and isinstance(child, Element):
                yield child

    def has_ancestor_class(self, class_name: str) -> bool:
        node = self.parent
        while node:
            if class_name in node.classes:
                return True
            node = node.parent
        return False


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = Element(
            tag=tag.lower(),
            attrs={key.lower(): value or "" for key, value in attrs},
            parent=self.stack[-1],
        )
        self.stack[-1].children.append(node)
        if node.tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_html_document(body: str) -> Element:
    parser = _TreeParser()
    parser.feed(body or "")
    parser.close()
    return parser.root
