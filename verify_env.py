import os
from dotenv import load_dotenv

load_dotenv()

vars_to_check = ["GEMINI_KEY", "SECRET_KEY", "DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"]
print("--- Environment Variables Check ---")
for var in vars_to_check:
    val = os.getenv(var)
    if val:
        print(f"  [OK] {var}: [Set]")
    else:
        print(f"  [FAIL] {var}: [NOT SET]")
print("----------------------------------")
