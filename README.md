# Multilingual Document Intelligence — Azure AI Research

> Exploration of Azure AI services to extract, translate, and structure content
> from documents written in six Indian regional languages — **Hindi, Bengali, Tamil, Telugu, Marathi, Kannada**.

---

## The Problem

We receive invoices, receipts, and documents written in regional Indian scripts. Before
any data processing can happen, the content needs to be made readable in English. We ran four
trials — each using a different combination of Azure AI services — to find the best approach
for accuracy, layout preservation, and structured extraction.

---

## Languages & Scripts Tested

| Language | Script     | ISO Code |
|----------|------------|----------|
| Hindi    | Devanagari | `hi`     |
| Bengali  | Bengali    | `bn`     |
| Tamil    | Tamil      | `ta`     |
| Telugu   | Telugu     | `te`     |
| Marathi  | Devanagari | `mr`     |
| Kannada  | Kannada    | `kn`     |

---

## Project Structure

```
.
├── .env                                         ← single config file for all trials
├── src/
│   ├── trial1_ocr_extraction/
│   │   └── analyze_read_folder_to_md.py
│   ├── trial2_text_translation/
│   │   ├── azure_vision_translate_v2.py
│   │   └── translate_single_pdf.py
│   ├── trial3_visual_image_translation/
│   │   ├── batch_translate_images_folder.py
│   │   ├── azure_batch_image_translation.py
│   │   └── translate_single_image.py
│   └── trial4_invoice_extraction/
│       └── invoice_extraction_test.py
├── utils/
│   ├── test_api_endpoints.py
│   └── test_connectivity.py
├── test_data/
│   ├── hindi/   bengali/   tamil/
│   ├── telugu/  marathi/   kannada/
│   └── invoices/
└── outputs/
    ├── trial1_ocr_extraction/
    ├── trial2_text_translation/
    ├── trial2_pdf_translation/
    └── trial3_visual_image_translation/
```

---

## Environment Setup

All trials share a single `.env` at the project root:

```env
# Trial 1 & 4 — Azure Document Intelligence
DOCUMENTINTELLIGENCE_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
DOCUMENTINTELLIGENCE_API_KEY=<key>

# Trial 2 & 3 — Azure Vision OCR
AZURE_VISION_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
AZURE_VISION_KEY=<key>

# Trial 2, 3 & 4 — Azure Translator
AZURE_TRANSLATOR_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
AZURE_TRANSLATOR_KEY=<key>

# Trial 2 & 3 — Azure Blob Storage
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...
AZURE_OUTPUT_CONTAINER=translated-output

# Trial 4 — Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_KEY=<key>
AZURE_OPENAI_DEPLOYMENT=gpt-4o
```

Install dependencies:
```bash
pip install -r requirements.txt
```

---

---

# Trial 1 — OCR Extraction with Azure Document Intelligence

**Script:** `src/trial1_ocr_extraction/analyze_read_folder_to_md.py`
**Azure Service:** Azure Document Intelligence (`prebuilt-read` model)
**Goal:** Extract raw text and full layout metadata from every document without any translation.

## How It Works

```
Input folder (images / PDFs)
        │
        ▼  os.walk — recursively find all supported files
        │
        ▼  analyze_file()
        │  → Azure Document Intelligence prebuilt-read
        │  → Returns: text, pages, lines, words, bounding boxes, styles
        │
        ▼  build_markdown()
        │  → Formats result into a structured Markdown report
        │
        ▼  Saves <filename>_read_analysis.md  +  <filename>_read_analysis.json
```

## Supported File Types

`.pdf` `.jpg` `.jpeg` `.png` `.tiff` `.tif` `.bmp` `.heif` `.docx` `.xlsx` `.pptx` `.html`

## Functions

---

### `format_bounding_box(bounding_box) → str`

Converts a flat list of 8 polygon coordinates (returned by Azure) into a human-readable
string showing four corner points.

```
Input:  [257.0, 31.0, 555.0, 34.0, 555.0, 103.0, 257.0, 101.0]
Output: "[257.0, 31.0], [555.0, 34.0], [555.0, 103.0], [257.0, 101.0]"
```

Returns `"N/A"` if no bounding box is present.

---

### `analyze_file(client, file_path) → result`

