<<<<<<< HEAD
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.png">
    <img src="docs/logo-light.png" alt="Veyra logo" height="160">
  </picture>
</p>

<h1 align="center">Veyra</h1>

<p align="center">
  <b>Visual macros for Roblox, without writing code.</b><br>
  Snap blocks together or record yourself playing. Veyra clicks, types, finds buttons and reads text for you.
</p>

<p align="center">
  <a href="https://github.com/Lumynith/Veyra/releases/latest"><b>⬇ Download for Windows</b></a>
  &nbsp;·&nbsp; <a href="#getting-started">Getting started</a>
  &nbsp;·&nbsp; <a href="#features">Features</a>
  &nbsp;·&nbsp; <a href="#troubleshooting">Troubleshooting</a>
</p>

---

## What is Veyra?

Veyra is a desktop app for Windows that automates Roblox the way a person would: by looking at the screen and using the mouse and keyboard. You build a macro by dragging blocks into place: *click here*, *wait for this button*, *press Space*, *repeat 10 times*. Then press Run.

You don't need to know how to code. Step-by-step tutorials open the first time you start the app and point at every button as you go.

### Highlights

- 🧩 **Block editor:** mouse, keyboard, waits, loops, If/Else, variables, image and text detection. Run, pause, step and restart, with a live run log.
- 📚 **Macro Library:** every saved macro in one searchable list, organised in folders. One click opens or runs a macro.
- ⏺ **Recorder:** play the game yourself and turn the recording into editable blocks.
- 🖼 **Image detection:** capture a button once, and Veyra finds and clicks it wherever it appears.
- 🔤 **Text detection (OCR):** find and click words, and read numbers such as coins or timers off the screen.
- 🛠 **Your own blocks:** wrap steps you reuse into one block, with no code or with sandboxed Python.
- 🪟 **Roblox docks inside the app**, so clicks always land in the right place.
- 🛑 **Emergency stop** (Ctrl+Alt+F8) halts everything and releases every held key and mouse button.
- 🎓 **Eleven guided tutorials**, from a two-minute tour to making your own blocks.

### Safe and local

- **No injection, ever.** Veyra only uses normal window, screen, mouse and keyboard input, like a person at the keyboard. It never reads or writes Roblox memory, never modifies the Roblox client, and does not try to get around anti-cheat.
- **Everything stays on your PC.** There is no account, no cloud and no telemetry. Macros, images and settings are plain files in the Veyra folder.

> Use automation responsibly and follow the rules of the games you play.

---

## Getting started

**Requirements:** Windows 10 or 11 (64-bit) and the Roblox Player.

