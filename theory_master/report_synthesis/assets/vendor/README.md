# Bundled browser dependencies

The graph works without fetching browser code or fonts from external services.
These files are the pinned distributions used by the existing graph component:

| Library | Version | Source | License file |
| --- | --- | --- | --- |
| AntV G6 | 5.1.1 | https://cdn.jsdelivr.net/npm/@antv/g6@5.1.1/dist/g6.min.js | g6-LICENSE.txt |
| marked | 15.0.12 | https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js | marked-LICENSE.txt |
| DOMPurify | 3.2.6 | https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js | dompurify-LICENSE.txt |
| KaTeX | 0.16.22 | https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/ | katex-LICENSE.txt |
| Prism | 1.30.0 | https://cdn.jsdelivr.net/npm/prismjs@1.30.0/ | prism-LICENSE.txt |

KaTeX CSS retains the original WOFF2 fonts. Legacy WOFF and TTF alternatives
were removed from the CSS to avoid distributing duplicate font formats. The
JavaScript distributions and WOFF2 fonts are unmodified.

`manifest.json` records the SHA-256 of every distributed asset and license.
