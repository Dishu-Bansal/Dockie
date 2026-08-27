import PyInstaller.__main__
import os

# Adjust these variables for your program
PROGRAM_NAME = "Dockie"
MAIN_SCRIPT = "indexer.py"  # Your program's entry point
ICON_PATH = "logo.ico"   # Optional: Path to your program's icon

PyInstaller.__main__.run([
    MAIN_SCRIPT,
    '--onefile',  # Create a single executable
    # '--windowed',  # For GUI applications (remove for console applications)
    f'--icon={ICON_PATH}',  # Optional: Add an icon
    f'--add-data={ICON_PATH};.',  # Bundle the icon for runtime use (tray icon)
    '--name', f'{PROGRAM_NAME}',
    '--clean',  # Clean PyInstaller cache and remove temporary files
    '--noconsole',
])