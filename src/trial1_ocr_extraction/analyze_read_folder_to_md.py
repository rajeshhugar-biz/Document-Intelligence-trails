import os
import sys
import json
from pathlib import Path
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
from dotenv import find_dotenv, load_dotenv
from azure.core.exceptions import HttpResponseError

load_dotenv(find_dotenv())

ENDPOINT = os.environ["DOCUMENTINTELLIGENCE_ENDPOINT"]
KEY      = os.environ["DOCUMENTINTELLIGENCE_API_KEY"]

# All file types supported by prebuilt-read
SUPPORTED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".tiff", ".tif",
    ".bmp", ".heif", ".docx", ".xlsx", ".pptx", ".html"
}


def format_bounding_box(bounding_box) -> str:
    if not bounding_box:
        return "N/A"
    return "[{}, {}], [{}, {}], [{}, {}], [{}, {}]".format(
        bounding_box[0], bounding_box[1],
        bounding_box[2], bounding_box[3],
        bounding_box[4], bounding_box[5],
        bounding_box[6], bounding_box[7],
    )


def analyze_file(client: DocumentIntelligenceClient, file_path: Path) -> object:
    """Submit a single file to prebuilt-read and return the result."""
    with open(file_path, "rb") as f:
        poller = client.begin_analyze_document(
            "prebuilt-read",
            body=f,
            content_type="application/octet-stream",
        )
    return poller.result()


def build_markdown(result, source_label: str) -> str:
    """Convert a prebuilt-read result into a Markdown string."""
    md = []

    md.append("# Document Analysis Report")
    md.append(f"\n**Source:** `{source_label}`\n")

    # ── Full extracted text ───────────────────────────────────────────────────
    md.append("---\n")
    md.append("## Full Extracted Text\n")
    md.append(result.content if result.content else "_No content extracted._")
    md.append("\n")

    # ── Content style (handwritten vs printed) ────────────────────────────────
    md.append("---\n")
    md.append("## Content Style\n")
    if result.styles:
        for style in result.styles:
            style_type = "Handwritten" if style.is_handwritten else "Printed / Typed"
            confidence = f"{style.confidence:.2f}" if style.confidence is not None else "N/A"
            md.append(f"- **Style:** {style_type} | **Confidence:** {confidence}")
    else:
        md.append("_No style information available._")
    md.append("\n")

    # ── Per-page breakdown ────────────────────────────────────────────────────
    md.append("---\n")
    md.append("## Page-by-Page Breakdown\n")

    for page in result.pages:
        md.append(f"### Page {page.page_number}\n")
        md.append(f"- **Dimensions:** {page.width} x {page.height} ({page.unit})\n")

        if page.lines:
            md.append("#### Lines\n")
            md.append("| Line # | Content | Bounding Box |")
            md.append("|--------|---------|--------------|")
            for idx, line in enumerate(page.lines):
                bbox    = format_bounding_box(line.polygon)
                content = line.content.replace("|", "\\|")
                md.append(f"| {idx + 1} | {content} | {bbox} |")
            md.append("")

        if page.words:
            md.append("#### Words\n")
            md.append("| Word | Confidence |")
            md.append("|------|------------|")
            for word in page.words:
                confidence = f"{word.confidence:.4f}" if word.confidence is not None else "N/A"
                w = word.content.replace("|", "\\|")
                md.append(f"| {w} | {confidence} |")
            md.append("")

    return "\n".join(md)


def process_folder(input_dir: Path, output_dir: Path, skip_existing: bool = True) -> None:
    """
    Recursively walk `input_dir`, run prebuilt-read on every supported file,
    and save a .md report under `output_dir` — mirroring the original
    subfolder structure.

    Example:
        input_dir/
            hindi/
                page1.png
                page2.jpg
            bengali/
                doc.pdf

        output_dir/
            hindi/
                page1_read_analysis.md
                page2_read_analysis.md
            bengali/
                doc_read_analysis.md
    """
    if not input_dir.exists():
        print(f"Error: Input folder does not exist: {input_dir}")
        sys.exit(1)

    # ── Discover all supported files recursively ──────────────────────────────
    all_files = [
        Path(root) / file
        for root, _, files in os.walk(input_dir)
        for file in files
        if Path(file).suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not all_files:
        print(f"No supported files found in: {input_dir}")
        print(f"Supported extensions: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        sys.exit(0)

    print(f"Found {len(all_files)} file(s) under '{input_dir}'")
    print(f"Output folder: '{output_dir}'\n")

    client = DocumentIntelligenceClient(
        endpoint=ENDPOINT, credential=AzureKeyCredential(KEY)
    )

    success_count = 0
    skip_count    = 0
    fail_count    = 0

    for idx, file_path in enumerate(all_files, start=1):
        # Mirror the subfolder structure in the output directory
        relative   = file_path.relative_to(input_dir)
        output_md   = output_dir / relative.parent / f"{file_path.stem}_read_analysis.md"
        output_json = output_dir / relative.parent / f"{file_path.stem}_read_analysis.json"

        print(f"[{idx}/{len(all_files)}] {relative}")

        # Skip if already processed
        if skip_existing and output_md.exists() and output_json.exists():
            print(f"  → Skipping (outputs already exist): {output_md.name}\n")
            skip_count += 1
            continue

        try:
            print(f"  → Analyzing...")
            result = analyze_file(client, file_path)

            md_content = build_markdown(result, source_label=str(relative))

            output_md.parent.mkdir(parents=True, exist_ok=True)
            output_md.write_text(md_content, encoding="utf-8")
            
            # Save JSON content
            try:
                res_dict = result.as_dict()
            except AttributeError:
                res_dict = result
                
            json_content = json.dumps(res_dict, default=str, indent=2, ensure_ascii=False)
            output_json.write_text(json_content, encoding="utf-8")
            
            print(f"  → Saved: {output_md.name} & {output_json.name}\n")
            success_count += 1

        except HttpResponseError as e:
            print(f"  → Service error: {e.error or e.message}\n")
            fail_count += 1
        except Exception as e:
            print(f"  → Unexpected error: {e}\n")
            fail_count += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=" * 55)
    print(f"Done.  Success: {success_count} | Skipped: {skip_count} | Failed: {fail_count}")
    print(f"Reports saved to: {output_dir.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python analyze_read_folder_to_md.py <input_folder> <output_folder>")
        print("Example: python analyze_read_folder_to_md.py ../test_data ../output_md")
        sys.exit(1)

    input_dir  = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])

    process_folder(input_dir, output_dir)
