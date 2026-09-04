import os
import sys

from openai import OpenAI

ROOT = "/mnt/data/AI/Agents/AAG-Ubuntu-Agent"

if not os.path.ismount("/mnt/data"):
    raise SystemExit("ERROR: /mnt/data is not mounted")

if not os.environ.get("OPENAI_API_KEY"):
    raise SystemExit("ERROR: OPENAI_API_KEY is not set")

client = OpenAI()

print("AAG Ubuntu Agent")
print("OpenAI connection test")
print("Model: gpt-5.6-luna")
print()

response = client.responses.create(
    model="gpt-5.6-luna",
    input=(
        "You are being tested as the reasoning engine for an Ubuntu "
        "system administration agent. Reply with exactly: "
        "AAG AGENT BRAIN ONLINE"
    ),
)

print(response.output_text)