Opens a single file in binary mode and submits it to Azure Document Intelligence using
the `prebuilt-read` model.

- Sends the file as raw bytes (`application/octet-stream`)
- Uses `begin_analyze_document()` which starts an async job and returns a poller
- Calls `poller.result()` to block until the job completes
- Returns the full Azure result object containing pages, lines, words, styles

```python
poller = client.begin_analyze_document(
    "prebuilt-read",
    body=f,
    content_type="application/octet-stream",
)
return poller.result()
```

---

### `build_markdown(result, source_label) → str`

Converts the raw Azure result object into a formatted Markdown string with three sections:

**Section 1 — Full Extracted Text**
The complete OCR content joined as plain text. This is the native-language script exactly
as Azure read it from the document.

**Section 2 — Content Style**
Iterates `result.styles` to report whether each section of the document is handwritten
or printed/typed, along with the confidence score (0.0–1.0).

```markdown
- Style: Handwritten | Confidence: 0.50
- Style: Printed / Typed | Confidence: 1.00
```

**Section 3 — Page-by-Page Breakdown**
For every page in `result.pages`:
- Page dimensions (width × height in pixels)
- A table of every **line** with its content and bounding box coordinates
- A table of every **word** with its individual confidence score

Pipe characters in content are escaped (`\|`) so Markdown tables render correctly.

---

### `process_folder(input_dir, output_dir, skip_existing=True) → None`

The main orchestrator. Walks the entire input directory tree, processes each file, and
mirrors the subfolder structure in the output directory.

**Steps:**
1. `os.walk(input_dir)` — recursively collects all files whose extension is in `SUPPORTED_EXTENSIONS`
2. Creates one `DocumentIntelligenceClient` shared across all files
3. For each file:
   - Computes relative path to mirror folder structure in output
   - Checks if both `.md` and `.json` already exist → skips if `skip_existing=True`
   - Calls `analyze_file()` → `build_markdown()` → saves `.md`
   - Serialises result with `result.as_dict()` → saves `.json`
4. Prints a final summary: `Success | Skipped | Failed`

**Output naming pattern:**
```
input:  test_data/hindi/hindi1.jpg
output: outputs/trial1_ocr_extraction/hindi/hindi1_read_analysis.md
        outputs/trial1_ocr_extraction/hindi/hindi1_read_analysis.json
```

## How to Run

```bash
python src/trial1_ocr_extraction/analyze_read_folder_to_md.py test_data outputs/trial1_ocr_extraction
```

## Output

```
outputs/trial1_ocr_extraction/
  hindi/
    hindi1_read_analysis.md      ← full text + line table + word confidence table
    hindi1_read_analysis.json    ← complete Azure response as JSON
  bengali/
    bengali1_read_analysis.md
    ...
```

Sample `.md` content:
```markdown
# Document Analysis Report
**Source:** `hindi/hindi1.jpg`

## Full Extracted Text
ग्रीन व्यू होटल * आराम आपका, सेवा हमारी
12, सिविल लाइन्स, उदयपुर, राजस्थान - 313001
...

## Content Style
- Style: Handwritten | Confidence: 0.50
- Style: Handwritten | Confidence: 1.00

## Page-by-Page Breakdown
### Page 1
- Dimensions: 850 x 1100 (pixel)

#### Lines
| Line # | Content              | Bounding Box                                  |
|--------|----------------------|-----------------------------------------------|
| 1      | ग्रीन व्यू होटल      | [257, 31], [555, 34], [555, 103], [257, 101]  |
| 2      | आराम आपका, सेवा हमारी | [266, 113], [547, 112], [548, 137], [266, 138]|

#### Words
| Word           | Confidence |
|----------------|------------|
| ग्रीन          | 0.9910     |
| व्यू           | 0.9880     |
```

## Result

| Metric | Value |
|--------|-------|
| Files processed | 31 / 31 |
| Output per file | 1 `.md` + 1 `.json` |
| Translation | None — native script only |
| Bounding boxes | Yes — every word and line |
| Confidence scores | Yes — per word (0.0–1.0) |
| Handwriting detection | Yes — per style region |
| Skip already-processed | Yes — safe to re-run |

---

---

# Trial 2 — Text Translation (Azure Vision OCR + Azure Translator)