1. Download **`Veyra-<version>-win64.zip`** from the [latest release](https://github.com/Lumynith/Veyra/releases/latest).
2. Unzip it to a folder you can write to, for example `Documents\Veyra`. Avoid `Program Files`, because Veyra saves your work next to itself.
3. Double-click **Veyra.exe**. Nothing else needs to be installed.
4. If Windows says *"Windows protected your PC"*, click **More info → Run anyway**. Windows shows this for any program that isn't code-signed.
5. Start Roblox, join a game, and follow the **Connect Roblox** tutorial in the app.

**Updating:** unzip the new version over the old folder. Your macros, images, settings and logs (`data\`, `config\settings.json`, `logs\`) aren't in the zip, so they are kept.

---

## Features

### Workflow editor (Ctrl+1)
- Drag blocks into stacks. Containers (Repeat, If/Else, Repeat Until…) hold other blocks.
- Every block has its own settings, including error handling: retries, timeouts, and whether to stop, continue or restart on error.
- Undo and redo, copy and paste, multi-select, Find (Ctrl+F), favourite blocks, templates and a variable manager.
- **Run** (F5), **Pause/Resume** (F6), **Step** (F7), **Restart** (Ctrl+Shift+F5), **Stop** (Shift+F5 or Esc). You can also run a single block, or run from a chosen block.
- **Debugging:** the current block is highlighted, the run log explains every step, and you can watch and edit variables live.
- **Autosave** every minute. After a crash, Veyra offers to restore your unsaved work.

### Macro Library (Ctrl+7)
- **Save current macro here** stores the open macro. After that, Ctrl+S keeps it up to date.
- **Open** (or double-click) a macro to edit it, or **Run** it straight away.
- Rename, duplicate, move into folders (for example one per game), or show a macro in Explorer. Deleted macros go to the Recycle Bin.
- **Import** macro files, or drop them on the page.

### Recorder (Ctrl+2)
- Records mouse and keyboard relative to the Roblox window. F9 pauses and resumes recording; F10 stops it.
- Optimisation presets remove jitter and tiny pauses. You can also set playback speed and the number of repeats.
- Play the recording back, or **Convert to blocks** to edit it like any other macro.

### Image detection (Ctrl+3)
- Capture part of the game, or import pictures, into your image library.
- Test detection live, and tune confidence, colour or greyscale matching, and multi-scale matching. Matching adapts when the Roblox window changes size.
- Blocks: Find, Wait For, Click, Move To, Exists, Disappears, Find Multiple, Compare, Capture.

### Text detection (Ctrl+4)
- Select an area of the game and read its text. This uses the OCR built into Windows, with Tesseract as an option.
- Save **text targets**: the text to look for, how to match it (exact, contains or regex) and where on screen to look.
- Blocks: find, click and wait for text, read numbers, and more.

### Custom blocks (Ctrl+5) and scripts (Ctrl+6)
- **No-code blocks** are a small macro with its own inputs and outputs, reusable everywhere.
- **Script blocks** run Python in a locked-down sandbox:
  - a separate process with a memory cap and a timeout, which can't start other programs
  - only an allow-list of modules can be imported
  - mouse, screen, variables and files are permissions you tick per script
- Share blocks as `.lcb.json` files. Examples are in `examples/custom_blocks/`.

### Tutorials and polish
- Eleven short lessons. A small guide card opens the right page and highlights each button the step talks about.
- A startup animation, a glow on the status bar while a macro runs, and a pop-up message when it finishes, fails or is stopped. You can turn these off in Settings.

### Global hotkeys (work while Roblox has focus)

| Action | Default |
|---|---|
| Start macro | Ctrl+Alt+F5 |
| Pause | Ctrl+Alt+F6 |
| Resume | Ctrl+Alt+F7 |
| **Emergency stop** | Ctrl+Alt+F8 |
| Start recording | Ctrl+Alt+F9 |
| Stop recording | Ctrl+Alt+F10 |
| Show/hide app | Ctrl+Alt+H |

You can change them on the **Settings** page. Hotkeys another program has already taken are listed there.

### Optional: Tesseract OCR
Windows OCR works out of the box. To use Tesseract instead:
1. Install it (for example the UB Mannheim build) to `C:\Program Files\Tesseract-OCR\`.
2. On the **Settings** page, set *Text recognition* to *Tesseract*.
3. If you installed it somewhere else, set `ocr.tesseract_cmd` in `config\settings.json` to the full path of `tesseract.exe`.

---

## Troubleshooting

| Problem | What to do |
|---|---|
| "Roblox not found" | Make sure the Roblox Player (not Roblox Studio) is running, then press Ctrl+R. |
| Macros run but Roblox ignores key presses | Use **anchored** mode, the anchor button above the viewport (it's the default), and put a **Focus Roblox** block at the start of the macro. |
| Roblox is not docked or has the wrong size | Press Ctrl+R to reconnect. Check the viewport size on the **Settings** page. |
| Clicks land in the wrong place | Use 100 % Windows display scaling if you can, and keep Roblox docked. |
| Image not found | Lower the confidence a little (0.8), turn on multi-scale, or capture the image again. **Test on viewport** shows the score. |
| OCR reads nothing or garbage | Select a tighter area, install the Windows language pack for the text, or switch to Tesseract. |
| A hotkey does nothing | Another program has taken it. Pick a different one on the **Settings** page. |
| Anything else | See `logs\veyra.log`, or the console panel in the app. Set *Log level* to DEBUG on the **Settings** page for more detail. |

---

## For developers

Run from source (Python 3.13 or newer):

```powershell
git clone https://github.com/Lumynith/Veyra.git
cd Veyra
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Run the tests with `python -m pytest`.

**Plugins:** drop a `.py` file with `BaseBlock` subclasses into `plugins\` and it loads at startup (see `plugins/example_block.py`). Plugins are trusted code and are **not** sandboxed, so only install plugins you have read.

```
main.py         entry point
core/           application, settings, logging, window docking, global hotkeys
automation/     engine, workflows, input, recorder, projects, macro library, script sandbox
blocks/         block system; blocks/categories/ holds the built-in blocks
vision/         screen capture, image matching, OCR, text targets
ui/             the PySide6 interface
plugins/        developer plugins
examples/       example macros and custom blocks
resources/      icons and theme
tests/          pytest suite
```
=======
# Veyra
>>>>>>> 032c7dc4181df045980a19d0cc0ee0af40b131a2
