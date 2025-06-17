import os
from bs4 import BeautifulSoup

folder_path = r"C:\Users\willo\OneDrive\Documents\GitHub\fics\ficArchive"
output_file = "index.html"

rows = []

for filename in os.listdir(folder_path):
    if filename.endswith(".html") and filename != output_file:
        full_path = os.path.join(folder_path, filename)
        with open(full_path, "r", encoding="utf-8") as f:
            soup = BeautifulSoup(f, "html.parser")

            title = soup.title.string.strip() if soup.title and soup.title.string else filename
            author_tag = soup.find("meta", attrs={"name": "author"})
            ship_tag = soup.find("meta", attrs={"name": "ship"})

            author = author_tag["content"].strip() if author_tag and "content" in author_tag.attrs else "Unknown"
            ship = ship_tag["content"].strip() if ship_tag and "content" in ship_tag.attrs else "Unknown"

            rows.append(f"""
                <tr>
                    <td><a href="{filename}">{title}</a></td>
                    <td>{author}</td>
                    <td>{ship}</td>
                </tr>
            """)

# Sortable table JavaScript (basic)
sortable_script = """
<script>
document.addEventListener('DOMContentLoaded', function () {
    const getCellValue = (tr, idx) => tr.children[idx].innerText || tr.children[idx].textContent;

    const comparer = (idx, asc) => (a, b) => 
        ((v1, v2) => 
            v1 !== '' && v2 !== '' && !isNaN(v1) && !isNaN(v2) 
                ? v1 - v2 
                : v1.toString().localeCompare(v2)
        )(getCellValue(asc ? a : b, idx), getCellValue(asc ? b : a, idx));

    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const table = th.closest('table');
            const tbody = table.querySelector('tbody');
            Array.from(tbody.querySelectorAll('tr'))
                .sort(comparer(Array.from(th.parentNode.children).indexOf(th), th.asc = !th.asc))
                .forEach(tr => tbody.appendChild(tr));
        });
    });
});
</script>
"""

# Create the index.html content
index_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Fanfic Archive Index</title>
    <link rel="stylesheet" href="assets/darkMode.css">
    <style>
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            padding: 8px;
            border: 1px solid #666;
        }}
        th.sortable {{
            cursor: pointer;
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <h1>Fanfic Archive</h1>
    <center>
        <p>These are not my fics. These are just some I don't want to lose.</p>
    </center>
    <table>
        <thead>
            <tr>
                <th class="sortable">Title</th>
                <th class="sortable">Author</th>
                <th class="sortable">Ship</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
    {sortable_script}
</body>
</html>
"""

# Write to index.html
with open(os.path.join(folder_path, output_file), "w", encoding="utf-8") as f:
    f.write(index_content)

print(f"Index created: {os.path.join(folder_path, output_file)}")
