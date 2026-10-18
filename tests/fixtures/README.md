# Synthetic fixtures

Fixture files are generated at test time from `tests/conftest.py`:

- `sample_source/*.md`, `*.txt` — synthetic integration documents with a
  contradiction between two sources;
- `docx_source/design.docx` — synthetic DOCX with headings, a table, and an
  image for `reports/manual_review.md`.

No customer documents are stored here. To add a binary fixture, keep it
synthetic and free of personal or client data.
