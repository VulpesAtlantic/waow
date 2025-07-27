import os
from bs4 import BeautifulSoup

# Folder paths
input_folder = "."
output_folder = os.path.join(input_folder, "converted_fics")
os.makedirs(output_folder, exist_ok=True)

def simplify_and_style_file(file_path, output_path):
    # Load HTML file
    with open(file_path, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "lxml")

    # Insert responsive dark mode CSS into the head
    dark_mode_css = """
    <style>
      body {
        background-color: #121212;
        color: #e0e0e0;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        line-height: 1.6;
        padding: 1rem;
        margin: auto;
        max-width: 90vw;
        font-size: 1rem;
      }
      h1, h2, h3 {
        color: #ffffff;
      }
      a {
        color: #bb86fc;
      }
      blockquote {
        border-left: 4px solid #bb86fc;
        padding-left: 1rem;
        color: #cccccc;
      }
      @media (min-width: 768px) {
        body {
          padding: 2rem;
          max-width: 800px;
          font-size: 1.1rem;
        }
      }
    </style>
    """

    if soup.head:
        soup.head.append(BeautifulSoup(dark_mode_css, "lxml"))

    # Simplify <body>: remove all tag attributes
    if soup.body:
        for tag in soup.body.find_all(True):
            tag.attrs = {}

    # Save output
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(soup.prettify())


# Process all HTML files
for filename in os.listdir(input_folder):
    if filename.endswith(".html"):
        input_path = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)
        simplify_and_style_file(input_path, output_path)
        print(f"Converted: {filename}")
