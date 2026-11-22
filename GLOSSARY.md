# OpenWispr

Private, local push-to-talk dictation for Windows. This context holds the vocabulary for controlling dictation from the keyboard and for managing the app from its window.

## Language

**Desktop App**:
The installed Windows application whose window shows Dictation status and whose window close or Quit ends the Instance.
_Avoid_: tray app, background service, shortcut

**Launch**:
Opening the Desktop App from the Start Menu, which makes it ready without starting a Dictation.
_Avoid_: start dictation, autostart, boot

**Instance**:
One running Desktop App; at most one exists per Windows session.
_Avoid_: process, session, duplicate window

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

**Quit**:
Ending the Desktop App and its Instance, after any in-progress Dictation finishes.
_Avoid_: stop, disable, exit

**Stop**:
Ending the current Dictation without ending the Desktop App.
_Avoid_: quit, exit, cancel
