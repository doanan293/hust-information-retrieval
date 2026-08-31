from __future__ import annotations

from scrapy.http import HtmlResponse, Response, TextResponse
from twisted.internet.defer import succeed


class StaticDownloadHandler:
    @classmethod
    def from_crawler(cls, crawler):
        return cls()

    def download_request(self, request, spider):
        url = request.url
        if url == "https://example.com/":
            body = (b"<main>Deterministic public fixture content long enough to avoid shell fallback.</main>"
                    b'<a href="/inside">inside</a><a href="/unknown">unknown</a>'
                    b'<a href="https://outside.test/leak">outside</a><img src="/image.png">')
            response = HtmlResponse(url, status=200, headers={b"Content-Type": b"text/html; charset=utf-8"}, body=body, encoding="utf-8", request=request)
        elif url == "https://example.com/robots.txt":
            response = TextResponse(url, status=200, headers={b"Content-Type": b"text/plain"}, body=b"Sitemap: https://example.com/sitemap.xml\n", encoding="utf-8", request=request)
        elif url == "https://example.com/sitemap.xml":
            response = TextResponse(url, status=200, headers={b"Content-Type": b"application/xml"}, body=b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/inside</loc></url></urlset>', encoding="utf-8", request=request)
        elif url == "https://example.com/inside":
            response = Response(url, status=302, headers={b"Location": b"https://outside.test/redirected"}, request=request)
        elif url == "https://example.com/same-redirect":
            response = Response(url, status=302, headers={b"Location": b"https://example.com/image.png"}, request=request)
        elif url == "https://example.com/image.png":
            response = Response(url, status=200, headers={b"Content-Type": b"image/png"}, body=b"\x89PNG\r\n\x1a\nfixture", request=request)
        else:
            response = HtmlResponse(url, status=200, headers={b"Content-Type": b"text/html"}, body=b"fixture page", encoding="utf-8", request=request)
        return succeed(response)

    def close(self):
        return succeed(None)
