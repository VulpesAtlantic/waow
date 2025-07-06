import os
from bs4 import BeautifulSoup

folder_path = r"."  # <-- Update this to your folder path

for filename in os.listdir(folder_path):
    if filename.endswith(".html"):
        file_path = os.path.join(folder_path, filename)

        try:
            with open(file_path, "r", encoding="utf-8") as file:
                soup = BeautifulSoup(file, "html.parser")

            head = soup.head
            if not head:
                continue  # skip files with no <head>

            # --- Handle <title>, author, universe ---
            if soup.title and soup.title.string:
                parts = soup.title.string.split(" - ")
                if len(parts) == 3:
                    title, author, universe = parts

                    # Set <title>
                    soup.title.string = title.strip()

                    # Add/update <meta name="author">
                    author_meta = head.find("meta", attrs={"name": "author"})
                    if author_meta:
                        author_meta["content"] = author.strip()
                    else:
                        new_author_meta = soup.new_tag("meta", attrs={
                            "name": "author",
                            "content": author.strip()
                        })
                        head.append(new_author_meta)

                    # Add/update <meta name="ship">
                    ship_meta = head.find("meta", attrs={"name": "ship"})
                    if ship_meta:
                        ship_meta["content"] = "Agatha Harkness/Rio Vidal"
                    else:
                        new_ship_meta = soup.new_tag("meta", attrs={
                            "name": "ship",
                            "content": "Agatha Harkness/Rio Vidal"
                        })
                        head.append(new_ship_meta)

            # --- Replace <style> with <link> ---
            for style_tag in head.find_all("style"):
                style_tag.decompose()

            if not head.find("link", href="assets/darkMode.css"):
                link_tag = soup.new_tag("link", rel="stylesheet", href="assets/darkMode.css")
                head.append(link_tag)

            # --- Extract rating and create <meta name="rating"> ---
            rating_dt = soup.find("dt", string="Rating:")
            if rating_dt:
                rating_dd = rating_dt.find_next_sibling("dd")
                if rating_dd:
                    rating_a = rating_dd.find("a")
                    if rating_a and rating_a.string:
                        rating_text = rating_a.string.strip()
                        rating_meta = head.find("meta", attrs={"name": "rating"})
                        if rating_meta:
                            rating_meta["content"] = rating_text
                        else:
                            new_rating_meta = soup.new_tag("meta", attrs={
                                "name": "rating",
                                "content": rating_text
                            })
                            head.append(new_rating_meta)

            # --- Save file ---
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(str(soup))

            print(f"✅ Updated: {filename}")

        except Exception as e:
            print(f"❌ Error in {filename}: {e}")
