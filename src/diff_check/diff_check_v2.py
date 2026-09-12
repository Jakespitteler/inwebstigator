import re
import json
import httpx

from pathlib import Path
from datetime import datetime
from difflib import SequenceMatcher
from bs4 import BeautifulSoup, Comment


# get HTML from a url

def get_html(url):
    response = httpx.get(
        url,
        timeout=30,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    # THIS is the HTML
    return response.text


# celan the HTML and maek it into text

def extract_content(html):

    soup = BeautifulSoup(html, "html.parser")

    # Remove unwanted HTML elements
    for element in soup([
        "script",
        "style",
        "noscript",
        "template",
        "svg"
    ]):
        element.decompose()

    # Remove HTML comments
    for comment in soup.find_all(
        string=lambda text: isinstance(text, Comment)
    ):
        comment.extract()

    # Remove text that contains literal HTML
    for text_node in soup.find_all(string=True):

        if "<" in text_node and ">" in text_node:

            if re.search(
                r"<\s*/?\s*[a-zA-Z][^>]*>",
                text_node
            ):
                text_node.extract()

    content = {
        "headings": [],
        "paragraph_details": [],
        "links": []
    }

# Find the main page content
    main_content = soup.find("main")

    if main_content is None:
        main_content = soup

    

    current_section = "No heading"

    for element in main_content.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]
    ):
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

            content["paragraph_details"].append({
                "section": current_section,
                "type": "paragraph",
                "text": text
            })
        elif element.name == "li":

            content["paragraph_details"].append({
                "section": current_section,
                "type": "list_item",
                "text": text
            })

    # Capture Last updated date
    last_updated_heading = main_content.find(
        lambda tag:
            tag.name in ["h1", "h2", "h3", "h4", "h5", "h6"]
            and tag.get_text(" ", strip=True).lower() == "last updated:"
    )

    if last_updated_heading:

        next_element = last_updated_heading.find_next_sibling()

        if next_element:
            date_text = next_element.get_text(" ", strip=True)
        else:
            date_text = None

        if date_text:

            date_text = " ".join(date_text.split())

            content["paragraph_details"].append({
                "section": "Last updated:",
                "type": "last_updated",
                "text": date_text
            })
        
    # Links
    # -------------------------

    for link in main_content.find_all("a", href=True):

        if link.find_parent(["nav", "aside"]):
            continue

        href = link["href"]

        if href:
            content["links"].append(href)


    return content

def save_snapshot(content):
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            content,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(f"Snapshot saved to: {SNAPSHOT_FILE}")

def load_snapshot():
    if not SNAPSHOT_FILE.exists():
        return None

    with open(SNAPSHOT_FILE, "r", encoding="utf-8") as file:
        return json.load(file)

def save_previous_snapshot(content):
    with open(
        PREVIOUS_SNAPSHOT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            content,
            file,
            indent=4,
            ensure_ascii=False
        )

