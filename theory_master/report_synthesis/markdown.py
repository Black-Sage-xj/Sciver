"""Markdown lexical helpers shared by report readers."""
import re

# Protect fenced and inline code from headings, embeds and link transforms.
CODE = re.compile(r"(^ {0,3}(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}(?P=fence)[ \t]*$|\Z)|(?P<tick>`+)[^\n]*?(?P=tick))", re.M | re.S)
