# OpenWispr

Private, local push-to-talk dictation for Windows. This context holds the vocabulary for controlling dictation from the keyboard and for managing the app from the Tray.

## Language

**Dictation**:
A single recording session whose recognized words are inserted into the focused application.
_Avoid_: recording session, transcription job

**Chord**:
The configured key combination that starts and stops dictation; holding it dictates until release, while tapping it switches to hands-free.
_Avoid_: hotkey, shortcut, keybind

**Hands-free dictation**:
A dictation session that continues without the chord held and ends on the next chord press or Escape.
_Avoid_: toggle mode, sticky recording

**Dictation model**:
The local speech model whose accuracy determines which words are recognized; selected as the default or as an optional alternative.
_Avoid_: Whisper engine, model file

**Tray**:
The app control the user right-clicks to Quit.
_Avoid_: system tray menu, indicator, status icon

**Quit**:
Ending the running app without removing its autostart installation.
_Avoid_: stop, disable, exit

**Autostart**:
The opt-in installation that launches the app at Windows login.
_Avoid_: background service, startup task
