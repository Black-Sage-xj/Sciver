"""Public full-text locations and article-body checks, without model/provider calls."""
from __future__ import annotations

from difflib import SequenceMatcher
import html
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlparse

from scholar_identity import arxiv_key, doi_key, title_key


def oa_locations(raw: dict, source: str) -> list[dict]:
    """Preserve all explicitly OA locations; a Crossref TDM link alone is not OA."""
    found: list[dict] = []

    def add(url, kind, provenance, license=""):
        if isinstance(url, str) and url.startswith("https://"):
            found.append({"url": url, "format": kind, "provenance": provenance,
                          "license": str(license or "")[:500]})

    if source == "openalex":
        best = raw.get("best_oa_location") or {}
        for location in [best, raw.get("primary_location") or {}, *(raw.get("locations") or [])]:
            if not isinstance(location, dict) or not (location.get("is_oa") or
                    location is best and best and "is_oa" not in best):
                continue
            add(location.get("pdf_url"), "pdf", "openalex:oa_location", location.get("license"))
            add(location.get("landing_page_url"), "html", "openalex:oa_location", location.get("license"))
        access = raw.get("open_access") or {}
        if access.get("is_oa"):
            add(access.get("oa_url"), "", "openalex:open_access")
    elif source == "semanticscholar":
        access = raw.get("openAccessPdf") or {}
        if isinstance(access, dict):
            add(access.get("url"), "pdf", "semanticscholar:openAccessPdf", access.get("license"))
    elif source == "crossref":
        licenses = [str(item.get("URL") or "") for item in raw.get("license") or [] if isinstance(item, dict)]
        # Publisher-specific TDM/rights pages do not establish public open access.
        license = next((url for url in licenses if re.match(
            r"https?://(?:www\.)?creativecommons\.org/(?:licenses|publicdomain)/", url, re.I)), "")
        if license:
            for link in raw.get("link") or []:
                if isinstance(link, dict):
                    add(link.get("URL"), "pdf" if "pdf" in str(link.get("content-type")) else "html",
                        "crossref:open_license_link", license)
    unique: dict[str, dict] = {}
    for location in found:
        unique.setdefault(location["url"], location)
    return list(unique.values())[:20]


def arxiv_from_locations(locations: list[dict]) -> str:
    return next((aid for item in locations if (aid := arxiv_key(item.get("url")))), "")


class ArticleHTML(HTMLParser):
    """Keep article containers separate from menus, abstracts and access walls."""
    _HIDDEN = {"script", "style", "noscript", "nav", "footer", "form", "aside", "svg"}
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, bool]] = []
        self.chunks: list[str] = []
        self.primary: list[str] = []
        self.body_chunks: list[str] = []
        self.body_paragraphs = 0
        self.after_abstract = False
        self.after_references = False
        self.references_boundary_seen = False
        self.headings: list[str] = []
        self.heading_depth = 0
        self.metadata: dict[str, list[str]] = {}
        self.alternatives: list[str] = []
        self.paragraphs = 0
        self.primary_paragraphs = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        hidden = any(item[1] for item in self.stack) or tag in self._HIDDEN or "hidden" in attrs
        classes = str(attrs.get("class") or "") + " " + str(attrs.get("id") or "")
        auxiliary = any(item[3] for item in self.stack) or bool(re.search(
            r"(?:^|[\s_-])(?:abstract|bibliography|references|refs|ref-list)(?:$|[\s_-])", classes, re.I))
        primary = any(item[2] for item in self.stack) or tag in {"article", "main"} or bool(re.search(
            r"(?:^|[\s_-])(?:article[-_]?(?:body|content)|ltx_document|full[-_]?text|jats-body)(?:$|[\s_-])", classes, re.I))
        if tag not in self._VOID:
            self.stack.append((tag, hidden, primary, auxiliary))
        if tag == "meta":
            name = str(attrs.get("name") or attrs.get("property") or "").lower()
            content = attrs.get("content")
            if name and content:
                self.metadata.setdefault(name, []).append(content)
                if name in {"citation_pdf_url", "citation_fulltext_html_url"}:
                    self.alternatives.append(content)
        if tag == "link" and str(attrs.get("type") or "").lower() in {"application/pdf", "text/xml", "application/xml"}:
            if attrs.get("href"):
                self.alternatives.append(attrs["href"])
        if tag == "a" and attrs.get("href") and str(attrs.get("href")).lower().split("?")[0].endswith(".pdf"):
            label = str(attrs.get("aria-label") or attrs.get("title") or "").lower()
            if "download" in label or "full" in label:
                self.alternatives.append(attrs["href"])
        if not hidden and (tag in {"p", "h1", "h2", "h3", "h4", "li", "section", "div", "br"}):
            self.chunks.append("\n")
            if primary:
                self.primary.append("\n")
        if not hidden and tag == "p":
            self.paragraphs += 1
            self.primary_paragraphs += int(primary)
            self.body_paragraphs += int(primary and not auxiliary and not self.after_abstract and not self.references_boundary_seen)
        if not hidden and tag in {"h1", "h2", "h3", "h4"}:
            self.heading_depth = len(self.stack)
            self.headings.append("")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "h1" and self.heading_depth and self.headings and any(item[2] for item in self.stack):
            self.metadata.setdefault("article_heading_title", []).append(self.headings[-1])
        if self.heading_depth and len(self.stack) <= self.heading_depth:
            heading = title_key(self.headings[-1]) if self.headings else ""
            self.after_abstract = bool(re.fullmatch(r"(?:\d+ )?abstract", heading))
            self.after_references = bool(re.fullmatch(r"(?:\d+ )?(?:references|bibliography|literature cited)", heading))
            self.references_boundary_seen = self.references_boundary_seen or self.after_references
            self.heading_depth = 0
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position][0] == tag:
                del self.stack[position:]
                break

    def handle_data(self, data):
        if any(item[1] for item in self.stack):
            return
        self.chunks.append(data)
        if any(item[2] for item in self.stack):
            self.primary.append(data)
            if not any(item[3] for item in self.stack) and not self.after_abstract and not self.references_boundary_seen:
                self.body_chunks.append(data)
        if self.heading_depth and self.headings:
            self.headings[-1] += data

    def text(self) -> str:
        primary = _clean(" ".join(self.primary))
        return primary if len(primary) >= 600 else _clean(" ".join(self.chunks))


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", html.unescape(text)).strip()


