# -*- coding: utf-8 -*-
"""Tiny pure-stdlib PDF writer — enough for stylish, graphic ebooks.

No reportlab, no pillow. A PDF is just a text format, so we emit objects only
what we need: vector fills (flat color bands, rounded panels), WinAnsi text
with word-wrap, footers and page numbers. `latin-1` output keeps bytes small
and deterministic for tests.
"""
_SAFE = {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
         "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u2192": "->",
         "\u00b7": ".", "\u2022": "*", "\u2605": "*", "\u2b50": "*"}


def _safe(t):
    out = []
    for ch in (t or "").replace("\r", " "):
        if ch in _SAFE:
            out.append(_SAFE[ch])
        elif ord(ch) < 256:
            out.append(ch)
        else:
            out.append("?")
    return "".join(out)


def _esc(s):
    return _safe(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _n(*nums):
    return " ".join("%0.2f" % n for n in nums)


def _wrap(text, width, size):
    """Simple word wrap (Helvetica avg char width ~0.52*size)."""
    words = _esc(text).split()
    max_chars = max(1, int(width / (0.52 * size)))
    lines, cur = [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if len(cand) <= max_chars:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


class Pdf:
    """One page at a time. cover(), heading(), paragraph(), bullets(),
    pullquote(), spacer() then save() -> PDF bytes."""

    def __init__(self, page_w=595, page_h=842, accent=(255, 107, 44), bg=(255, 253, 247)):
        self.w, self.h = page_w, page_h
        self.accent, self.bg = accent, bg
        self.margin_x = 56
        self.body_w = self.w - 2 * self.margin_x
        self.pages = []
        self._content = []
        self.x, self.y = self.margin_x, self.h - 96
        self.margin_y = 64

    # ------------------------------------------------------------ primitives
    def _c(self, s):
        self._content.append(s)

    def _rgb(self, c):
        return "%0.3f %0.3f %0.3f rg" % (c[0] / 255, c[1] / 255, c[2] / 255)

    def rect(self, x, y, w, h, rgb, r=0):
        path = self._round(x, y, w, h, r) if r else "%s re" % _n(x, y, w, h)
        self._c("q %s %s f Q" % (self._rgb(rgb), path))

    @staticmethod
    def _round(x, y, w, h, r):
        r = min(r, w / 2, h / 2)
        if r <= 0:
            return "%s re" % _n(x, y, w, h)
        k = 0.5523 * r
        parts = [
            "%s m" % _n(x + r, y),
            "%s l" % _n(x + w - r, y),
            "%s c" % _n(x + w - r + k, y, x + w, y + r - k, x + w, y + r),
            "%s l" % _n(x + w, y + h - r),
            "%s c" % _n(x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h),
            "%s l" % _n(x + r, y + h),
            "%s c" % _n(x + r - k, y + h, x, y + h - r + k, x, y + h - r),
            "%s l" % _n(x, y + r),
            "%s c" % _n(x, y + r - k, x + r - k, y, x + r, y),
        ]
        return " ".join(parts)

    def text(self, s, x, y, size, color, bold=False, align="left"):
        family = "/F2" if bold else "/F1"
        s = _esc(s)
        if align in ("right", "center"):
            width = len(s) * 0.52 * size
            x = self.w - self.margin_x - width if align == "right" else x - width / 2
        self._c("BT %s %d Tf %s %s Td (%s) Tj ET"
                % (family, size, self._rgb(color), _n(x, y), s))

    def ensure(self, need):
        if self.y - need < self.margin_y:
            self.new_page()

    def new_page(self):
        self.pages.append("\n".join(self._content))
        self._content = []
        self.y = self.h - 96

    # -------------------------------------------------------------- elements
    def _footer(self):
        self.text("pstore picks", self.margin_x, 40, 9, (150, 140, 155))
        self.text(str(len(self.pages) + 1), self.w - self.margin_x, 40, 9,
                  (150, 140, 155), align="right")

    def cover(self, title, subtitle, kicker=None):
        self.rect(0, 0, self.w, self.h, self.bg)
        self.rect(0, 0, self.w, 22, self.accent)
        self.rect(0, self.h - 22, self.w, 22, (238, 224, 255))
        self.rect(self.margin_x, self.h - 96, 120, 5, self.accent, r=2)
        if kicker:
            self.text(_esc(kicker).upper(), self.margin_x, self.h - 130, 11, (130, 120, 145), bold=True)
        self.y = self.h - 210
        for line in _wrap(title or "", int(self.body_w * 0.9), 38):
            self.text(line, self.margin_x, self.y, 38, (45, 40, 50), bold=True)
            self.y -= 52
        self.y -= 8
        for line in _wrap(subtitle or "", int(self.body_w * 0.8), 15):
            self.text(line, self.margin_x, self.y, 15, (110, 100, 120))
            self.y -= 24
        self.rect(self.margin_x, self.y - 4, 104, 3, (255, 160, 120), r=1)
        self.text("A free guide from pstore", self.margin_x, 40, 12, (150, 140, 155))
        self.page_break()

    def page_break(self):
        self.pages.append("\n".join(self._content) if self._content else "")
        self._content = []
        self.y = self.h - 96

    def heading(self, title, size=21):
        self.ensure(40)
        self.rect(self.margin_x, self.y + 2, 6, 26, self.accent, r=3)
        self.text(_esc(title).upper(), self.margin_x + 15, self.y + 2, size,
                  (40, 38, 44), bold=True)
        self.y -= size + 14

    def paragraph(self, text, size=12, leading=17, color=(60, 55, 65)):
        self.ensure(leading * 2)
        for line in _wrap(text, self.body_w, size):
            self.ensure(leading)
            self.text(line, self.margin_x, self.y, size, color)
            self.y -= leading
        self.y -= 4

    def bullets(self, items, size=12, leading=17, color=(60, 55, 65)):
        for it in (items or []):
            self.ensure(leading * 2)
            self.text("*", self.margin_x + 2, self.y, size, self.accent, bold=True)
            for line in _wrap(" " + str(it), self.body_w - 18, size):
                self.ensure(leading)
                self.text(line, self.margin_x + 16, self.y, size, color)
                self.y -= leading
            self.y -= 3

    def pullquote(self, text, size=15, color=(110, 60, 40)):
        self.ensure(54)
        qw = self.body_w - 26
        lines = _wrap(text, qw, size)
        box_h = len(lines) * (size + 8) + 26
        self.rect(self.margin_x, self.y - box_h + 8, self.body_w, box_h,
                  (255, 247, 240), r=14)
        self.rect(self.margin_x, self.y - box_h + 8, 6, box_h, self.accent, r=3)
        ty = self.y - 22
        for line in lines:
            self.text(line, self.margin_x + 24, ty, size, color)
            ty -= size + 8
        self.y -= box_h + 16

    def spacer(self, n=10):
        self.y -= n

    # -------------------------------------------------------------- assembly
    def save(self):
        self._footer()
        self.pages.append("\n".join(self._content) if self._content else "")
        n = len(self.pages)
        font_f1, font_f2 = 3 + n, 4 + n
        kids = " ".join("%d 0 R" % (3 + i) for i in range(n))
        objs = []
        objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")                # 1
        objs.append(b"<< /Type /Pages /Kids [ %s ] /Count %d >>"
                    % (kids.encode("latin-1"), n))                      # 2
        for i in range(n):                                              # 3..3+n-1 pages
            stream = 5 + n + i
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 %d %d ] "
                        b"/Contents %d 0 R /Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                        % (self.w, self.h, stream, font_f1, font_f2))
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")        # F1
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")   # F2
        for page in self.pages:                                         # streams
            objs.append(b"<< /Length %d >>stream\n%s\nendstream"
                        % (len(page.encode("latin-1", "replace")),
                           page.encode("latin-1", "replace")))
        out = bytearray()
        out += b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        offsets = [0]
        for idx, o in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % idx
            out += o
            out += b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n" % (len(objs) + 1)
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
        return bytes(out)