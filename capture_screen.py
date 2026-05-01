import pyautogui
import os

# Save to the artifacts directory of the current conversation
save_path = os.path.join(os.getcwd(), "screen_verification.png")
pyautogui.screenshot(save_path)
print(f"Screenshot saved to {save_path}")
