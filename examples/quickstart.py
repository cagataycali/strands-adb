"""Quickstart: use strands-adb with a Strands agent."""
from strands import Agent
from strands_adb import adb

agent = Agent(
    tools=[adb],
    system_prompt=(
        "You control an Android device via the `adb` tool. "
        "Always list_devices first, then select_device, then act. "
        "For UI automation, use ui_dump or screenshot to see state."
    ),
)

if __name__ == "__main__":
    print(agent("List connected devices, then tell me the model and battery level."))
