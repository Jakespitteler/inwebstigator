import json

from sqlalchemy import select

from app.db.core import SessionLocal
from app.db.schema import DBWebsite


def compare_and_update_links(json_file: str):
    with open(json_file, "r", encoding="utf-8") as file:
        current_data = json.load(file)

    session = SessionLocal()

    try:
        for website_url, current_links in current_data.items():

            website = session.scalar(
                select(DBWebsite).where(DBWebsite.url == website_url)
            )

            # First time seeing this website
            if website is None:
                website = DBWebsite(
                    url=website_url,
                    internal_links=current_links
                )

                session.add(website)

                print(f"New website stored: {website_url}")
                continue

            old_links = set(website.internal_links or [])
            new_links = set(current_links)

            added = new_links - old_links
            removed = old_links - new_links

            print(f"\nWebsite: {website_url}")

            print("Added:")
            for link in added:
                print(link)

            print("Removed:")
            for link in removed:
                print(link)

            # Replace old list with current list
            website.internal_links = current_links

        session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


if __name__ == "__main__":
    compare_and_update_links("app/db/tmprqjont0j.json")