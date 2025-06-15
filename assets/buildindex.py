import os
from bs4 import BeautifulSoup

folder_path = r"C:\Users\willo\OneDrive\Documents\GitHub\fics\ficArchive"
output_file = "index.html"

html_links = []

for filename in os.listdir(folder_path):
    if filename.endswith(".html"):
        full_path = os.path.join(folder_path, filename)
        with open(full_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "html.parser")
            title = soup.title.string if soup.title else filename
            html_links.append(f'<li><a href="{filename}">{title}</a></li>')

# Create the index.html content
index_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Index of HTML Files</title>
    <link rel="stylesheet" href="assets/darkMode.css">
</head>
<body>
    <h1>Index of HTML Files</h1>
    <ul>
        {''.join(html_links)}
    </ul>
</body>
</html>
"""

# Write to index.html
with open(os.path.join(folder_path, output_file), "w", encoding="utf-8") as f:
    f.write(index_content)

print(f"Index created: {os.path.join(folder_path, output_file)}")
