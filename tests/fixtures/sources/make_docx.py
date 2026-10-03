"""Writes the technical fixture (C) as a real Word document with Word's own styles (Title, Heading 1/2, Caption,
Code) and a table, for the Phase 11 browser check:  python make_docx.py <out.docx>

Only the standard library: a .docx is a zip of XML parts. Deterministic (fixed timestamps)."""
import sys
import zipfile
from xml.sax.saxutils import escape

PARAGRAPHS = [
    ("Title", "Motion and Algorithms"),
    ("Heading1", "Newton's Second Law"),
    (None, "Force is defined as the product of mass and acceleration."),
    (None, "F = m × a"),
    (None, "where F is the force in newtons and m is the mass in kilograms."),
    (None, "Example: a 2 kg ball pushed with a force of 10 N accelerates at 5 m/s²."),
    ("Heading1", "Computing the Area of a Circle"),
    (None, "The following Python function returns the area of a circle for a given radius."),
    ("Code", "def area(r):"),
    ("Code", "    return 3.14159 * r * r"),
    (None, "Output: 12.56636"),
    ("Heading1", "Binary Search"),
    (None, "Binary search is an algorithm that finds a value in a sorted list by repeatedly halving the search range."),
    ("TABLE", [["List size", "Comparisons"], ["8", "3"], ["1024", "10"]]),
    ("Caption", "Table 1: Comparisons needed for lists of different sizes"),
    ("Heading1", "Left out later"),
    (None, "This section is removed in the Document Assistant before the lesson is generated."),
]

STYLES = {"Title": "Title", "Heading1": "heading 1", "Heading2": "heading 2", "Caption": "Caption", "Code": "Code"}


def run(text):
    return f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def paragraph(style, text):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{run(text)}</w:p>"


def table(rows):
    cells = "".join("<w:tr>" + "".join(f"<w:tc><w:p>{run(c)}</w:p></w:tc>" for c in row) + "</w:tr>" for row in rows)
    return f"<w:tbl><w:tblPr><w:tblStyle w:val=\"TableGrid\"/></w:tblPr>{cells}</w:tbl>"


def build(path):
    body = "".join(table(t) if s == "TABLE" else paragraph(s, t) for s, t in PARAGRAPHS)
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {ns}><w:body>{body}<w:sectPr/></w:body></w:document>'
    styles = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {ns}>' + "".join(
        f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/></w:style>' for sid, name in STYLES.items()) + "</w:styles>"
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                               '<Default Extension="xml" ContentType="application/xml"/>'
                               '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                               '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        "word/document.xml": document,
        "word/styles.xml": styles,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            z.writestr(info, data.encode("utf-8"))


if __name__ == "__main__":
    build(sys.argv[1])
