import markdown
import os

# Input and output file paths
input_file = "./unraveled.md"
output_file = "./unraveled.html"

# Read Markdown content
with open(input_file, "r", encoding="utf-8") as f:
    md_text = f.read()

# Convert to HTML
html = markdown.markdown(md_text)

# Optional: wrap in basic HTML structure
html_full = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{os.path.splitext(os.path.basename(input_file))[0]}</title>
    <link rel="stylesheet" href="assets/darkMode.css">
</head>
<body>
{html}
</body>
</html>"""

# Write to HTML file
with open(output_file, "w", encoding="utf-8") as f:
    f.write(html_full)

print(f"Converted {input_file} to {output_file}")