Two scripts in this trial covering images and PDFs separately.

---

## Trial 2A — Batch Image Translation

**Script:** `src/trial2_text_translation/azure_vision_translate_v2.py`
**Azure Services:** Azure Vision Read API + Azure Translator Text API + Azure Blob Storage
**Goal:** OCR every image in the input folders, translate the extracted text to English,
save results as `.txt` files, and upload everything to Azure Blob Storage.

### How It Works

```
Input root (language subfolders)
        │
        ▼  run_batch()
        │  reads LANG_MAP → pairs each subfolder with its language code
        │  launches ThreadPoolExecutor (default 3 workers)
        │
        ├── translate_folder()  [runs in parallel per language subfolder]
        │       │
        │       ├── process_pdfs()   → Azure Document Translation SDK
        │       │
        │       └── process_image()  [per image]
        │               │
        │               ▼  _ocr_image()
        │               │  → Azure Vision Read API v3.2
        │               │  → polls Operation-Location until succeeded
        │               │
        │               ▼  _translate_text()
        │               │  → Azure Translator Text API v3.0
        │               │
        │               ▼  saves .txt  +  uploads to Blob Storage
        │
        ▼  Summary log: total output files
```

### Language Map

```python
LANG_MAP = {
    "hindi": "hi",   "bengali": "bn",  "tamil": "ta",
    "telugu": "te",  "marathi": "mr",  "kannada": "kn"
}
```

Subfolder names must match these keys exactly. Unrecognised folders are skipped with a warning.

### Functions

---

#### `_make_session() → requests.Session`

Creates a shared HTTP session used for all Vision and Translator REST calls.

- Configures automatic retry: 3 attempts, exponential backoff (1s, 2s, 4s)
- Retries on status codes: `429` (rate limit), `500`, `502`, `503`, `504`
- Loads the `certifi` CA bundle for SSL verification; falls back to `verify=False`
  if certifi is not installed

---

#### `_parse_connection_string(conn_str) → (account_name, account_key)`

Parses the Azure Storage connection string manually by splitting on `;` and
extracting `AccountName=` and `AccountKey=` segments. Returns both as strings
so they can be used to generate SAS tokens.

---

#### `_ensure_container(blob_service, container) → None`

Attempts to create the permanent output container (`translated-output` by default).
Silently ignores the error if it already exists, so this is safe to call every run.

---

#### `_upload_blob(blob_service, container, blob_path, local_path) → None`

Uploads a single local file to Azure Blob Storage with `overwrite=True`.
Used to archive both the original image copy and the translated `.txt` file.

---

#### `_ocr_image(image_path) → str`

Extracts text from a single image using the **Azure Vision Read API v3.2** (async).

**Steps:**
1. POST the image bytes to `/vision/v3.2/read/analyze`
2. Reads `Operation-Location` from the response header — this is the polling URL
3. Polls every 1 second (up to 30 attempts) until status is `"succeeded"` or `"failed"`
4. On success: iterates `analyzeResult.readResults[].lines[]` and joins all `.text` values
5. Returns the full extracted text as a newline-separated string

Raises `RuntimeError` on failure and `TimeoutError` if 30 seconds elapse with no result.

---

#### `_translate_text(text, target_lang) → str`

Translates a plain-text string using the **Azure Translator Text API v3.0**.

- Skips empty strings (returns `""` immediately)
- POSTs to `/translator/text/v3.0/translate?api-version=3.0&to=<lang>`
- Body is a JSON array: `[{"text": "..."}]`
- Returns `response[0]["translations"][0]["text"]`

---

#### `process_image(image_path, local_out_dir, blob_service, folder_name) → list[Path]`

Processes one image end-to-end:

1. Copies the original image to the local output directory
2. Uploads the image copy to the permanent Blob container
3. Calls `_ocr_image()` to extract text
4. If text is empty → logs a warning, returns early (image copy only)
5. Calls `_translate_text(extracted, "en")` to translate to English
6. Writes translated text to `<stem>.txt`
7. Uploads the `.txt` to Blob Storage

Returns a list of saved local paths (image + txt).

---

#### `process_pdfs(pdf_files, target_lang, local_out_dir, blob_service, account_name, account_key, folder_name) → list[Path]`

Translates a batch of PDFs using the **Azure Document Translation SDK**.

