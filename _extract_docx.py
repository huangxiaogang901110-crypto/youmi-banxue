"""Extract all tables from a docx file."""
import sys
from docx import Document

doc = Document(sys.argv[1])
for i, table in enumerate(doc.tables):
    print(f'\n===== TABLE {i+1} =====')
    for j, row in enumerate(table.rows):
        cells = [cell.text.strip().replace('\n', ' | ') for cell in row.cells]
        print('|'.join(cells))
