"""QR destination URLs and the printable A4 QR PDF."""
from io import BytesIO
from urllib.parse import urlencode

from django.conf import settings
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


def build_menu_url(restaurant_id, table_id, token=None):
    """`<FRONTEND_BASE_URL>/menu?restaurant_id=..&table=..[&token=..]`.

    The base URL comes from settings (never hardcoded) and a trailing slash is
    tolerated. The confirmed parameters `restaurant_id` and `table` are always
    present and unchanged; `token` is appended only for the printed QR code so the
    frontend can verify the scan through `GET /api/v1/qr/resolve/`."""
    base = settings.FRONTEND_BASE_URL.strip().rstrip('/')
    params = {'restaurant_id': restaurant_id, 'table': table_id}
    if token is not None:
        params['token'] = str(token)
    return f'{base}/menu?{urlencode(params)}'


def _fit(text, font, size, max_width):
    """Shrink the font until `text` fits, then truncate as a last resort."""
    while size > 7 and stringWidth(text, font, size) > max_width:
        size -= 1
    while len(text) > 1 and stringWidth(text, font, size) > max_width:
        text = text[:-2] + '…'
    return text, size


# --------------------------------------------------------------------------
# Card geometry (millimetres). Every offset is measured DOWN from the top edge
# of a card, so the vertical stack below cannot overlap: each block ends above
# the next one starts. tests.QRPDFTests.test_card_layout_does_not_overlap checks it.
# --------------------------------------------------------------------------
COLUMNS, ROWS = 2, 3
PER_PAGE = COLUMNS * ROWS
PAGE_MARGIN = 10
CARD_H = (297 - 2 * PAGE_MARGIN) / ROWS      # 92.33 mm
CARD_W = (210 - 2 * PAGE_MARGIN) / COLUMNS   # 95 mm
GUIDE_INSET = 2          # cutting-guide rectangle sits this far inside the card
NAME_BASELINE = 9        # restaurant name, 12 pt
LABEL_BASELINE = 20      # "Table X", up to 26 pt (cap height ~7 mm)
QR_TOP = 24
QR_SIZE = 52             # QR incl. its quiet zone; scans fine from ~2 cm up
TYPE_BASELINE = 82       # "Indoor | 4 seats", 10 pt
FOOTER_BASELINE = 87     # "Scan to view the menu", 8 pt
QR_BOTTOM = QR_TOP + QR_SIZE  # 76 mm, leaves 6 mm above the 82 mm baseline


def _page(pdf, restaurant):
    pdf.setTitle(f'{restaurant.name} - table QR codes')


def render_qr_pdf(restaurant, tables):
    """Return the bytes of an A4 PDF with one QR card per table (6 per page).

    With no tables it still returns a valid one-page PDF that says so."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _page(pdf, restaurant)
    page_w, page_h = A4
    margin, cell_w, cell_h = PAGE_MARGIN * mm, CARD_W * mm, CARD_H * mm

    if not tables:
        name, size = _fit(restaurant.name, 'Helvetica-Bold', 18, page_w - 2 * margin)
        pdf.setFont('Helvetica-Bold', size)
        pdf.drawCentredString(page_w / 2, page_h - 40 * mm, name)
        pdf.setFont('Helvetica', 14)
        pdf.drawCentredString(page_w / 2, page_h - 55 * mm, 'No tables available')
        pdf.setFont('Helvetica', 10)
        pdf.drawCentredString(
            page_w / 2, page_h - 63 * mm, 'Add tables in TableFlow, then export the QR codes again.')
        pdf.save()
        return buffer.getvalue()

    for index, table in enumerate(tables):
        slot = index % PER_PAGE
        if index and slot == 0:
            pdf.showPage()
        col, row = slot % COLUMNS, slot // COLUMNS
        x = margin + col * cell_w
        top = page_h - margin - row * cell_h   # y of the card's top edge
        centre = x + cell_w / 2
        inset = GUIDE_INSET * mm

        # Cutting guide.
        pdf.setStrokeColorRGB(0.7, 0.7, 0.7)
        pdf.setDash(3, 3)
        pdf.rect(x + inset, top - cell_h + inset, cell_w - 2 * inset, cell_h - 2 * inset)
        pdf.setDash()
        pdf.setFillColorRGB(0, 0, 0)

        text_w = cell_w - 4 * inset
        name, size = _fit(restaurant.name, 'Helvetica-Bold', 12, text_w)
        pdf.setFont('Helvetica-Bold', size)
        pdf.drawCentredString(centre, top - NAME_BASELINE * mm, name)

        label, size = _fit(f'Table {table.table_number}', 'Helvetica-Bold', 26, text_w)
        pdf.setFont('Helvetica-Bold', size)
        pdf.drawCentredString(centre, top - LABEL_BASELINE * mm, label)

        size_pt = QR_SIZE * mm
        widget = QrCodeWidget(
            build_menu_url(restaurant.pk, table.pk, table.qr_token), barLevel='M')
        x0, y0, x1, y1 = widget.getBounds()
        drawing = Drawing(size_pt, size_pt, transform=[
            size_pt / (x1 - x0), 0, 0, size_pt / (y1 - y0), 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, pdf, centre - size_pt / 2, top - QR_BOTTOM * mm)

        pdf.setFont('Helvetica', 10)
        seats = f'{table.capacity} seat{"" if table.capacity == 1 else "s"}'
        pdf.drawCentredString(centre, top - TYPE_BASELINE * mm, f'{table.table_type}  |  {seats}')
        pdf.setFont('Helvetica-Oblique', 8)
        pdf.drawCentredString(centre, top - FOOTER_BASELINE * mm, 'Scan to view the menu')

    pdf.save()
    return buffer.getvalue()
