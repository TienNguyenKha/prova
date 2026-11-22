from urllib.request import Request, urlopen


def read_url(url: str) -> str:
    """Read a web page and return its text content (truncated to 3000 chars)."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Atlas Agent)"})
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        extractor = _HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()

        if len(text) > 3000:
            text = text[:3000] + "\n\n[...truncated]"
        return text if text else "Could not extract text from this page."
    except Exception as e:
        return f"Failed to read URL: {e}"
