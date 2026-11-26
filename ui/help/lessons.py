"""Tutorial content: lessons made of short steps, written for people who have never used code or blocks.

A step may point at something in the app. `page` is a sidebar page name (editor, recorder, image, text,
blocks, script, settings); `target` names the thing to highlight on it:
  "nav:Editor"      a sidebar button, by its tooltip
  "@status.roblox"  an attribute path on the main window
  anything else     a button (text or tooltip start), tab, input placeholder, panel title or block in the
                    block list with that text. tests/test_tutorials.py checks that every target is found.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    text: str
    tip: str = ""
    page: str = ""
    target: str = ""


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    summary: str
    minutes: int
    steps: list[Step] = field(default_factory=list)


S = Step

LESSONS = [
    Lesson("tour", "Welcome: a quick tour", "What this app does and where everything is.", 2, [
        S("<b>Veyra plays Roblox for you.</b> It presses keys and clicks the mouse exactly like you "
          "would, following a list of instructions you put together.<br><br>"
          "You build that list from <b>blocks</b>, like snapping LEGO bricks together. "
          "<b>You never need to write code.</b>",
          "Press Next to continue. You can drag this guide anywhere, close it at any time, and start the lesson "
          "again from the Tutorials page."),
        S("The column of icons on the far left switches between <b>pages</b>. "
          "Hover over an icon to see its name.<br><br>You will spend most of your time on the <b>Editor</b> page, "
          "where you build macros.", page="", target="nav:Editor"),
        S("The big area on the left is the <b>Roblox viewport</b>. When Roblox is running, its window is pulled "
          "inside the app and sits right here, always at the same size. That's how a macro always knows where "
          "the game is.", target="@viewport_panel"),
        S("Below it is the <b>Console</b>. It writes down what happens while a macro runs: what was done, what "
          "was found and what went wrong. <b>Red lines are errors.</b>", target="@console_panel"),
        S("The bar along the very bottom shows whether a macro is running, whether Roblox is connected, and "
          "how many errors there have been.", target="@status.roblox",
          tip="Not sure what a button does? Rest the mouse on it for a second and a short explanation appears."),
        S("That's the tour. 🎉<br><br>Each step with a <b>Show me</b> button can point out what it's "
          "talking about: press it and that part of the app lights up.<br><br>"
          "Next up: <b>Connect Roblox</b>."),
    ]),
    Lesson("connect", "Connect Roblox", "Get the game inside the app so macros can see and control it.", 2, [
        S("Start <b>Roblox</b> and join a game, the same way you normally do."),
        S("Veyra finds Roblox <b>by itself</b> and pulls its window into the viewport. This takes a second "
          "or two.", target="@viewport_panel"),
        S("Look at the bottom bar. A <b>green dot</b> next to the word Roblox means it's connected.<br><br>"
          "If it says <b>Roblox disconnected</b>, Roblox isn't running or wasn't found yet.",
          target="@status.roblox"),
        S("Not connected? Press <b>Reconnect</b> above the viewport (or press <b>Ctrl+R</b>).",
          target="Reconnect"),
        S("Have more than one Roblox open? Pick the one you want in the list above the viewport, then press "
          "the <b>chain</b> button to attach it.", target="@viewport_panel.toolbar.connect_btn"),
        S("<b>Leave the Roblox window where it is.</b> Every click a macro makes is measured from the corner of "
          "the viewport, so keeping the size and position fixed keeps clicks landing in the right place.",
          "If Roblox crashes or restarts, Veyra notices and pulls the new window in automatically."),
    ]),
    Lesson("first", "Your first macro", "Build a macro that makes your character jump three times.", 6, [
        S("A macro is a <b>stack of blocks</b>. When you press Run, the blocks run one after another, "
          "<b>from top to bottom</b>.<br><br>Every stack that runs starts with a <b>Start</b> block. "
          "Blocks only run if they're snapped underneath it.", page="editor", target="Workflow"),
        S("Let's start with a clean page. Click the <b>folder</b> button and choose <b>New project</b> "
          "(or press <b>Ctrl+N</b>).", page="editor", target="Project:",
          tip="Don't worry about the old workflow: if it has unsaved changes, you'll be asked first."),
        S("On the left is the <b>Blocks</b> list, sorted into groups like Mouse, Keyboard and Flow Control.<br><br>"
          "Type in the <b>search box</b> at the top to find a block by name.", page="editor",
          target="Search blocks"),
        S("Find <b>Focus Roblox</b> (in the Roblox group) and <b>double-click</b> it. It's added under Start.<br><br>"
          "This block brings Roblox to the front, so key presses go to the game and not to this app.",
          page="editor", target="Focus Roblox",
          tip="You can also drag a block from the list and drop it just below another block: it snaps into place."),
        S("Now find <b>Repeat</b> (in Flow Control) and double-click it too.<br><br>"
          "Repeat is shaped like a <b>C</b>. Whatever you put inside its mouth runs several times.",
          page="editor", target="Repeat"),
        S("<b>Click the Repeat block</b> on the canvas. Its settings appear in the <b>Properties</b> tab "
          "underneath. Set <b>Times</b> to <b>3</b>.", page="editor", target="Properties",
          tip="Every block's settings live in Properties. Click a block to see them."),
        S("Find <b>Press Key</b> (in Keyboard). <b>Drag</b> it onto the canvas and drop it <b>inside the mouth</b> "
          "of the Repeat block.<br><br>Click it and type <b>space</b> in its <b>Key</b> box.",
          page="editor", target="Press Key",
          tip="While you type a key name, suggestions appear. Letters are just the letter: e, f, 1…"),
        S("Drag a <b>Wait</b> block (in Flow Control) inside the Repeat too, <b>under Press Key</b>. "
          "Set <b>Seconds</b> to <b>1</b>.<br><br>Waits give the game time to react. Without them a macro runs "
          "far faster than Roblox can keep up with.", page="editor", target="Wait"),
        S("Your stack should now read:<br><br>"
          "&nbsp;&nbsp;Start<br>&nbsp;&nbsp;Focus Roblox<br>&nbsp;&nbsp;Repeat 3 times<br>"
          "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Press Key space<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Wait 1 s<br><br>"
          "Press the <b>▶ Run</b> button (or <b>F5</b>). Your character jumps three times. The block that is "
          "running lights up while it works.", page="editor", target="Run workflow"),
        S("<b>To stop a macro at any time</b>, press the <b>■ Stop</b> button, <b>Shift+F5</b>, or <b>Esc</b>.<br><br>"
          "While you're in Roblox, press <b>Ctrl+Alt+F8</b>: it works even when this app is hidden.<br><br>"
          "Stopping also lets go of any key or mouse button the macro was holding down.",
          page="editor", target="Stop now"),
        S("Press <b>Ctrl+S</b> (or the disk button) to save your macro, and give it a name.<br><br>"
          "🎉 You've built your first macro!", page="editor", target="Save project",
          tip="Your work is also saved automatically every minute, and offered back after a crash."),
    ]),
    Lesson("blocks", "How blocks work", "Moving, deleting, loops, If/Else and variables.", 6, [
        S("<b>Moving blocks.</b> Drag a block close underneath another and it snaps on.<br><br>"
          "Dragging a block <b>out of</b> a stack takes every block below it along. Hold <b>Alt</b> while dragging "
          "to move just that one block.", page="editor", target="Workflow"),
        S("<b>Deleting.</b> Click a block and press <b>Delete</b>, or right-click it and choose Delete.<br><br>"
          "Made a mistake? <b>Ctrl+Z</b> undoes it, <b>Ctrl+Y</b> redoes it.", page="editor", target="Undo"),
        S("<b>Right-click a block</b> for more: Copy, Duplicate, Collapse (fold it up to save space), "
          "Run this block on its own, Run from here, and what to do if it fails.", page="editor",
          target="Workflow"),
        S("<b>Loops.</b> Blocks with a C shape hold other blocks.<br><br>"
          "• <b>Repeat</b> runs the inside a number of times (<b>0 = forever</b>, until you stop it).<br>"
          "• <b>Repeat Until</b> keeps going until something becomes true.<br>"
          "• <b>Break Loop</b> jumps out of the loop it's in.", page="editor", target="Repeat Until"),
        S("<b>If / Else.</b> <b>If</b> checks something and only runs its inside when the check is true. "
          "Put an <b>Else</b> right under an If for what should happen otherwise.<br><br>"
          "A check compares a <b>Value</b> with <b>Compare to</b> using an operator such as == (is equal), "
          "!= (is not), &gt;, &lt;, contains, or <i>is true</i>.", page="editor", target="If"),
        S("<b>Variables</b> are named boxes that hold a value, like a coin count.<br><br>"
          "• <b>Set Variable</b> puts a value into one.<br>"
          "• Anywhere else, write the name in curly brackets, like <b>{coins}</b>, and it's swapped for the value.<br>"
          "• Start a value with <b>=</b> to calculate: <b>={coins} + 1</b>.", page="editor", target="Set Variable"),
        S("Many blocks also <b>store their result</b> in a variable, named in their <b>Output variable</b> "
          "setting. For example, Find Image sets <b>{image}</b> to true or false, ready for an If block.<br><br>"
          "The <b>Variables</b> tab shows every variable and its live value while a macro runs.",
          page="editor", target="Variables"),
        S("<b>Comment</b> blocks do nothing except hold a note, which is handy for reminding yourself what part "
          "of a macro is for.", page="editor", target="Comment",
          tip="Lots of blocks? Select several, right-click, Group selected, and give the group a name."),
    ]),
    Lesson("record", "Record instead of building", "Play the game yourself and turn it into blocks.", 4, [
        S("Don't want to build block by block? <b>Record</b> yourself playing and Veyra writes the blocks "
          "for you.", page="recorder", target="nav:Recorder"),
        S("Leave <b>Record in</b> on <b>Roblox viewport</b>. Clicks are then measured from the game's corner, "
          "so they still land right after the window moves.", page="recorder", target="@recorder.space"),
        S("Press <b>Record</b>, then click into Roblox and play normally. Clicks and typing in this app itself "
          "are ignored.", page="recorder", target="Record"),
        S("While recording, press <b>F9</b> to pause or resume and <b>F10</b> to stop. These work while you're "
          "inside Roblox."),
        S("<b>Optimisation</b> tidies the recording. <b>Medium</b> suits most things. Choose <b>Exact</b> to "
          "keep every tiny mouse movement and pause.", page="recorder", target="Optimisation"),
        S("Press <b>Play</b> to replay it as it is, or <b>Convert to blocks</b> to open it in the Editor, where "
          "you can change it, add waits or wrap it in a Repeat.", page="recorder", target="Convert to blocks",
          tip="Recordings click at fixed spots. If the button you need moves around, use image or text "
              "detection (the next two lessons) instead."),
    ]),
    Lesson("image", "Click things by their picture", "Find a button on screen wherever it is, and click it.", 6, [
        S("Clicking at a fixed spot breaks when a button moves. <b>Image detection</b> looks for a picture of "
          "the button instead, wherever it is on the screen.", page="image", target="nav:Image Detection"),
        S("Make sure the button you want is <b>visible in Roblox</b>. Then press the <b>camera</b> button to "
          "take a picture of the viewport.", page="image", target="Capture from the Roblox viewport"),
        S("A window opens. <b>Drag a box tightly around the button</b>, give it a name like <i>Play button</i>, "
          "and press <b>Save image</b>.",
          tip="Crop just the button. Leave out backgrounds, numbers and animations that change."),
        S("Select the image in the list and press <b>Test on viewport</b>. A box shows where it was found, "
          "along with a score: how sure the match is.", page="image", target="Test on viewport"),
        S("Now use it. In the Editor, add a <b>Click Image</b> block and choose your picture in its "
          "<b>Image</b> setting.<br><br>It waits up to 10 seconds for the picture to appear, then clicks its "
          "centre.", page="editor", target="Click Image"),
        S("If the picture never shows up, Click Image counts as an <b>error</b> and the macro stops.<br><br>"
          "To just <b>check</b> without failing, use <b>Find Image</b>: it sets <b>{image}</b> to true or false. "
          "Follow it with an <b>If</b> block: Value <b>{image}</b>, operator <b>is true</b>.",
          page="editor", target="Find Image"),
        S("Not found reliably? Lower <b>Confidence threshold</b> a little, from 0.85 to 0.8. Set it too low and "
          "it may click the wrong thing.",
          tip="If the game's look changes (a new season, a different menu), capture the picture again."),
    ]),
    Lesson("text", "Find and read text", "Click words on screen and read numbers such as coins.", 5, [
        S("<b>Text detection</b> reads words on the screen (this is called OCR), so a macro can look for "
          "<i>Play</i> or read your coin count.", page="text", target="nav:Text Detection"),
        S("Press the <b>camera</b> button to capture the viewport, then <b>drag over the area</b> that has the "
          "text.", page="text", target="Capture the Roblox viewport"),
        S("Press <b>Read text</b>. Every word found is listed, along with how sure the reader is about it.",
          page="text", target="Read text"),
        S("To reuse it, fill in <b>New target</b>: a <b>Name</b>, the <b>Text</b> to look for and how to match it, "
          "then press <b>Save target</b>. Tick <b>Only search the selected area</b> to make it faster and "
          "more accurate.", page="text", target="Save target"),
        S("In the Editor, add <b>Click Text</b> and pick your target under <b>Saved target</b>, or simply type the "
          "words into <b>Text</b>.<br><br>Also handy: <b>Wait For Text</b>, <b>Text Exists</b>, and "
          "<b>Find Number</b>, which reads a number into a variable.", page="editor", target="Click Text",
          tip="Small or fancy fonts are harder to read. Select a tighter area around the text."),
    ]),
    Lesson("debug", "Run safely and fix problems", "Pause, step through, read the run log, handle errors.", 5, [
        S("The run buttons, left to right: <b>Run</b> (F5), <b>Step</b> (F7) runs one block then pauses, "
          "<b>Pause/Resume</b> (F6), <b>Restart</b>, and <b>Stop</b>.<br><br>"
          "<b>Step</b> is the best way to find a mistake: watch it one block at a time.",
          page="editor", target="Step:"),
        S("While you're in Roblox, these keys work too:<br><br>"
          "<b>Ctrl+Alt+F5</b> start · <b>Ctrl+Alt+F6</b> pause · <b>Ctrl+Alt+F7</b> resume<br>"
          "<b>Ctrl+Alt+F8</b> emergency stop<br><br>"
          "You can change them on the Settings page.", page="settings", target="nav:Settings"),
        S("The <b>Run log</b> tab lists every block that ran, whether it worked, and what it found. "
          "Click a row to jump to that block.", page="editor", target="Run log"),
        S("Something keeps failing now and then? Click the block and scroll down its Properties to "
          "<b>Error handling</b>:<br><br>"
          "• <b>Retries</b>: try again this many times<br>"
          "• <b>Time limit</b>: give up after this long<br>"
          "• <b>On error</b>: stop, skip it and carry on, or restart the whole macro",
          page="editor", target="Properties"),
        S("To test one part, right-click a block and choose <b>Run this block</b> or <b>Run from here</b>.",
          page="editor", target="Workflow",
          tip="Still stuck? The Console lists errors in red, and the full log is in the logs folder."),
    ]),
    Lesson("projects", "Save, open and share", "The Macro Library, projects, templates and autosave.", 4, [
        S("The <b>Macro Library</b> keeps all your macros in one list. Open it from this button on the left.",
          page="library", target="nav:Macro Library"),
        S("Press <b>Save current macro here</b> to save the macro that is open in the Editor, and give it a name. "
          "After that, <b>Ctrl+S</b> in the Editor keeps that library copy up to date.", page="library",
          target="Save current macro here"),
        S("Pick a macro and press <b>Open</b> to edit it, or <b>Run</b> to start it straight away. <b>More</b> "
          "renames, copies, moves it into a folder or deletes it (deleted macros go to the Recycle Bin).",
          page="library", target="Run"),
        S("Use the <b>folder</b> button next to the search box to make folders, for example one per game. "
          "The list above it shows one folder at a time.", page="library", target="New folder"),
        S("The <b>folder</b> button holds everything to do with files: New, Open, Open recent, Save As, "
          "Duplicate, Import, Export, and <b>New from template</b>, which has ready-made examples to learn from.",
          page="editor", target="Project:"),
        S("Your work is <b>saved automatically</b> every minute. If the app or your PC crashes, the next time "
          "it starts it offers to bring your unsaved work back.", page="editor", target="Save project"),
        S("To share a macro, send its project file (it ends in <b>.lcp.json</b>).<br><br>"
          "Pictures and saved text targets stay on your computer, so whoever gets it may need to capture "
          "them again on the Image and Text Detection pages."),
    ]),
    Lesson("custom", "Make your own blocks", "Wrap steps you use a lot into one reusable block.", 5, [
        S("Doing the same few steps in many macros? Turn them into <b>one block</b> of your own.",
          page="blocks", target="nav:Custom Blocks"),
        S("Press <b>+</b> and choose <b>Built from blocks (no code)</b>.", page="blocks",
          target="New custom block"),
        S("On <b>General</b>, give it a <b>Name</b> and a short description.", page="blocks", target="General"),
        S("On <b>Inputs &amp; outputs</b>, add the settings your block should have, for example a <i>label</i> "
          "text. Inside the block, use them like variables: <b>{label}</b>.", page="blocks",
          target="Inputs & outputs"),
        S("On <b>Behaviour</b>, build the steps exactly like in the Editor. Then press <b>Save block</b>. "
          "Your block now appears in the Editor's Blocks list.", page="blocks", target="Behaviour",
          tip="Want examples? Press Import and open the examples/custom_blocks folder."),
    ]),
    Lesson("scripts", "Python scripts (optional)", "For people who already know a little Python.", 3, [
        S("<b>You can skip this lesson.</b> Everything in the other lessons works without code.<br><br>"
          "If you know some Python, the <b>Script Editor</b> runs small scripts as part of a macro.",
          page="script", target="nav:Script Editor"),
        S("Scripts run in a <b>separate, locked-down</b> process. They can only use the mouse, keyboard, screen, "
          "variables or files if you tick that permission under <b>Allow</b>.", page="script", target="input"),
        S("<b>inputs['name']</b> reads a value passed in, and <b>output('name', value)</b> hands a value back to "
          "the macro as a variable. Press <b>Run</b> to test it, then <b>Make block…</b> to turn it into a "
          "block.", page="script", target="Make block"),
    ]),
]
