# Data

The Cocoarynth corpus comes from [microsoft/ignite26-LAB673](https://github.com/microsoft/ignite26-LAB673/tree/main/data). Cocoarynth Chocolate Works is a fictional bean-to-bar chocolate maker that buys cacao from farms in five countries and sells through several retailers. All documents are generated: no real company, person, or record is described.

- `cocoarynth/documents/<folder>/`: 55 PDFs in LAB673's four folders. `postprovision` uploads them all to one Blob container, `cocoarynth`, and ingests them into one search index, `cocoarynth-all-index`.
  - `cocoarynth-origins`: farm and supplier records, partly in Spanish.
  - `cocoarynth-quality`: factory receiving, testing, tasting, and batch records.
  - `cocoarynth-logistics-retail`: shipping, carrier, retailer, and sales records.
  - `cocoarynth-policies`: company policies, guides, and reference material.
- [corpora.json](cocoarynth/corpora.json): the PDFs in each folder, as listed in LAB673.
- [questions.yaml](cocoarynth/questions.yaml): LAB673's "mysteries", with expected answers and the documents that are valid citations for each fact. Use them as inspiration for demo questions.

The PDFs are generated from structured facts in LAB673. To change a document, edit and re-render it there (see LAB673's `data/README.md`), then copy the PDFs back here. This repo ingests the PDFs with Content Understanding during `postprovision`; it doesn't use LAB673's index snapshots.
