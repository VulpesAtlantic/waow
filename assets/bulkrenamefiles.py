import os
import re

def to_camel_case(s):
    parts = s.split('_')
    return parts[0].lower() + ''.join(p.capitalize() for p in parts[1:])

def rename_files_in_directory(directory):
    for filename in os.listdir(directory):
        old_path = os.path.join(directory, filename)
        if os.path.isfile(old_path): 
            name, ext = os.path.splitext(filename)
            new_name = to_camel_case(name) + ext
            new_path = os.path.join(directory, new_name)

            # Rename only if the name changes
            if old_path != new_path:
                print(f"Renaming: {filename} -> {new_name}")
                os.rename(old_path, new_path)

# Set the target directory here
target_directory = ".."
rename_files_in_directory(target_directory)