**Steps:**
1. Creates two temp Blob containers: `src-<random8>` and `tgt-<random8>`
2. Uploads all PDFs to the source container
3. Generates SAS tokens (2-hour expiry) for both containers
4. Calls `DocumentTranslationClient.begin_translation(source_url, target_url, target_lang)`
5. Blocks on `poller.result()` until all documents complete
6. Downloads each successfully translated PDF from the target container
7. Saves locally and uploads to the permanent output container
8. `finally` block always deletes both temp containers — no orphaned blobs

---

#### `translate_folder(subfolder, target_lang, local_output_root, blob_service, account_name, account_key) → list[Path]`

Orchestrates processing for one language subfolder:

1. Splits files into `pdf_files` and `img_files`
2. Creates the local output subdirectory (e.g. `outputs/trial2_text_translation/hindi/`)
3. Calls `process_pdfs()` for all PDFs in one batch job
4. Calls `process_image()` for each image individually (errors per image are caught and logged)

Returns the combined list of all saved output paths.

---

#### `run_batch(input_root, output_root, max_workers=3) → None`

Entry point for the full batch run:

1. Validates that the input root exists and all required env vars are set
2. Iterates `input_root` to find subfolders matching `LANG_MAP`
3. Creates one `BlobServiceClient` shared across all threads
4. Launches a `ThreadPoolExecutor` — each language folder runs in its own thread
5. Uses `as_completed()` to collect results as they finish
6. Logs the total output file count on completion

### How to Run

```bash
# All language folders (3 parallel workers)
python src/trial2_text_translation/azure_vision_translate_v2.py test_data outputs/trial2_text_translation

# Custom number of parallel workers
python src/trial2_text_translation/azure_vision_translate_v2.py test_data outputs/trial2_text_translation 5
```

### Output

```
outputs/trial2_text_translation/
  hindi/
    hindi1.jpg       ← original image (copied)
    hindi1.txt       ← English translation of extracted text
  bengali/
    bengali1.png
    bengali1.txt
  ...
```

---

## Trial 2B — Single PDF Translation

**Script:** `src/trial2_text_translation/translate_single_pdf.py`
**Azure Services:** Azure Document Translation SDK + Azure Blob Storage
**Goal:** Translate one PDF file into a target language and save it alongside the original.

### Functions

---

#### `_parse_connection_string(conn_str) → (account_name, account_key)`

Same as Trial 2A — splits the storage connection string to extract credentials for SAS generation.

---

#### `translate_pdf(input_pdf, target_lang="en") → None`

Translates a single PDF end-to-end:

1. Derives output path: `translated_<lang>_<filename>.pdf` in the same folder as input
2. Creates two temp containers: `src-<random8>` / `tgt-<random8>`
3. Uploads the PDF to the source container
4. Generates 2-hour SAS tokens for both containers
5. Submits a translation job via `DocumentTranslationClient.begin_translation()`
6. Blocks on `poller.result()` — waits for Azure to finish translating
7. Downloads the translated PDF from the target container
8. `finally` block always cleans up both temp containers

### How to Run

```bash
# Translate a PDF to English (default)
python src/trial2_text_translation/translate_single_pdf.py "test_data/invoices/invoice_hindi.pdf"

# Translate to a specific language
python src/trial2_text_translation/translate_single_pdf.py "test_data/invoices/invoice_hindi.pdf" hi
```

### Output

```
test_data/invoices/
  invoice_hindi.pdf                   ← original
  translated_en_invoice_hindi.pdf     ← translated
```

---

---

# Trial 3 — Visual Image Translation (Azure Document Translation Batch API)

Three scripts in this trial — two for batch processing, one for single images.

**Azure Service:** Azure Document Translation Batch API (`2025-12-01-preview`)
**Goal:** Translate text _visually inside the image_ — the layout stays identical but all
native-language text is replaced with English text rendered in-place.

> **Important:** Image translation only works on the preview API version `2025-12-01-preview`.
> The stable API (`2024-05-01`) only supports PDFs.

---

## Trial 3A — Batch Folder Translation (All Images, One Job)

**Script:** `src/trial3_visual_image_translation/batch_translate_images_folder.py`

Uploads ALL images at once in a single batch job — the most efficient approach.