def save_change_history(results):

    # If nothing changed, don't add anything
    if (
        not results["changed"]
        and not results["added"]
        and not results["removed"]
    ):
        return

    # Load existing history if there is one
    if CHANGE_HISTORY_FILE.exists():
        with open(
            CHANGE_HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            history = json.load(file)

    else:
        history = []

    change_record = {
        "detected_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "url": URL,
        "changed": results["changed"],
        "added": results["added"],
        "removed": results["removed"]
    }

    history.append(change_record)

    with open(
        CHANGE_HISTORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            history,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(f"Change history saved to: {CHANGE_HISTORY_FILE}")

def get_paragraph_details(content):

    # New snapshot format
    if "paragraph_details" in content:
        return content["paragraph_details"]

    # Fallback for older snapshots
    return [
        {
            "section": "Unknown section",
            "text": paragraph
        }
        for paragraph in content["paragraphs"]
    ]
    
def compare_paragraphs(old_content, new_content):

    old_details = get_paragraph_details(old_content)
    new_details = get_paragraph_details(new_content)

    old_paragraphs = [
        item["text"]
        for item in old_details
    ]

    new_paragraphs = [
        item["text"]
        for item in new_details
    ]

    # Section + paragraph text
    old_items = [
        (
            item["section"],
            item.get("type", "paragraph"),
            item["text"]
        )
        for item in old_details
    ]

    new_items = [
        (
            item["section"],
            item.get("type", "paragraph"),
            item["text"]
        )
        for item in new_details
    ]

    results = {
        "changed": [],
        "added": [],
        "removed": []
    }

    matcher = SequenceMatcher(
        None,
        old_items,
        new_items,
        autojunk=False
    )

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():

        # Nothing changed
        if tag == "equal":
            continue


        # Paragraph was removed
        elif tag == "delete":

            for index in range(i1, i2):

                results["removed"].append({
                    "section": old_details[index]["section"],
                    "text": old_details[index]["text"]
                })


        # Paragraph was added
        elif tag == "insert":

            for index in range(j1, j2):

                results["added"].append({
                    "section": new_details[index]["section"],
                    "text": new_details[index]["text"]
                })

        # Something in this area changed
        elif tag == "replace":

            old_block = old_paragraphs[i1:i2]
            new_block = new_paragraphs[j1:j2]

            pair_count = min(
                len(old_block),
                len(new_block)
            )

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

                similarity = SequenceMatcher(
                    None,
                    old_paragraph,
                    new_paragraph,
                    autojunk=False
                ).ratio()

                # Similar enough → treat as edited paragraph
                if similarity >= 0.60:

                    results["changed"].append({
                        "old_section": old_section,
                        "new_section": new_section,

                        "old_type": old_type,
                        "new_type": new_type,

                        "old": old_paragraph,
                        "new": new_paragraph,

                        "similarity": similarity
                        
                    })

                # Very different → treat as removal + addition
                else:

                    results["removed"].append({
                        "section": old_section,
                        "text": old_paragraph
                    })

                    results["added"].append({
                        "section": new_section,
                        "text": new_paragraph
                    })


            # Extra old paragraphs = removed
            for offset in range(pair_count, len(old_block)):

                index = i1 + offset

                results["removed"].append({
                    "section": old_details[index]["section"],
                    "text": old_details[index]["text"]
                })


            # Extra new paragraphs = added
            for offset in range(pair_count, len(new_block)):

                index = j1 + offset

                results["added"].append({
                    "section": new_details[index]["section"],
                    "text": new_details[index]["text"]
                })

    return results




# =========================================================
# 4. TEST 
# =========================================================

URL = "https://www.teqsa.gov.au/how-we-regulate/public-reporting"

SNAPSHOT_FILE = Path(__file__).parent / "snapshot.json"
PREVIOUS_SNAPSHOT_FILE = Path(__file__).parent / "previous_snapshot.json"
CHANGE_HISTORY_FILE = Path(__file__).parent / "change_history.json"



def diff_check(url):

    print("Scraping website...")

    html = get_html(url)

    new_content = extract_content(html)

    old_snapshot = load_snapshot()

    # rest of your code...


    # First ever run
    if old_snapshot is None:

        print("No previous snapshot found.")
        print("Creating first snapshot...")

        save_snapshot(new_content)

        return


    print("Previous snapshot found.")
    print("Comparing old and new content...")


    results = compare_paragraphs(
        old_snapshot,
        new_content
    )


    print("\n==========================")
    print("WEBSITE CHANGES")
    print("==========================")


    # Nothing changed
    if (
        not results["changed"]
        and not results["added"]
        and not results["removed"]
    ):

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

        print(
            f"\nSimilarity: "
            f"{change['similarity']:.2%}"
        )


    # Added paragraphs
    for paragraph in results["added"]:

        print("\n--------------------------")
        print("ADDED PARAGRAPH")
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
    has_changes = (
        results["changed"]
        or results["added"]
        or results["removed"]
    )


    # If there was a change:
    if has_changes:

        # Keep a permanent record of the change
        save_change_history(results)

        # Keep a copy of the OLD website
        save_previous_snapshot(old_snapshot)


    # Whether there was a change or not,
    # save the newest website as the current snapshot
    save_snapshot(new_content)


if __name__ == "__main__":

    test_url = "https://www.teqsa.gov.au/how-we-regulate/public-reporting"

    diff_check(test_url)