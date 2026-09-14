import json
import os

from datetime import datetime
from bs4 import BeautifulSoup
from difflib import SequenceMatcher


# extract content from html

def extract_content(html):
    soup = BeautifulSoup(html, "html.parser")

    content = {
        "headings": [],
        "paragraphs": [],
        "links": []
    }

    # Extract headings
    for heading in soup.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6"]
    ):
        text = heading.get_text(" ", strip=True)

        if text:
            content["headings"].append(text)

    # Extract paragraphs
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)

        if text:
            content["paragraphs"].append(text)

    # Extract links
    for link in soup.find_all("a"):
        text = link.get_text(" ", strip=True)
        href = link.get("href")

        content["links"].append({
            "text": text,
            "href": href
        })

    return content


# calculate text similarity

def similarity(old_text, new_text):
    return SequenceMatcher(
        None,
        old_text,
        new_text
    ).ratio()


# compare headings and paragraphs

def compare_text_list(
    old_items,
    new_items,
    threshold=0.6
):
    added = []
    removed = []
    modified = []

    matched_new = set()

    for old_item in old_items:

        best_match = None
        best_score = 0
        best_index = None

        for index, new_item in enumerate(new_items):

            if index in matched_new:
                continue

            score = similarity(
                old_item,
                new_item
            )

            if score > best_score:
                best_score = score
                best_match = new_item
                best_index = index

        # Same item but edited
        if best_score >= threshold:

            matched_new.add(best_index)

            if old_item != best_match:
                modified.append({
                    "old": old_item,
                    "new": best_match
                })

        # Old item disappeared
        else:
            removed.append(old_item)

    # Find new items that did not exist before
    for index, new_item in enumerate(new_items):

        if index not in matched_new:
            added.append(new_item)

    return {
        "added": added,
        "removed": removed,
        "modified": modified
    }


# compare links

def compare_links(
    old_links,
    new_links
):
    added = []
    removed = []
    modified = []

    old_by_text = {
        link["text"]: link
        for link in old_links
    }

    new_by_text = {
        link["text"]: link
        for link in new_links
    }

    # Check old links
    for text, old_link in old_by_text.items():

        # Link disappeared
        if text not in new_by_text:
            removed.append(old_link)

        else:
            new_link = new_by_text[text]

            # Same visible text,
            # but URL changed
            if (
                old_link.get("href")
                != new_link.get("href")
            ):
                modified.append({
                    "old": old_link,
                    "new": new_link
                })

    # Check newly added links
    for text, new_link in new_by_text.items():

        if text not in old_by_text:
            added.append(new_link)

    return {
        "added": added,
        "removed": removed,
        "modified": modified
    }


# compare all page content

def compare_content(
    old_data,
    new_data
):
    return {

        "headings": compare_text_list(
            old_data.get("headings", []),
            new_data.get("headings", [])
        ),

        "paragraphs": compare_text_list(
            old_data.get("paragraphs", []),
            new_data.get("paragraphs", [])
        ),

        "links": compare_links(
            old_data.get("links", []),
            new_data.get("links", [])
        )
    }


# storage setting

LATEST_FILE = "data/latest.json"

CHANGES_FOLDER = "data/changes"


# save curretn page snapshot

def save_snapshot(
    data,
    filename=LATEST_FILE
):
    folder = os.path.dirname(filename)

    if folder:
        os.makedirs(
            folder,
            exist_ok=True
        )

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=4,
            ensure_ascii=False
        )


# load previous snapshot

def load_snapshot(
    filename=LATEST_FILE
):
    if not os.path.exists(filename):
        return None

    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


# save the dectected changes to the file

def save_changes(
    changes,
    folder=CHANGES_FOLDER
):
    os.makedirs(
        folder,
        exist_ok=True
    )

    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S"
    )

    filename = os.path.join(
        folder,
        f"changes_{timestamp}.json"
    )

    with open(
        filename,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            changes,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(
        f"Changes saved to: {filename}"
    )


#check if there are any changes

def has_changes(changes):

    for category in changes.values():

        if category["added"]:
            return True

        if category["removed"]:
            return True

        if category["modified"]:
            return True

    return False


#print report of changes

def print_report(changes):

    if not has_changes(changes):

        print(
            "No changes detected."
        )

        return

    print(
        "\nCHANGES DETECTED"
    )

    print(
        "=" * 50
    )

    for category_name, category in changes.items():

        print(
            f"\n{category_name.upper()}"
        )

        print(
            "-" * 50
        )

        # Added
        if category["added"]:

            print(
                "\nADDED:"
            )

            for item in category["added"]:
                print(
                    "+",
                    item
                )

        # Removed
        if category["removed"]:

            print(
                "\nREMOVED:"
            )

            for item in category["removed"]:
                print(
                    "-",
                    item
                )

        # Modified
        if category["modified"]:

            print(
                "\nMODIFIED:"
            )

            for item in category["modified"]:

                print(
                    "\nOLD:"
                )

                print(
                    item["old"]
                )

                print(
                    "\nNEW:"
                )

                print(
                    item["new"]
                )


#change detector

def run_change_detector(html):

    # Convert raw HTML
    # into clean structured data
    new_data = extract_content(html)

    # Load previous version
    old_data = load_snapshot()

    # First ever run
    if old_data is None:

        print(
            "No previous version found."
        )

        print(
            "Saving this version as the baseline."
        )

        save_snapshot(
            new_data
        )

        return

    # Compare old vs new
    changes = compare_content(
        old_data,
        new_data
    )

    # Show result
    print_report(
        changes
    )

    # Keep change history
    if has_changes(changes):

        save_changes(
            changes
        )

    # Replace latest baseline
    # with newest version
    save_snapshot(
        new_data
    )