### How It Works

```
Input folder
      │
      ▼  os.walk → collect all supported images
      │
      ▼  Upload ALL images to src-<id> container (preserving relative paths)
      │
      ▼  Generate SAS tokens (4-hour expiry) for source + target containers
      │
      ▼  POST to /translator/document/batches (one job for everything)
      │
      ▼  Poll every 10s — logs progress (succeeded / failed / inProgress / total)
      │
      ▼  Download all translated images from target container
      │
      ▼  finally: delete both temp containers
```

### Functions

---

#### `_parse_connection_string(conn_str) → (account_name, account_key)`

Parses the Azure Storage connection string to extract `AccountName` and `AccountKey`
for SAS token generation. Splits on `;` and scans each segment.

---

#### `safe_makedirs(path) → None`

A patched version of `Path.mkdir()` that handles a specific edge case: if a **file**
already exists at the path where a directory needs to be created (a leftover from a
previous run), it deletes the file first, then creates the directory.

```
path is a directory  → do nothing
path is a file       → delete the file, then create the directory
path does not exist  → create the full directory tree
```

This prevents `FileExistsError` that `mkdir(exist_ok=True)` doesn't handle when the
path is occupied by a file instead of a folder.

---

#### `translate_folder(input_dir, output_dir, target_lang="en") → None`

Main function — translates an entire folder of images in one batch job:

1. `os.walk()` — collects all images with supported extensions recursively
2. Creates two temp Blob containers: `src-<random8>` / `tgt-<random8>`
3. Uploads every image, preserving its relative sub-path as the blob name
4. Generates SAS tokens with **4-hour** expiry (longer than Trial 2 to allow for large batches)
5. POSTs one batch job to `/translator/document/batches?api-version=2025-12-01-preview`
6. Polls every 10 seconds, printing: `Status | X succeeded, Y failed, Z in progress (Total: N)`
7. On completion, lists all blobs in the target container and downloads each one
8. Uses `safe_makedirs()` for both the output root and each blob's parent folder
9. Skips blobs ending in `/` (directory markers, not real files)
10. `finally` block always deletes both temp containers

### How to Run

```bash
python src/trial3_visual_image_translation/batch_translate_images_folder.py test_data outputs/trial3_visual_image_translation
```

---

## Trial 3B — One Image at a Time

**Script:** `src/trial3_visual_image_translation/azure_batch_image_translation.py`

Same visual translation approach but processes images one by one in a loop.
Slower, but easier to debug and resume from failures.

### How It Works

```
For each image in input_folder:
    │
    ├── Skip if translated_<lang>_<name> already exists (skip_existing=True)
    │
    ▼  Create temp containers src-<id> / tgt-<id>
    │
    ▼  Upload single image to source container
    │
    ▼  Generate SAS tokens (2-hour expiry)
    │
    ▼  POST to /translator/document:batch-translate (one image per job)
    │
    ▼  Poll every 5s — prints inline status dots
    │
    ▼  Download translated image from target container
    │  → saved as translated_<lang>_<original_name>
    │
    └── finally: delete both temp containers (always, even on failure)
```

### Functions

---

#### `_parse_connection_string(conn_str) → (account_name, account_key)`

Same as Trial 3A.

---

#### `translate_image_batch(input_folder, output_folder, target_lang="en", skip_existing=True) → None`

Processes images in a folder one at a time:

1. Collects images using `input_path.iterdir()` (non-recursive — top level only)
2. For each image, checks if `translated_<lang>_<name>` already exists in the output folder
   — skips if `skip_existing=True`
3. For each image creates fresh temp containers per image (unique `run_id` each time)
4. Uploads just that one image, generates SAS tokens (2-hour expiry)
5. POSTs to `/translator/document:batch-translate` (different endpoint than Trial 3A)
6. Polls every 5 seconds, printing inline status (e.g. `Running Running Succeeded`)
7. On success: downloads the translated image blob and saves with `translated_<lang>_` prefix
8. On failure: logs the error message from the response
9. `finally`: always deletes the two temp containers before moving to the next image
10. Prints a final summary: `Success | Skipped | Failed`

### How to Run

```bash
python src/trial3_visual_image_translation/azure_batch_image_translation.py test_data/hindi outputs/trial3_visual_image_translation en
```

