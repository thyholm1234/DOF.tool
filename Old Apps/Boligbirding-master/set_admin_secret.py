import secrets
import os

env_path = os.path.join(os.path.dirname(__file__), ".env")
new_secret = secrets.token_hex(32)  # 64 tegn hex

with open(env_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

with open(env_path, "w", encoding="utf-8") as f:
    found = False
    for line in lines:
        if line.startswith("ADMIN_SECRET="):
            f.write(f"ADMIN_SECRET={new_secret}\n")
            found = True
        else:
            f.write(line)
    if not found:
        f.write(f"ADMIN_SECRET={new_secret}\n")

print("ADMIN_SECRET er nu sat til:", new_secret)