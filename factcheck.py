import base64
import os
import subprocess
import time

import anthropic
from dotenv import load_dotenv
from evdev import InputDevice, ecodes
from libcamera import controls
from picamera2 import Picamera2

load_dotenv()

BUTTON = "/dev/input/by-id/usb-SayoDevice_SayoDevice_1x1P_03CDABF15D5BBD19C7FFFFFFFF00-event-kbd"
AUDIO_DEVICE = "plughw:Audio,0"
SPEECH_RATE = "125"
SPEECH_FILE = "/tmp/say.wav"
PHOTO = "last.jpg"
MAX_SIDE = 1568
JPEG_QUALITY = 80
SETTLE_SECONDS = 0.4
MAX_TOKENS = 2000
MAX_SEARCHES = 3
MODEL = os.environ["FACTCHECK_MODEL"]

SYSTEM_PROMPT = """You are a fact-checker. The image is a photo of an article the user is reading, on paper or on a screen.

Read the article section nearest the center of the image. Ignore partial text at the edges, menus, and anything that is not the article.
Identify its main factual claims and check them with web search.

If there are multiple choices outlined by empty bulletins, or some other form of multiple choice, you will respond with one number, where the top is 1, and the last is the bottom choice. 

Example:
What color is the sky?
Red []
Orange []
Yellow []
Blue []

You would respond Fourth option, to represent the fourth option. 

There may be multiple choices in which the design of numbering does not work, such as if there is multiple columns. In that advent you should respond with whatever you believe will identify the right answer. 

You will also encounter question which will sometimes require the individual to type themselves. 

Example: The days of the weekend are _______
In this you will provide the minimal answer. 

In the situation of True or False, reply with exactly one verdict word , then one sentence explaining why.
The verdict word must be one of: TRUE, FALSE.

Example: Monday is the first day of the week?
True or False?
Answer: True.

If you cannot read the text, reply UNREADABLE, then say what was wrong with the photo.
No other text. No markdown. No list of sources.

There may be instances where there is multiple questions on the page at one time, in this situation respond with the answers, but by some identifying marker. 

Example:
1. What is the largest ocean on Earth?
Atlantic
Pacific
Indian
2. Which plant is known as the Red Planet?
Venus
Jupiter
Mars

Your answer in this situation would be:
for the question labeled 1: Pacific. 
for the question labeled 2: Mars

Do note give full explanations of optional choices, give the minimum to tell the user what the answer is for selection. Do not give descriptions, only the answers. Only give the answer to the question:

Example:
What scientist discovered relativity?
Answer: Albert Einstein.

Do not ramble, or give unnecessary details beyond what is needed to answer the question."""


def speak(text):
    print(text)
    subprocess.run(["espeak-ng", "-s", SPEECH_RATE, "-g", "4", "-v", "en-us", "-w", SPEECH_FILE, text])
    subprocess.run(["aplay", "-q", "-D", AUDIO_DEVICE, SPEECH_FILE])


def take_photo(camera):
    time.sleep(SETTLE_SECONDS)
    image = camera.capture_image("main").convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    image.save(PHOTO, "JPEG", quality=JPEG_QUALITY)
    return PHOTO


def extract_answer(response):
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
        elif block.type == "web_search_tool_result":
            parts = []
    return "".join(parts).strip()


def fact_check(client, path):
    with open(path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}],
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data}},
                {"type": "text", "text": "Fact-check this."},
            ],
        }],
    )
    print("stop_reason:", response.stop_reason,
          "| blocks:", [b.type for b in response.content],
          "| output tokens:", response.usage.output_tokens)
    return extract_answer(response)


client = anthropic.Anthropic()

camera = Picamera2()
camera.configure(camera.create_still_configuration(main={"size": (2028, 1520)}))
camera.set_controls({"AeExposureMode": controls.AeExposureModeEnum.Short})
camera.start()
time.sleep(2)

button = InputDevice(BUTTON)
speak("Ready")

LONG_PRESS_SECONDS = 1.5
pressed_at = None

for event in button.read_loop():
    if event.type != ecodes.EV_KEY or event.code != ecodes.KEY_1:
        continue
    if event.value == 1:
        # Button went down: remember when.
        pressed_at = event.timestamp()
    elif event.value == 2 and pressed_at is not None:
        # Still held: shut down once it has been held long enough.
        if event.timestamp() - pressed_at >= LONG_PRESS_SECONDS:
            pressed_at = None
            speak("Shutting down")
            subprocess.run(["sudo", "shutdown", "now"])
            time.sleep(60)
    elif event.value == 0 and pressed_at is not None:
        # Released before the long-press time: run a check.
        pressed_at = None
        started = time.time()
        path = take_photo(camera)
        speak("Checking")
        try:
            verdict = fact_check(client, path)
        except Exception as error:
            verdict = "Error. The check failed."
            print(error)
        speak(verdict or "No answer came back.")
        print(f"({time.time() - started:.0f}s)")
        while button.read_one() is not None:
            pass
