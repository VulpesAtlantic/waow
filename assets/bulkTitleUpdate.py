import os
import csv
from bs4 import BeautifulSoup

def extract_titles_and_meta(directory, output_csv):
    rows = []

    for filename in os.listdir(directory):
        if filename.endswith(".html"):
            file_path = os.path.join(directory, filename)
            with open(file_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f, "lxml")

                title = soup.title.string.strip() if soup.title and soup.title.string else ""
                meta_tag = soup.find("meta", attrs={"name": "author"})
                author = meta_tag["content"].strip() if meta_tag and "content" in meta_tag.attrs else ""

                rows.append({
                    "filename": filename,
                    "title": title,
                    "author": author
                })

    # Write to CSV
    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "title", "author"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Extracted data written to {output_csv}")

# Example usage
extract_titles_and_meta(directory=".", output_csv="html_metadata.csv")