---

## Trial 3C — Single Image Translation

**Script:** `src/trial3_visual_image_translation/translate_single_image.py`

Translates exactly one image. Used for quick ad-hoc testing.

### Functions

---

#### `_parse_connection_string(conn_str) → (account_name, account_key)`

Same as Trial 3A and 3B.

---

#### `translate_image(input_image, target_lang="en") → None`

1. Validates the file extension against `SUPPORTED_EXTENSIONS` — exits if unsupported
2. Derives output path: `translated_<lang>_<filename>` in the same directory as input
3. Creates temp containers, uploads the file, generates SAS tokens (2-hour expiry)
4. POSTs to `/translator/document:batch-translate?api-version=2025-12-01-preview`
5. Polls every 5 seconds until `Succeeded / Failed / Cancelled`
6. Downloads the translated image from the target container
7. `finally`: always deletes both temp containers

### How to Run

```bash
python src/trial3_visual_image_translation/translate_single_image.py test_data/hindi/hindi1.jpg en
```

## Trial 3 — Output & Quality

```
outputs/trial3_visual_image_translation/
  hindi/    hindi1.jpg    ← same image, text visually redrawn in English
  marathi/  marathi1.png
  ...
```

| Language | Translation Quality | Notes |
|----------|---------------------|-------|
| Hindi    | ✅ Good  | Clean English, layout preserved |
| Marathi  | ✅ Good  | Reads naturally |
| Telugu   | ⚠️ Partial | Numbers translated, some script residue |
| Bengali  | ⚠️ Partial | Words translated, some names garbled |
| Tamil    | ❌ Poor  | Mixed Latin/Tamil characters in output |
| Kannada  | ❌ Poor  | Mostly untranslated or transliterated |

---

---

# Trial 4 — Invoice Field Extraction (OCR + Translator + GPT-4o)

**Script:** `src/trial4_invoice_extraction/invoice_extraction_test.py`
**Azure Services:** Azure Document Intelligence + Azure Translator + Azure OpenAI (GPT-4o)
**Goal:** Run a full three-step pipeline per document — OCR in native language →
translate to English → extract structured invoice fields using GPT-4o.

## How It Works

```
Input root (language subfolders)
        │
        ▼  run_extraction()
        │  validates env vars, finds language subfolders
        │  launches ThreadPoolExecutor (default 3 workers)
        │
        ├── process_folder()  [per language subfolder, in parallel]
        │       │
        │       └── process_file()  [per document]
        │               │
        │               ▼  _ocr_file()
        │               │  → Azure Document Intelligence prebuilt-read
        │               │  → Extracts raw text in native script
        │               │
        │               ▼  _translate_to_english()
        │               │  → Azure Translator Text API v3.0
        │               │  → Chunks at 45,000 chars to stay under 50,000 char limit
        │               │
        │               ▼  _extract_fields_with_gpt()
        │               │  → Azure OpenAI GPT-4o
        │               │  → INVOICE_PROMPT → structured JSON
        │               │
        │               ▼  Saves <filename>.json
```

## The GPT-4o Prompt

```
You are an invoice data extraction assistant.
Extract the following fields and return a valid JSON object.
If a field is not present, set its value to null.

Fields: vendor_name, vendor_address, vendor_tax_id, customer_name,
        customer_address, customer_id, invoice_id, invoice_date,
        due_date, purchase_order, billing_address, shipping_address,
        subtotal, total_tax, invoice_total, amount_due,
        line_items[]: { description, quantity, unit, unit_price, amount }

Return ONLY the JSON object. No explanation, no markdown, no code block.
```

Temperature is set to `0` for deterministic, consistent extraction.

## Functions

---

### `_make_session() → requests.Session`

Creates a shared `requests.Session` for all Translator API calls (same pattern as Trial 2A):

- 3 retries with exponential backoff (1s, 2s, 4s)
- Retries on `429`, `500`, `502`, `503`, `504`
- Uses `certifi` CA bundle for SSL verification

---

### `_ocr_file(file_path, di_client) → str`

Extracts all text from a document using **Azure Document Intelligence `prebuilt-read`**.

- Opens the file in binary mode and submits it via `begin_analyze_document()`
  with `body=f` and `content_type="application/octet-stream"`
