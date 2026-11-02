import azure.functions as func
import logging
import os
from urllib.parse import parse_qs

# Must be set before Paddle/protobuf import inside handlers.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_use_onednn", "0")

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

OCR_ENGINES = {"tesseract", "paddle"}


@app.route(route="extract_text", methods=["POST"])
def ocr(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # multipart/form-data
        files = req.files
        form = req.form

        uploaded_file = files.get("file")
        engine = form.get("engine", "").strip().lower()
        raw_page_no = (form.get("page_no") or "1").strip() or "1"

        if uploaded_file is None:
            return func.HttpResponse(
                "Missing file",
                status_code=400
            )

        if engine not in OCR_ENGINES:
            return func.HttpResponse(
                "engine must be tesseract or paddle",
                status_code=400
            )

        try:
            page_no = int(raw_page_no)
        except ValueError:
            return func.HttpResponse(
                "page_no must be an integer",
                status_code=400
            )

        if page_no < 1:
            return func.HttpResponse(
                "page_no must be >= 1",
                status_code=400
            )

        data = uploaded_file.stream.read()

        if not data:
            return func.HttpResponse(
                "Empty file",
                status_code=400
            )

        layout_mode = engine

        if engine == "tesseract":
            from ocr import tesseract_extract_text_from_bytes

            text = tesseract_extract_text_from_bytes(data, page_no=page_no)
        else:
            from ocr import extract_text_with_paddle, _PADDLE_LAYOUT_MODE

            text = extract_text_with_paddle(
                data,
                uploaded_file.filename or "",
                page_no=page_no,
            )
            layout_mode = _PADDLE_LAYOUT_MODE

        return func.HttpResponse(
            text,
            mimetype="text/plain; charset=utf-8",
            status_code=200,
            headers={
                "X-OCR-Layout-Mode": layout_mode,
                "X-OCR-Page-No": str(page_no),
            },
        )

    except Exception as e:
        logging.exception("OCR failed")

        return func.HttpResponse(
            f"OCR failed: {type(e).__name__}: {e}",
            status_code=500
        )


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        "OCR API running",
        status_code=200
    )








"""Azure Functions app: EML to PDF conversion (Python v2 programming model)."""
import base64
import email
import html
import json
import re
from pathlib import Path

import azure.functions as func
from email import policy
from email.header import decode_header, make_header
from playwright.async_api import async_playwright


def _decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (email.errors.HeaderParseError, LookupError, TypeError, ValueError):
        return value


def _first_inner_rfc822(msg: email.message.Message) -> email.message.Message | None:
    for part in msg.walk():
        if part.get_content_type() != "message/rfc822":
            continue
        pl = part.get_payload()
        if isinstance(pl, list):
            for item in pl:
                if isinstance(item, email.message.Message):
                    return item
        elif isinstance(pl, email.message.Message):
            return pl
    return None


def _merged_field(
    root: email.message.Message,
    inner: email.message.Message | None,
    key: str,
) -> str:
    for m in (root, inner):
        if not m:
            continue
        v = m.get(key)
        if v:
            return _decode_mime_header(v)
    return ""


def _decode_part_bytes(part: email.message.Message, payload: bytes) -> str:
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _looks_like_html(s: str) -> bool:
    if not s or not s.strip():
        return False
    t = s.lstrip()[:8000].lower()
    if "<!doctype html" in t[:200] or t.startswith("<html"):
        return True
    if "<body" in t and "<" in t:
        return True
    if re.search(r"<\s*html[\s>]", t[:2000]):
        return True
    if re.search(r"<\s*head[\s>]", t[:2000]) and re.search(
        r"<\s*(div|table|style|meta)\b", t[:4000]
    ):
        return True
    return False


