import asyncio
import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from urllib.parse import urlparse
from pathlib import Path

import httpx2
from bs4 import BeautifulSoup, Comment

from app.backend.web_scraper.utils import fetch_content_from_url

# get HTML from a url

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"  # noqa: E501
}
SECONDS_TIMEOUT = 20

# celan the HTML and maek it into text


def extract_content(html):

    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted HTML elements
    for element in soup(["script", "style", "noscript", "template", "svg"]):
        element.decompose()

    # Remove HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Remove text that contains literal HTML
    for text_node in soup.find_all(string=True):
        if "<" in text_node and ">" in text_node and re.search(r"<\s*/?\s*[a-zA-Z][^>]*>", text_node):
            text_node.extract()

    content = {"headings": [], "paragraph_details": [], "links": []}

    # Find the main page content
    main_content = soup.find("main")

    if main_content is None:
        main_content = soup

    current_section = "No heading"

    for element in main_content.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"]):
        # Ignore navigation and sidebar content
        if element.find_parent(["nav", "aside"]):
            continue

        text = element.get_text(" ", strip=True)
        # Clean whitespace
        text = " ".join(text.split())

        if not text:
            continue

        # If we find a heading,
        # remember it as the current section
        if element.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            content["headings"].append(text)

            current_section = text

        # If we find a paragraph,
        # save both its text and its section
        elif element.name == "p":
            content["paragraph_details"].append({"section": current_section, "type": "paragraph", "text": text})

        elif element.name == "li":
            content["paragraph_details"].append({"section": current_section, "type": "list_item", "text": text})

        elif element.name == "tr":

            cells = element.find_all(["th", "td"])

            row_text = " | ".join(
                cell.get_text(" ", strip=True)
                for cell in cells
            )

            if row_text:
                content["paragraph_details"].append({
                    "section": current_section,
                    "type": "table_row",
                    "text": row_text
                })
        

    # Capture Last updated date
    last_updated_heading = main_content.find(
        lambda tag: (
            tag.name in ["h1", "h2", "h3", "h4", "h5", "h6"]
            and tag.get_text(" ", strip=True).lower() == "last updated:"
        )
    )

    if last_updated_heading:
        next_element = last_updated_heading.find_next_sibling()

        date_text = next_element.get_text(" ", strip=True) if next_element else None

        if date_text:
            date_text = " ".join(date_text.split())

            content["paragraph_details"].append({"section": "Last updated:", "type": "last_updated", "text": date_text})

    # Links
    # -------------------------

    for link in main_content.find_all("a", href=True):
        if link.find_parent(["nav", "aside"]):
            continue

        href = link["href"]

        if href:
            content["links"].append(href)

    return content

def make_snapshot_name(url):

    parsed = urlparse(url)

    name = parsed.netloc + parsed.path

    name = re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        name
    )

    return name + ".json"


def get_snapshot_file(url):

    snapshot_dir = Path(__file__).parent / "snapshots"

    snapshot_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return snapshot_dir / make_snapshot_name(url)

def get_previous_snapshot_file(url):

    previous_dir = Path(__file__).parent / "previous_snapshots"

    previous_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return previous_dir / make_snapshot_name(url)

