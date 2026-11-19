#!/bin/bash
# aDSL installation script

echo "=== aDSL installation script ==="
echo "Activating virtual environment..."
source venv/Scripts/activate

echo "Step 1/3: Installing the aDSL core module and Blender rendering support..."
pip install -e "./adsl-core[mesh,render]" \
  --extra-index-url https://download.blender.org/pypi/

echo "Step 2/3: Installing Agent workflow dependencies..."
pip install -e .

echo "Step 3/3: Verifying the installation..."
python -c "import adsl; print('aDSL core module installed successfully!')"
python -c "import bpy; print(f'Blender Python module version: {bpy.app.version_string}')"

echo "=== Installation complete ==="
echo "You can test with the following command:"
echo "  adsl-run create \"A laptop\" --output ./outputs/laptop"