def parse_eml_bytes(eml_bytes: bytes) -> str:
    msg = email.message_from_bytes(eml_bytes, policy=policy.default)
    inner = _first_inner_rfc822(msg)
    html_content = None
    text_content = None
    cid_map = {}
    for part in msg.walk():
        content_type = part.get_content_type()
        content_id = part.get("Content-ID")
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if content_type == "text/html":
            decoded = _decode_part_bytes(part, payload)
            if decoded.strip():
                if not html_content or len(decoded) > len(html_content):
                    html_content = decoded
        elif content_type == "text/plain":
            decoded = _decode_part_bytes(part, payload)
            if decoded.strip():
                if not text_content or len(decoded) > len(text_content):
                    text_content = decoded
        elif content_id:
            cid = content_id.strip("<>")
            encoded = base64.b64encode(payload).decode()
            cid_map[cid] = f"data:{content_type};base64,{encoded}"
    if not html_content and text_content and _looks_like_html(text_content):
        html_content = text_content
    if not html_content:
        raw = text_content or "No content found"
        html_content = f"<pre>{html.escape(raw)}</pre>"
    for cid, data_uri in cid_map.items():
        html_content = html_content.replace(f"cid:{cid}", data_uri)
    from_h = html.escape(_merged_field(msg, inner, "From"))
    to_h = html.escape(_merged_field(msg, inner, "To"))
    subj_h = html.escape(_merged_field(msg, inner, "Subject"))
    date_h = html.escape(_merged_field(msg, inner, "Date"))
    header_html = f"""
    <p><b>From:</b> {from_h}</p>
    <p><b>To:</b> {to_h}</p>
    <p><b>Sent:</b> {date_h}</p>
    <p><b>Subject:</b> {subj_h}</p>
    <hr>
    """
    return f"""
    <html>
    <head>
        <meta http-equiv="Content-Security-Policy" content="{_EML_CSP}">
        <meta charset="utf-8">
        <style>
            body {{
                font-family: Arial, "Segoe UI Emoji", "Noto Color Emoji";
                font-size: 12px;
                word-wrap: break-word;
                overflow-wrap: anywhere;
                max-width: 100%;
            }}
            pre {{ white-space: pre-wrap; word-break: break-word; }}
            table {{ max-width: 100%; }}
        </style>
    </head>
    <body>
        {header_html}
        {html_content}
    </body>
    </html>
    """


def parse_eml(eml_path: str) -> str:
    with open(eml_path, "rb") as f:
        return parse_eml_bytes(f.read())


def attachment_pdf_name(filename: str | None) -> str:
    name = filename or "email.pdf"
    stem = Path(name).stem or "email"
    return f"{stem}.pdf"


# Layers that keep untrusted email HTML from making the server fetch anything:
# 1. CSP as the first element of <head>; email HTML comes later and cannot
#    loosen it.
# 2. JavaScript off, and page.route() aborts every non-data: request.
# 3. Chromium uses a dead proxy (port 9), also for loopback, so requests that
#    bypass page.route() (e.g. <link rel=prefetch>) go nowhere.
_EML_CSP = (
    "default-src 'none'; img-src data:; "
    "style-src 'unsafe-inline'; font-src data:"
)
_EML_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--proxy-server=http://127.0.0.1:9",
    "--proxy-bypass-list=<-loopback>",
]


async def _allow_only_data_urls(route) -> None:
    # Email HTML is untrusted: it must not make the server fetch anything
    # (remote trackers, or internal sites reachable from this host).
    if route.request.url.startswith("data:"):
        await route.continue_()
    else:
        await route.abort()


async def html_to_pdf_bytes(html: str) -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=_EML_BROWSER_ARGS
        )
        try:
            page = await browser.new_page(java_script_enabled=False)
            await page.route("**/*", _allow_only_data_urls)
            await page.set_content(html, wait_until="load")
            return await page.pdf(format="A4", print_background=True)
        finally:
            await browser.close()



"""
@app.route(route="", methods=["GET"])
def root(req: func.HttpRequest) -> func.HttpResponse:
    payload = {
        "status": "ok",
        "convert": "POST /convert multipart form-data field: file",
    }
    return func.HttpResponse(
        json.dumps(payload),
        mimetype="application/json",
    )

"""
@app.route(route="emltopdfconvert", methods=["POST"])
async def convert(req: func.HttpRequest) -> func.HttpResponse:
    try:
        upload = req.files["file"]
        eml_bytes = upload.read()
        html_str = parse_eml_bytes(eml_bytes)
        pdf_bytes = await html_to_pdf_bytes(html_str)
        out_name = attachment_pdf_name(upload.filename)
        return func.HttpResponse(
            body=pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )
    except Exception as e:
        logging.exception("EML to PDF failed")
        return func.HttpResponse(body=str(e), status_code=500)