def save_snapshot(content, url):

    snapshot_file = get_snapshot_file(url)

    with open(
        snapshot_file,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump(
            content,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(f"Snapshot saved to: {snapshot_file}")


def load_snapshot(url):

    snapshot_file = get_snapshot_file(url)

    if not snapshot_file.exists():
        return None

    with open(
        snapshot_file,
        "r",
        encoding="utf-8"
    ) as file:
        return json.load(file)


def save_previous_snapshot(content, url):

    previous_snapshot_file = get_previous_snapshot_file(url)

    with open(
        previous_snapshot_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            content,
            file,
            indent=4,
            ensure_ascii=False
        )

def get_change_history_file(url):

    history_dir = Path(__file__).parent / "change_history"

    history_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return history_dir / make_snapshot_name(url)

def save_change_history(results, url):
    
    # If nothing changed, don't add anything
    if not results["changed"] and not results["added"] and not results["removed"]:
        return

    change_history_file = get_change_history_file(url)

    # Load existing history if there is one
    if change_history_file.exists():
        with open(change_history_file, encoding="utf-8") as file:
            history = json.load(file)

    else:
        history = []

    change_record = {
        "detected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "url": url,
        "changed": results["changed"],
        "added": results["added"],
        "removed": results["removed"]
    }

    history.append(change_record)

    with open(change_history_file, "w", encoding="utf-8") as file:
        json.dump(history, file, indent=4, ensure_ascii=False)

    print(f"Change history saved to: {change_history_file}")


def get_paragraph_details(content):

    # New snapshot format
    if "paragraph_details" in content:
        return content["paragraph_details"]

    # Fallback for older snapshots
    return [{"section": "Unknown section", "text": paragraph} for paragraph in content["paragraphs"]]


def compare_paragraphs(old_content, new_content):

    old_details = get_paragraph_details(old_content)
    new_details = get_paragraph_details(new_content)

    old_paragraphs = [item["text"] for item in old_details]

    new_paragraphs = [item["text"] for item in new_details]

    # Section + paragraph text
    old_items = [(item["section"], item.get("type", "paragraph"), item["text"]) for item in old_details]

    new_items = [(item["section"], item.get("type", "paragraph"), item["text"]) for item in new_details]

    results = {"changed": [], "added": [], "removed": []}

    matcher = SequenceMatcher(None, old_items, new_items, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        # Nothing changed
        if tag == "equal":
            continue

        # Paragraph was removed
        elif tag == "delete":
            for index in range(i1, i2):
                results["removed"].append(
                    {"section": old_details[index]["section"], "text": old_details[index]["text"]}
                )

        # Paragraph was added
        elif tag == "insert":
            for index in range(j1, j2):
                results["added"].append({"section": new_details[index]["section"], "text": new_details[index]["text"]})

        # Something in this area changed
        elif tag == "replace":
            old_block = old_paragraphs[i1:i2]
            new_block = new_paragraphs[j1:j2]

            pair_count = min(len(old_block), len(new_block))

            # Compare old/new paragraphs in the same area
            for index in range(pair_count):
                old_paragraph = old_block[index]
                new_paragraph = new_block[index]

                # Get the section for each paragraph
                old_section = old_details[i1 + index]["section"]
                new_section = new_details[j1 + index]["section"]

                # Get the content type for each paragraph
                old_type = old_details[i1 + index].get("type", "paragraph")
                new_type = new_details[j1 + index].get("type", "paragraph")

                similarity = SequenceMatcher(None, old_paragraph, new_paragraph, autojunk=False).ratio()

                # Similar enough → treat as edited paragraph
                if similarity >= 0.60:
                    results["changed"].append(
                        {
                            "old_section": old_section,
                            "new_section": new_section,
                            "old_type": old_type,
                            "new_type": new_type,
                            "old": old_paragraph,
                            "new": new_paragraph,
                            "similarity": similarity,
                        }
                    )

                # Very different → treat as removal + addition
                else:
                    results["removed"].append({"section": old_section, "text": old_paragraph})

                    results["added"].append({"section": new_section, "text": new_paragraph})

            # Extra old paragraphs = removed
            for offset in range(pair_count, len(old_block)):
                index = i1 + offset

                results["removed"].append(
                    {"section": old_details[index]["section"], "text": old_details[index]["text"]}
                )

            # Extra new paragraphs = added
            for offset in range(pair_count, len(new_block)):
                index = j1 + offset

                results["added"].append({"section": new_details[index]["section"], "text": new_details[index]["text"]})

    return results


# =========================================================
# 4. TEST
# =========================================================



SNAPSHOT_FILE = Path(__file__).parent / "snapshot.json"
PREVIOUS_SNAPSHOT_FILE = Path(__file__).parent / "previous_snapshot.json"



async def diff_check(client, url):

    print("Scraping website...")

    html, url = await fetch_content_from_url(client, url)  # returned url accounts for redirects

    new_content = extract_content(html)

    old_snapshot = load_snapshot(url)

    # rest of your code...

    # First ever run
    if old_snapshot is None:
        print("No previous snapshot found.")
        print("Creating first snapshot...")

        save_snapshot(new_content, url)

        return

    print("Previous snapshot found.")
    print("Comparing old and new content...")

    results = compare_paragraphs(old_snapshot, new_content)

    print("\n==========================")
    print("WEBSITE CHANGES")
    print("==========================")

    # Nothing changed
    if not results["changed"] and not results["added"] and not results["removed"]:
        print("\nNo paragraph changes detected.")

    # Changed content
    for change in results["changed"]:
        print("\n--------------------------")
        print("CHANGED CONTENT")
        print("--------------------------")

        # Section
        if change["old_section"] == change["new_section"]:
            print("\nSECTION:")
            print(change["new_section"])

        else:
            print("\nOLD SECTION:")
            print(change["old_section"])

            print("\nNEW SECTION:")
            print(change["new_section"])

        # Content type
        if change["old_type"] == change["new_type"]:
            print("\nTYPE:")
            print(change["new_type"])

        else:
            print("\nOLD TYPE:")
            print(change["old_type"])

            print("\nNEW TYPE:")
            print(change["new_type"])

        # Text
        print("\nOLD:")
        print(change["old"])

        print("\nNEW:")
        print(change["new"])

        print(f"\nSimilarity: {change['similarity']:.2%}")

    # Added paragraphs
    for paragraph in results["added"]:
        print("\n--------------------------")
        print("ADDED CONTENT")
        print("--------------------------")

        print("\nSECTION:")
        print(paragraph["section"])

        print("\nTEXT:")
        print(paragraph["text"])

        # Removed paragraphs
    for paragraph in results["removed"]:
        print("\n--------------------------")
        print("REMOVED PARAGRAPH")
        print("--------------------------")

        print("\nSECTION:")
        print(paragraph["section"])

        print("\nTEXT:")
        print(paragraph["text"])

    # Check whether anything changed
    has_changes = results["changed"] or results["added"] or results["removed"]

    # If there was a change:
    if has_changes:
        # Keep a permanent record of the change
        save_change_history(results,url)

        # Keep a copy of the OLD website
        save_previous_snapshot(old_snapshot, url)

    # Whether there was a change or not,
    # save the newest website as the current snapshot
    save_snapshot(new_content, url)



async def main():

    test_urls = ["https://www.teqsa.gov.au/national-register","https://www.teqsa.gov.au/how-we-regulate/public-reporting"]

    async with httpx2.AsyncClient(headers=HEADERS, timeout=SECONDS_TIMEOUT) as client:

        for url in test_urls:
            print("\nChecking:", url)
            await diff_check(client, url)


if __name__ == "__main__":
    asyncio.run(main())
