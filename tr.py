import os
import zipfile
from pathlib import Path


def zip_current_folder():
    # Get the folder where this script is located
    script_dir = Path(__file__).parent.resolve()
    folder_name = script_dir.name
    # Save the zip INSIDE the folder to avoid permission issues on C:\ root
    zip_filename = script_dir / f"{folder_name}.zip"

    print(f"Zipping folder: {script_dir}")
    print(f"Output zip:     {zip_filename}")

    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(script_dir):
            root_path = Path(root)
            for file in files:
                file_path = root_path / file
                # Skip the zip file itself if it somehow ends up inside
                if file_path == zip_filename:
                    continue
                # Skip this script itself (optional — remove if you want it included)
                if file_path == Path(__file__).resolve():
                    continue
                arcname = file_path.relative_to(script_dir)
                zipf.write(file_path, arcname)

    print(f"\nDone! Created: {zip_filename}")


if __name__ == "__main__":
    zip_current_folder()