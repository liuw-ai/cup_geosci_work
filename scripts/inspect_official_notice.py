"""Read-only inspection of an already robots-checked official notice."""

from __future__ import annotations

import argparse
import json
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--term", action="append", default=[])
    args = parser.parse_args()

    response = requests.get(args.url, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    links = [
        {"label": anchor.get_text(" ", strip=True), "url": urljoin(response.url, anchor["href"])}
        for anchor in soup.find_all("a", href=True)
    ]
    contexts = {}
    for term in args.term:
        start = 0
        matches = []
        while len(matches) < 8:
            index = text.find(term, start)
            if index < 0:
                break
            matches.append(text[max(0, index - 160) : index + 220])
            start = index + len(term)
        contexts[term] = matches
    print(
        json.dumps(
            {"url": response.url, "status": response.status_code, "title": soup.title.get_text(" ", strip=True) if soup.title else "", "links": links, "contexts": contexts},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
