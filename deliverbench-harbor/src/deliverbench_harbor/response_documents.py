"""Bounded, non-executing document reads for business responders, never for Judge."""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from pypdf.errors import PyPdfError

MAX_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 128 * 1024 * 1024

def read_document(path):
    path = Path(path)
    if not path.exists():
        return {'status': 'missing', 'text': ''}
    if path.is_symlink() or not path.is_file():
        return {'status': 'invalid', 'text': 'Not a regular file'}
    if path.stat().st_size > MAX_BYTES:
        return {'status': 'invalid', 'text': 'Document exceeds the published 32 MiB per-file responder limit'}
    try:
        if path.suffix.lower() in ('.docx', '.pptx', '.xlsx'):
            lines = []
            with zipfile.ZipFile(path) as archive:
                if sum(x.file_size for x in archive.infolist()) > MAX_XML_BYTES:
                    raise ValueError('Expanded document too large')
                names = archive.namelist()
                shared = []
                if 'xl/sharedStrings.xml' in names:
                    tree = ET.fromstring(archive.read('xl/sharedStrings.xml'))
                    shared = [''.join(x.itertext()) for x in tree]
                for name in sorted(names):
                    if (name == 'word/document.xml' or re.fullmatch(r'word/(?:footnotes|endnotes|comments|header\d+|footer\d+)\.xml', name)
                            or re.fullmatch(r'ppt/(?:slides/slide\d+|notesSlides/notesSlide\d+)\.xml', name)):
                        tree = ET.fromstring(archive.read(name))
                        for i, p in enumerate(x for x in tree.iter() if x.tag.rsplit('}', 1)[-1] == 'p'):
                            s = ''.join(x.text or '' for x in p.iter() if x.tag.rsplit('}', 1)[-1] == 't')
                            if s.strip(): lines.append(f'{name}:p{i+1}: {s}')
                    elif re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name):
                        tree = ET.fromstring(archive.read(name))
                        for cell in (x for x in tree.iter() if x.tag.rsplit('}', 1)[-1] == 'c'):
                            values = [x.text or '' for x in cell.iter() if x.tag.rsplit('}', 1)[-1] in ('v', 't', 'f')]
                            if cell.get('t') == 's' and values: values = [shared[int(values[-1])]]
                            if values: lines.append(f'{name}:{cell.get("r")}: '+ ' | '.join(values))
            result = '\n'.join(lines)
        elif path.suffix.lower() == '.pdf':
            from pypdf import PdfReader
            result = '\n'.join(f'page {i+1}:\n{p.extract_text() or ""}' for i,p in enumerate(PdfReader(path).pages))
            if not result.strip() or not re.search(r'\w', re.sub(r'page \d+:', '', result)):
                raise ValueError('No readable text; scanned PDF requires a text layer')
        else:
            result = path.read_text(encoding='utf-8-sig')
        return {'status': 'readable' if result.strip() else 'empty', 'text': result}
    except (ValueError, UnicodeError, zipfile.BadZipFile, ET.ParseError, IndexError, KeyError, PyPdfError) as exc:
        return {'status': 'invalid', 'text': type(exc).__name__}