- Waits for the result with `poller.result()`
- Iterates `result.pages → page.lines → line.content`
- Joins all line content with newlines and returns the full native-language text

This uses Document Intelligence (not Vision API) because it is more accurate on
structured documents with tables, small text, and mixed content.

---

### `_translate_to_english(text) → str`

Translates native-language text to English using **Azure Translator Text API v3.0**.

- Returns `""` immediately if the text is empty or whitespace
- Splits the input into **45,000-character chunks** before sending
  (Azure Translator has a hard limit of 50,000 characters per request;
  the 45,000 buffer prevents edge-case failures with multi-byte characters)
- Each chunk is sent as `[{"text": chunk}]` and the translation is extracted
  from `response[0]["translations"][0]["text"]`
- All chunk translations are joined with newlines and returned

---

### `_extract_fields_with_gpt(english_text, aoai_client) → dict`

Sends the translated English text to **Azure OpenAI GPT-4o** to extract structured fields.

- Formats `INVOICE_PROMPT` with the English text
- Calls `chat.completions.create()` with `temperature=0` and `max_tokens=2000`
- Parses the response as JSON with `json.loads()`
- If parsing fails (GPT returned markdown or explanation instead of raw JSON):
  saves the raw string under the key `"raw_extraction"` so no data is lost

---

### `process_file(file_path, di_client, aoai_client, local_out_dir) → Path`

Coordinates the three-step pipeline for a single document:

1. Calls `_ocr_file()` → raw native text
2. If no text was extracted → logs a warning and returns `None` (skips the file)
3. Calls `_translate_to_english()` → English text
4. Calls `_extract_fields_with_gpt()` → structured dict
5. Adds three extra fields to the dict:
   - `"file"` — original filename
   - `"raw_text"` — original OCR output in native script
   - `"english_text"` — translated English text
6. Serialises to JSON with `indent=2` and `ensure_ascii=False` (preserves native characters)
7. Saves to `<stem>.json` in the output directory

---

### `process_folder(subfolder, local_output_root, di_client, aoai_client) → list[Path]`

Processes all supported files in one language subfolder:

1. Lists all files matching `SUPPORTED_EXTS` (`.pdf`, `.jpg`, `.jpeg`, `.png`, `.tiff`, `.bmp`)
2. Creates the output subdirectory (e.g. `outputs/trial4_invoice_extraction/hindi/`)
3. Calls `process_file()` for each document sequentially within the folder
4. Catches and logs individual file errors without stopping the rest
5. Returns the list of successfully saved JSON paths

---

### `run_extraction(input_root, output_root, max_workers=3) → None`

Entry point and orchestrator:

1. Validates the input root exists
2. Checks all three service credentials are present — exits with a clear error if any are missing
3. Finds all subfolders matching `LANG_MAP` keys
4. Falls back to processing the root folder directly if no language subfolders are found
   but supported files exist at the top level
5. Creates one `DocumentIntelligenceClient` and one `AzureOpenAI` client — shared across threads
6. `ThreadPoolExecutor` with `max_workers` threads — each language folder runs in parallel
7. `as_completed()` collects results and logs `[<language>] done — N file(s).` as each finishes
8. Logs the total count and output path on completion

### How to Run

```bash
# All language folders (default 3 parallel workers)
python src/trial4_invoice_extraction/invoice_extraction_test.py test_data outputs/trial4_invoice_extraction

# Custom output folder
python src/trial4_invoice_extraction/invoice_extraction_test.py test_data my_output

# More parallel workers
python src/trial4_invoice_extraction/invoice_extraction_test.py test_data outputs/trial4_invoice_extraction 5
```

## Output

```
outputs/trial4_invoice_extraction/
  hindi/
    hindi1.json
  bengali/
    bengali1.json
  ...
```

