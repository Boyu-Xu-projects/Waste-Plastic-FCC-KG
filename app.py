import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "05_14_waste_plastic_fcc_KG_M3_INTERACTION_FIX.py"

spec = importlib.util.spec_from_file_location("waste_plastic_fcc_kg", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

app = module.app
