"""A strict parser for the small YAML subset this repository's CI files use.

The standard library has no YAML parser, and neither the tool nor its tests take
a dependency. Rather than skip validation, the hook manifest, the workflows and
the documented snippets are kept inside a plain subset that this module can
parse: block mappings and sequences, plain or quoted scalars, inline lists of
scalars, `|` literal blocks and comments. Anything else (tabs, anchors, tags,
flow mappings, folded blocks, duplicate keys, unquoted values that YAML would
silently turn into floats or booleans) raises ValueError.
"""
import json
import re

KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:[ ]+(.*))?$")
AMBIGUOUS = {"yes", "no", "on", "off", "y", "n"}


class YamlError(ValueError):
    pass


def strip_comment(content):
    quote = None
    for index, char in enumerate(content):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or content[index - 1] in " \t"):
            return content[:index].rstrip()
    return content.rstrip()


def split_flow(body, lineno):
    items, current, quote = [], "", None
    for char in body:
        if quote:
            current += char
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current += char
        elif char == ",":
            items.append(current.strip())
            current = ""
        elif char in "[]{}":
            raise YamlError("line %d: nested flow collections are outside the subset" % lineno)
        else:
            current += char
    if current.strip():
        items.append(current.strip())
    if quote or "" in items:
        raise YamlError("line %d: malformed inline list" % lineno)
    return items


def scalar(value, lineno, in_flow=False):
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            raise YamlError("line %d: unterminated double-quoted scalar" % lineno)
        try:
            return json.loads(value)
        except ValueError:
            raise YamlError("line %d: unsupported escape in double-quoted scalar" % lineno)
    if value.startswith("'"):
        inner = value[1:-1]
        if len(value) < 2 or not value.endswith("'") or "'" in inner.replace("''", ""):
            raise YamlError("line %d: unterminated single-quoted scalar" % lineno)
        return inner.replace("''", "'")
    if value.startswith("["):
        if in_flow or not value.endswith("]"):
            raise YamlError("line %d: malformed inline list" % lineno)
        return [scalar(item, lineno, in_flow=True) for item in split_flow(value[1:-1], lineno)]
    if value[0] in "{&*!|>%@`" or value in ("-", "?") or value.startswith(("- ", "? ")):
        raise YamlError("line %d: %r is outside the supported YAML subset" % (lineno, value))
    if ": " in value or value.endswith(":"):
        raise YamlError("line %d: a plain scalar must not contain ': '; quote it" % lineno)
    if value in ("true", "false"):
        return value == "true"
    if value in ("null", "~"):
        return None
    if re.match(r"^-?\d+$", value):
        return int(value)
    if re.match(r"^[-+]?(\d[\d_]*)?\.\d*([eE][-+]?\d+)?$", value) or value.lower() in AMBIGUOUS:
        raise YamlError("line %d: %r would not load as a string; quote it" % (lineno, value))
    return value


class Parser:
    def __init__(self, text):
        if "\t" in text:
            raise YamlError("tabs are not allowed")
        self.lines = text.split("\n")
        self.index = 0

    def peek(self):
        """(indent, content without comment) of the next meaningful line, or None at the end."""
        while self.index < len(self.lines):
            raw = self.lines[self.index]
            content = strip_comment(raw.strip())
            if content:
                if content in ("---", "...") or content.startswith("--- "):
                    raise YamlError("line %d: document markers are outside the subset" % (self.index + 1))
                return len(raw) - len(raw.lstrip(" ")), content
            self.index += 1
        return None

    def block(self, indent):
        content = self.peek()[1]
        return self.sequence(indent) if content.startswith("- ") else self.mapping(indent)

    def sequence(self, indent):
        items = []
        while True:
            head = self.peek()
            if head is None or head[0] < indent or not head[1].startswith("- "):
                break
            lineno = self.index + 1
            if head[0] != indent:
                raise YamlError("line %d: inconsistent indentation" % lineno)
            rest = head[1][2:].lstrip(" ")
            if KEY_RE.match(rest):
                # "- key: value" starts a mapping whose keys sit where `key` does.
                column = indent + len(head[1]) - len(rest)
                self.lines[self.index] = " " * column + rest
                items.append(self.mapping(column))
            else:
                self.index += 1
                items.append(scalar(rest, lineno))
        return items

    def mapping(self, indent):
        result = {}
        while True:
            head = self.peek()
            if head is None or head[0] < indent:
                break
            lineno = self.index + 1
            match = KEY_RE.match(head[1])
            if head[0] != indent or not match:
                raise YamlError("line %d: expected `key: value` at column %d" % (lineno, indent))
            key, value = match.group(1), (match.group(2) or "").strip()
            if key in result:
                raise YamlError("line %d: duplicate key %r" % (lineno, key))
            self.index += 1
            if value in ("|", "|-"):
                result[key] = self.literal(indent, value == "|")
            elif value:
                result[key] = scalar(value, lineno)
            else:
                result[key] = self.nested(indent)
        return result

    def nested(self, parent):
        head = self.peek()
        if head is None or head[0] < parent:
            return None
        if head[0] == parent:
            # YAML lets a sequence sit at the same column as the key that owns it.
            return self.sequence(parent) if head[1].startswith("- ") else None
        return self.block(head[0])

    def literal(self, parent, keep_newline):
        lines = []
        width = None
        while self.index < len(self.lines):
            raw = self.lines[self.index]
            if raw.strip():
                indent = len(raw) - len(raw.lstrip(" "))
                if width is None:
                    width = indent
                if indent <= parent or indent < width:
                    break
            lines.append(raw)
            self.index += 1
        while lines and not lines[-1].strip():
            lines.pop()
        if width is None or width <= parent:
            raise YamlError("empty literal block")
        text = "\n".join(line[width:] for line in lines)
        return text + "\n" if keep_newline else text


def load(text):
    """Parse `text`; raise YamlError when it is not in the supported subset."""
    parser = Parser(text)
    head = parser.peek()
    if head is None:
        return None
    if head[0] != 0:
        raise YamlError("the document must start at column 0")
    document = parser.block(0)
    if parser.peek() is not None:
        raise YamlError("line %d: unexpected content" % (parser.index + 1))
    return document