Sample JSON output:
```json
{
  "vendor_name": "Green View Hotel",
  "vendor_address": "12, Civil Lines, Udaipur, Rajasthan - 313001",
  "vendor_tax_id": null,
  "customer_name": "Shri Ajay Singh",
  "customer_address": "25, Ambamata Scheme, Udaipur - 313001",
  "customer_id": null,
  "invoice_id": "GVH/24-25/0187",
  "invoice_date": "24 May 2024",
  "due_date": null,
  "subtotal": "12,480.00",
  "total_tax": "2,197.00",
  "invoice_total": "14,677.00",
  "amount_due": "14,677.00",
  "line_items": [
    { "description": "Room Rent (Deluxe Room)", "quantity": "3 nights", "unit_price": "3,200.00", "amount": "9,600.00" },
    { "description": "Breakfast", "quantity": "3", "unit_price": "250.00", "amount": "750.00" },
    { "description": "Laundry", "quantity": "1", "unit_price": "150.00", "amount": "150.00" }
  ],
  "file": "hindi1.jpg",
  "raw_text": "ग्रीन व्यू होटल * आराम आपका...",
  "english_text": "Green View Hotel * Rest from you..."
}
```

---

---

# Utility Scripts

## `utils/test_api_endpoints.py`

Tests whether both the stable and preview Azure Translator batch API URLs are reachable
and returns HTTP status codes. Written to confirm that image translation requires the
preview API version.

```bash
python utils/test_api_endpoints.py
# Testing URL:  .../batches?api-version=2024-05-01      → Status: 200
# Testing URL2: .../batches?api-version=2025-12-01-preview → Status: 200
```

**Key finding:** Both URLs return 200, but image payloads submitted to the stable API
are rejected at the job level. The preview API is required for image support.

## `utils/test_connectivity.py`

One-line connectivity check — GETs the Azure Translator base URL and prints the HTTP
status code. Used during initial setup to verify network access to Azure services.

---

---

# Trial Comparison

| | Trial 1 | Trial 2A | Trial 2B | Trial 3A | Trial 3B | Trial 4 |
|--|--|--|--|--|--|--|
| **Script** | analyze_read_folder_to_md | azure_vision_translate_v2 | translate_single_pdf | batch_translate_images_folder | azure_batch_image_translation | invoice_extraction_test |
| **Services** | Doc Intelligence | Vision + Translator + Blob | Doc Translation + Blob | Doc Translation + Blob | Doc Translation + Blob | Doc Intelligence + Translator + OpenAI |
| **Input** | Any folder | Language subfolders | Single PDF | Any folder | Single folder | Language subfolders |
| **Output** | `.md` + `.json` | `.txt` per image + PDF | Translated PDF | Translated images | Translated images | `.json` per document |
| **Translation** | None | Text only | Document | Visual in-image | Visual in-image | Text only |
| **Layout preserved** | Metadata | No | Yes | Yes ✅ | Yes ✅ | No |
| **Structured fields** | No | No | No | No | No | Yes ✅ |
| **Parallel processing** | No | Yes (ThreadPool) | No | No (1 batch job) | No (loop) | Yes (ThreadPool) |
| **Skip existing** | Yes ✅ | No | No | No | Yes ✅ | No |
| **Hindi / Marathi** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Bengali / Telugu** | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ✅ |
| **Tamil / Kannada** | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |

---

# Key Learnings

1. **`find_dotenv()` searches upward, not into subdirectories.** The `.env` must live at
   the project root (where you run scripts from), not inside `src/`.

2. **The `prebuilt-read` model API changed its parameter name.** The `analyze_request=`
   argument was renamed to `body=` in newer SDK versions. Trial 4 needed this fix.

3. **Preview API is required for image translation.** The stable Document Translation API
   (`2024-05-01`) only processes PDFs. Image support requires `2025-12-01-preview`.

4. **Visual translation quality depends heavily on script complexity.** Devanagari (Hindi,
   Marathi) translates visually with high accuracy. Dravidian scripts (Tamil, Kannada)
   are much harder for the visual translation model.

5. **Document Intelligence OCR > Vision Read API for dense invoices.** Trial 4 switched
   to Document Intelligence for OCR because it handles table structure, small print, and
   mixed content more accurately than the Vision Read API used in Trial 2.

6. **GPT-4o handles imperfect translations gracefully.** Even when the Translator output
   has minor errors, GPT-4o correctly identifies and extracts invoice fields. If it returns
   non-JSON, the raw output is saved under `raw_extraction` so no data is lost.

7. **Temp containers must always be cleaned up.** All Trial 2 and 3 scripts use `finally`
   blocks to delete source and target containers regardless of success or failure —
   preventing orphaned blobs and unexpected storage costs.