def title_matches(expected: str, actual: str) -> bool:
    left, right = title_key(expected), title_key(actual)
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) >= 16 and min(len(left.split()), len(right.split())) >= 3:
        if (left in right or right in left) and min(len(left), len(right)) / max(len(left), len(right)) >= .65:
            return True
    a, b = set(left.split()), set(right.split())
    return len(a & b) / max(1, len(a | b)) >= .65 or SequenceMatcher(None, left, right).ratio() >= .82


def validate_identity(paper: dict, text: str, metadata: dict[str, list[str]] | None = None) -> str:
    metadata = metadata or {}
    expected_doi = doi_key(paper.get("doi"))
    declared = {doi_key(item) for name in ("citation_doi", "dc.identifier", "dc.identifier.doi", "dc.doi", "prism.doi")
                for item in metadata.get(name, [])} - {""}
    if expected_doi and declared and expected_doi not in declared:
        raise ValueError("article identity conflicts with requested DOI")
    expected_title = str(paper.get("title") or "")
    titles = [item for name in ("citation_title", "dc.title", "dcterms.title") for item in metadata.get(name, [])]
    if not titles:
        titles = metadata.get("article_heading_title", [])
    if titles and expected_title and not any(title_matches(expected_title, title) for title in titles):
        raise ValueError("article identity conflicts with requested title")
    if expected_doi and expected_doi in declared:
        return "exact_article_doi"
    if titles and expected_title and len(title_key(expected_title).split()) >= 2:
        return "article_title"
    leading = title_key(text[:2000])
    words = set(title_key(expected_title).split())
    if expected_title and len(words) >= 2 and title_key(expected_title) in leading:
        return "title_in_source_text"
    text_dois = {doi_key(item) for item in re.findall(
        r"(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)(10\.\d{1,9}/[^\s<>]+)", text[:2000], re.I)}
    if expected_doi and expected_doi in text_dois:
        return "exact_doi_in_source_text"
    if paper:
        raise ValueError("article text does not establish the requested paper identity")
    return "unspecified_source_identity"


def article_body(parser: ArticleHTML, paper: dict) -> tuple[str, str]:
    text = parser.text()
    identity = validate_identity(paper, text, parser.metadata)
    headings = "\n".join(parser.headings)
    body_sections = re.findall(
        r"\b(?:introduction|background|methods?|methodology|materials|results?|discussion|conclusions?|theory|model|experiments?|simulation|analysis|references|acknowledg\w*)\b",
        headings, re.I)
    # A title, an abstract, references and menus on a publisher landing page are
    # not a body. Require substantive text plus several scientific sections.
    distinct = {section.lower() for section in body_sections}
    substantive = distinct - {"references", "acknowledgments", "acknowledgements", "background"}
    named_sections = [heading for heading in parser.headings if len(title_key(heading).split()) >= 2
                      and not title_matches(str(paper.get("title") or ""), heading)
                      and not re.fullmatch(r"(?:abstract|references|bibliography|acknowledg\w*)", heading.strip(), re.I)]
    body_size = len(" ".join(parser.body_chunks))
    custom_sections = body_size >= 4500 and parser.body_paragraphs >= 8 and len(named_sections) >= 2
    unsectioned_article = body_size >= 8000 and parser.body_paragraphs >= 8 and bool(
        parser.metadata.get("article_heading_title")) and "references" in distinct
    if len(text) < 1200 or parser.paragraphs < 4 or not (len(distinct) >= 2 and substantive or custom_sections or unsectioned_article):
        raise ValueError("page has no substantial readable article body (landing page or abstract only)")
    if re.search(r"(?:access denied|verify you are human|checking your browser|captcha|enable javascript)", text[:900], re.I) and len(text) < 4000:
        raise ValueError("source returned an access wall or browser challenge")
    return text, identity


def alternative_urls(parser: ArticleHTML, source_url: str) -> list[str]:
    urls = []
    for value in parser.alternatives[:20]:
        url = urljoin(source_url, value)
        if urlparse(url).scheme == "https" and url != source_url:
            urls.append(url)
    return list(dict.fromkeys(urls))[:4]
