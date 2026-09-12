import gzip
from urllib.parse import urlsplit

from scrapy.http import Request, Response, TextResponse

ASSETS = {
    "/assets/a.png": ("image/png", b"png-fixture"),
    "/assets/a.mp3": ("audio/mpeg", b"mp3-fixture"),
    "/assets/a.mp4": ("video/mp4", b"mp4-fixture"),
    "/assets/a.pdf": ("application/pdf", b"pdf-fixture"),
    "/assets/a.docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        b"docx-fixture",
    ),
}


class StaticDownloadHandler:
    """Serve deterministic in-memory pages without opening a network socket."""

    lazy = True

    def __init__(self, settings, crawler=None):
        self.attempts: dict[str, int] = {}
        self.request_log = settings.get("FIXTURE_REQUEST_LOG")
        self.fixture_mode = settings.get("FIXTURE_MODE", "complete")

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings, crawler)

    async def close(self):
        return None

    async def download_request(self, request: Request):
        self.attempts[request.url] = self.attempts.get(request.url, 0) + 1
        if self.request_log:
            with open(self.request_log, "a", encoding="utf-8") as f:
                f.write(f"{request.url}\n")
        url = request.url

        # generated_graph mode
        if self.fixture_mode == "generated_graph":
            if url == "https://a.test/robots.txt":
                return TextResponse(
                    url,
                    status=200,
                    body=b"User-agent: *\nDisallow:\n",
                    encoding="utf-8",
                    headers={b"Content-Type": b"text/plain"},
                    request=request,
                )
            if "sitemap" in url:
                return Response(url, status=404, body=b"Not found", request=request)
            if url in {"https://a.test/", "https://a.test"}:
                body = b'<main><h1>Home</h1><a href="/news?page=1">News</a></main>'
                return TextResponse(
                    url,
                    status=200,
                    body=body,
                    encoding="utf-8",
                    headers={b"Content-Type": b"text/html"},
                    request=request,
                )
            if url == "https://a.test/news?page=1":
                pagination_links = "".join(f'<a href="/news?page={i}">{i}</a>' for i in range(2, 21))
                body = (
                    f'<main><h1>News 1</h1><p>Listing body page 1</p>'
                    f'<a href="/article/123">Article 123</a>'
                    f'{pagination_links}'
                    f'<a href="/cycle-a">Cycle A</a>'
                    f'<a href="/archive/2026/09">Archive</a>'
                    f'<a href="/filter?cat=1&amp;sort=asc">Filter</a>'
                    f'<a href="/news?page=1">Self</a>'
                    f'</main>'
                ).encode("utf-8")
                return TextResponse(
                    url,
                    status=200,
                    body=body,
                    encoding="utf-8",
                    headers={b"Content-Type": b"text/html"},
                    request=request,
                )
            if url == "https://a.test/news?page=2":
                pagination_links = "".join(f'<a href="/news?page={i}">{i}</a>' for i in range(3, 21))
                body = (
                    f'<main><h1>News 2</h1><p>Listing body page 2</p>'
                    f'<a href="/article/124">Article 124</a>'
                    f'<a href="/article/125">Article 125</a>'
                    f'{pagination_links}'
                    f'</main>'
                ).encode("utf-8")
                return TextResponse(
                    url,
                    status=200,
                    body=body,
                    encoding="utf-8",
                    headers={b"Content-Type": b"text/html"},
                    request=request,
                )
            if url == "https://a.test/news?page=3":
                pagination_links = "".join(f'<a href="/news?page={i}">{i}</a>' for i in range(4, 21))
                body = (
                    f'<main><h1>News 3</h1><p>Listing body page 3</p>'
                    f'<a href="/article/124">Article 124</a>'
                    f'<a href="/article/125">Article 125</a>'
                    f'{pagination_links}'
                    f'</main>'
                ).encode("utf-8")
                return TextResponse(
                    url,
                    status=200,
                    body=body,
                    encoding="utf-8",
                    headers={b"Content-Type": b"text/html"},
                    request=request,
                )
            if url == "https://a.test/article/123":
                return TextResponse(url, status=200, body=b'<main><h1>Article 123</h1><p>Body 123</p></main>', encoding="utf-8", headers={b"Content-Type": b"text/html"}, request=request)
            if url == "https://a.test/article/124":
                return TextResponse(url, status=200, body=b'<main><h1>Article 124</h1><p>Body 124</p></main>', encoding="utf-8", headers={b"Content-Type": b"text/html"}, request=request)
            if url == "https://a.test/article/125":
                return TextResponse(url, status=200, body=b'<main><h1>Article 125</h1><p>Body 125</p></main>', encoding="utf-8", headers={b"Content-Type": b"text/html"}, request=request)
            if url == "https://a.test/cycle-a":
                return TextResponse(url, status=200, body=b'<main><h1>Cycle A</h1><p>Body A</p><a href="/cycle-b">Cycle B</a></main>', encoding="utf-8", headers={b"Content-Type": b"text/html"}, request=request)
            if url == "https://a.test/cycle-b":
                return TextResponse(url, status=200, body=b'<main><h1>Cycle B</h1><p>Body B</p><a href="/cycle-a">Cycle A</a></main>', encoding="utf-8", headers={b"Content-Type": b"text/html"}, request=request)
            return Response(url, status=404, body=b"", request=request)

        # Observable semantic assets
        if url == "https://cdn.test/redirect-asset":
            return Response(
                url,
                status=302,
                headers={b"Location": b"https://store.test/assets/a.png"},
                request=request,
            )
        if url == "https://cdn.test/private-redirect":
            return Response(
                url,
                status=302,
                headers={b"Location": b"http://127.0.0.1/assets/a.png"},
                request=request,
            )
        if url == "https://cdn.test/frame.html":
            return TextResponse(
                url,
                status=200,
                body=b"<main><h1>Frame</h1></main>",
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        parsed_path = urlsplit(url).path
        if parsed_path in ASSETS:
            mime, asset_body = ASSETS[parsed_path]
            return Response(
                url,
                status=200,
                body=asset_body,
                headers={b"Content-Type": mime.encode("latin1")},
                request=request,
            )
        if url in {"https://a.test/robots.txt", "https://no-map.test/robots.txt"}:
            if self.fixture_mode == "robots_redirect_disallow" and url.startswith("https://a.test/"):
                return Response(
                    url,
                    status=301,
                    headers={b"Location": b"/robots-public.txt"},
                    request=request,
                )
            disallow = b"/about" if self.fixture_mode == "robots_disallow" and url.startswith("https://a.test/") else b""
            return TextResponse(
                url,
                status=200,
                body=b"User-agent: *\nDisallow: " + disallow,
                encoding="utf-8",
                headers={b"Content-Type": b"text/plain"},
                request=request,
            )
        if url == "https://a.test/robots-public.txt":
            return TextResponse(
                url,
                status=200,
                body=b"User-agent: *\nDisallow: /about",
                encoding="utf-8",
                headers={b"Content-Type": b"text/plain"},
                request=request,
            )
        if url == "https://map.test/robots.txt":
            return TextResponse(
                url,
                status=200,
                body=b"User-agent: *\nDisallow:\nSitemap: https://map.test/sitemap.xml\n",
                encoding="utf-8",
                headers={b"Content-Type": b"text/plain"},
                request=request,
            )
        if url in {"https://html-only.test/robots.txt", "https://cycle.test/robots.txt"}:
            return TextResponse(
                url,
                status=200,
                body=b"User-agent: *\nDisallow:\n",
                encoding="utf-8",
                headers={b"Content-Type": b"text/plain"},
                request=request,
            )

        # a.test sitemaps
        if url == "https://a.test/sitemap.xml":
            index_xml = (
                b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<sitemap><loc>https://a.test/sitemaps/posts</loc></sitemap>'
                b'<sitemap><loc>https://a.test/sitemaps/pages.xml.gz</loc></sitemap>'
                b'<sitemap><loc>https://a.test/sitemaps/broken.xml</loc></sitemap>'
                b'</sitemapindex>'
            )
            return TextResponse(
                url,
                status=200,
                body=index_xml,
                encoding="utf-8",
                headers={b"Content-Type": b"application/xml"},
                request=request,
            )
        if url == "https://a.test/sitemaps/posts":
            posts_xml = (
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<url><loc>https://a.test/article</loc></url>'
                b'<url><loc>https://a.test/article2</loc></url>'
                b'<url><loc>https://a.test/article3</loc></url>'
                b'</urlset>'
            )
            return TextResponse(
                url,
                status=200,
                body=posts_xml,
                encoding="utf-8",
                headers={b"Content-Type": b"application/xml"},
                request=request,
            )
        if url == "https://a.test/sitemaps/pages.xml.gz":
            pages_xml = (
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<url><loc>https://a.test/about</loc></url>'
                b'</urlset>'
            )
            return Response(
                url,
                status=200,
                body=gzip.compress(pages_xml),
                headers={b"Content-Type": b"application/gzip"},
                request=request,
            )
        if url == "https://a.test/sitemaps/broken.xml":
            return Response(url, status=404, body=b"Not found", request=request)
        if url in {"https://a.test/sitemap_index.xml", "https://a.test/sitemap-index.xml"}:
            return Response(url, status=404, body=b"Not found", request=request)

        # no-map.test sitemaps (all 404)
        if "no-map.test" in url and "sitemap" in url:
            return Response(url, status=404, body=b"Not found", request=request)
        if url == "https://no-map.test/":
            body = b'<main><h1>No Map</h1><a href="/page1">Page 1</a></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://no-map.test/page1":
            body = b'<main><h1>Page 1</h1></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/":
            body = (
                b'<main><h1>A home</h1>'
                b'<a href="/article">Article</a>'
                b'<a href="/report.pdf">Report</a>'
                b'<a href="https://b.test/news">B news</a>'
                b'<img src="/dynamic-image?id=1">'
                b'</main>'
            )
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://b.test/one":
            body = b'<main><h1>B one</h1><a href="/two">Two</a></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://b.test/two":
            body = b'<main><h1>B two</h1></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://b.test/":
            body = b'<main><h1>B home</h1><a href="/news">News</a></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://b.test/explicit":
            body = b'<main><h1>Explicit page</h1><p>Content</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/article":
            if self.fixture_mode == "faithful_article":
                body = (
                    b"<html><head><title>Generic Site Title</title></head>"
                    b"<body>"
                    b'<header><img src="https://cdn.external.test/banner.jpg"></header>'
                    b"<main>"
                    b"<h1>Article heading</h1>"
                    b'<p onclick="bad()">First paragraph. <a href="/report.pdf">Download PDF</a> <a href="/spec.docx">Spec</a> <a href="/outside-sitemap">Outside link</a> <a href="https://sub.a.test/out">Sub link</a> <script>alert(1)</script></p>'
                    b"<h2>Section</h2>"
                    b"<ul><li>Item 1</li><li>Item 2<ul><li>Nested</li></ul></li></ul>"
                    b"<blockquote>A famous quote</blockquote>"
                    b"<table><thead><tr><th>H1</th><th>H2</th></tr></thead><tbody><tr><td>D1</td><td>D2</td></tr></tbody></table>"
                    b'<figure><img src="/photo.jpg" alt="Photo"><figcaption>Photo caption</figcaption></figure>'
                    b'<img src="https://cdn.test/assets/a.png">'
                    b'<a href="https://cdn.test/frame.html">Frame</a>'
                    b'<img src="https://cdn.test/private-redirect">'
                    b'<audio src="/assets/a.mp3"></audio>'
                    b"</main></body></html>"
                )
            else:
                body = (
                    b'<main><h1>Article</h1><p>Body</p>'
                    b'<a href="/report.pdf">Download PDF</a>'
                    b'<a href="/spec.docx">Spec</a>'
                    b'<a href="/outside-sitemap">Outside</a>'
                    b'<a href="https://sub.a.test/out">Sub</a>'
                    b'<figure><img src="/photo.jpg" alt="Photo"><figcaption>Photo caption</figcaption></figure>'
                    b'<img src="https://cdn.test/assets/a.png">'
                    b'<a href="https://cdn.test/frame.html">Frame</a>'
                    b'<img src="https://cdn.test/private-redirect">'
                    b'<audio src="/assets/a.mp3"></audio>'
                    b'</main>'
                )
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/photo.jpg":
            return Response(
                url,
                status=200,
                body=b"\xff\xd8\xff\xe0jpeg-fixture-bytes",
                headers={b"Content-Type": b"image/jpeg"},
                request=request,
            )
        if url == "https://a.test/outside-sitemap":
            body = b'<main><h1>Outside</h1><p>Outside sitemap</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/article2":
            body = (
                b'<main><h1>Article 2</h1><p>Body</p>'
                b'<img src="https://cdn.test/assets/a.png">'
                b'</main>'
            )
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/article3":
            body = b'<main><h1>Article 3</h1><p>Body</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/about":
            body = b'<main><h1>About</h1><p>About page</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://b.test/news":
            body = b'<main><h1>News</h1><p>Story</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/report.pdf":
            return Response(
                url,
                status=200,
                body=b"%PDF-fixture",
                headers={b"Content-Type": b"application/pdf"},
                request=request,
            )
        if url == "https://a.test/spec.docx":
            return Response(
                url,
                status=200,
                body=b"PK\x03\x04docx-fixture-bytes",
                headers={b"Content-Type": b"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                request=request,
            )
        if url == "https://a.test/image":
            return Response(
                url,
                status=200,
                body=b"jpeg-bytes",
                headers={b"Content-Type": b"image/jpeg"},
                request=request,
            )
        if url == "https://a.test/dynamic-image?id=1":
            return Response(
                url,
                status=200,
                body=b"jpeg-fixture",
                headers={b"Content-Type": b"image/jpeg"},
                request=request,
            )
        if url == "https://a.test/captcha-page":
            body = b'<main><h1>Challenge</h1><div class="g-recaptcha"></div></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/login-page":
            body = b'<main><h1>Login</h1><form action="/login"><input type="password"></form></main>'
            return TextResponse(
                url,
                status=401,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://a.test/retry-page":
            attempt = self.attempts.get(url, 1)
            if attempt == 1:
                return Response(url, status=503, body=b"Service Unavailable", request=request)
            return TextResponse(
                url,
                status=200,
                body=b'<main><h1>Retry Success</h1><p>Recovered after 503</p></main>',
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )

        # map.test routes
        if url == "https://map.test/sitemap.xml":
            sitemap_xml = (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'<url><loc>https://map.test/from-sitemap</loc></url>'
                b'</urlset>'
            )
            return TextResponse(
                url,
                status=200,
                body=sitemap_xml,
                encoding="utf-8",
                headers={b"Content-Type": b"application/xml"},
                request=request,
            )
        if "map.test" in url and ("sitemap_index.xml" in url or "sitemap-index.xml" in url):
            return Response(url, status=404, body=b"Not found", request=request)
        if url == "https://map.test/":
            body = b'<main><h1>Map Home</h1><a href="/outside-sitemap">Outside</a></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://map.test/from-sitemap":
            body = b'<main><h1>From Sitemap</h1><p>Content from sitemap</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://map.test/outside-sitemap":
            body = b'<main><h1>Outside Sitemap</h1><p>Content outside sitemap</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )

        # html-only.test routes
        if "html-only.test" in url and "sitemap" in url:
            return Response(url, status=404, body=b"Not found", request=request)
        if url in {"https://html-only.test/", "https://html-only.test/html-only/"}:
            body = (
                b'<main><h1>HTML Only</h1>'
                b'<a href="/article">Article</a>'
                b'<a href="/report.pdf">Report</a>'
                b'<a href="/html-only/article">HTML Article</a>'
                b'<a href="/html-only/report.pdf">HTML Report</a>'
                b'<img src="/photo.jpg" alt="test">'
                b'<video src="/clip.mp4"></video>'
                b'</main>'
            )
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url in {"https://html-only.test/article", "https://html-only.test/html-only/article"}:
            body = b'<main><h1>HTML only</h1><p>Body</p></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url in {"https://html-only.test/report.pdf", "https://html-only.test/html-only/report.pdf"}:
            return Response(
                url,
                status=200,
                body=b"%PDF-1.4 report bytes",
                headers={b"Content-Type": b"application/pdf"},
                request=request,
            )
        if url == "https://html-only.test/photo.jpg":
            return Response(
                url,
                status=200,
                body=b"\xff\xd8\xff\xe0jpeg-bytes",
                headers={b"Content-Type": b"image/jpeg"},
                request=request,
            )
        if url == "https://html-only.test/clip.mp4":
            return Response(
                url,
                status=200,
                body=b"video-bytes",
                headers={b"Content-Type": b"video/mp4"},
                request=request,
            )

        # cycle.test routes
        if "cycle.test" in url and "sitemap" in url:
            return Response(url, status=404, body=b"Not found", request=request)
        if url == "https://cycle.test/":
            body = b'<main><h1>Cycle Home</h1><a href="/cycle/a">A</a></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://cycle.test/cycle/a":
            body = b'<main><h1>Cycle A</h1><a href="/cycle/b">B</a></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )
        if url == "https://cycle.test/cycle/b":
            body = b'<main><h1>Cycle B</h1><a href="/cycle/a">A</a></main>'
            return TextResponse(
                url,
                status=200,
                body=body,
                encoding="utf-8",
                headers={b"Content-Type": b"text/html"},
                request=request,
            )

        return Response(url, status=404, body=b"", request=request)
