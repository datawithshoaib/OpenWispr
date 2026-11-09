//! Windows integration: global keyboard hook, text insertion and sound cues.

use crate::hotkey::{Action, Chord, Machine};
use anyhow::{Result, bail};
use std::cell::RefCell;
use std::sync::OnceLock;
use std::sync::mpsc::Sender;
use std::time::{Duration, Instant};
use windows_sys::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::Media::Audio::{PlaySoundW, SND_ASYNC, SND_MEMORY, SND_NODEFAULT};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBD_EVENT_FLAGS, KEYBDINPUT, KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE, SendInput, VK_CONTROL, VK_LWIN, VK_MENU, VK_RETURN, VK_RWIN, VK_SHIFT,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, DispatchMessageW, GetMessageW, HC_ACTION, KBDLLHOOKSTRUCT, MSG, SetWindowsHookExW,
    TranslateMessage, WH_KEYBOARD_LL, WM_KEYDOWN, WM_SYSKEYDOWN,
};

/// Tags input we inject ourselves so the hook ignores it.
const OUR_INPUT: usize = 0x0057_4953; // "WIS"
/// An unassigned virtual key; tapping it before Win/Alt is released stops the
/// Start menu / menu bar from opening (the same trick AutoHotkey uses).
const VK_MASK: u16 = 0xE8;

struct HookState {
    machine: Machine,
    tx: Sender<Action>,
}

thread_local! {
    static HOOK: RefCell<Option<HookState>> = const { RefCell::new(None) };
}

/// Installs a low-level keyboard hook on a dedicated thread and sends hotkey
/// actions to `tx`. Returns once the hook is installed.
pub fn spawn_hotkey_listener(chord: Chord, tx: Sender<Action>) -> Result<()> {
    let (ready_tx, ready_rx) = std::sync::mpsc::channel();
    std::thread::Builder::new().name("hotkey".into()).spawn(move || {
        HOOK.with(|h| *h.borrow_mut() = Some(HookState { machine: Machine::new(chord), tx }));
        // SAFETY: plain Win32 calls; the hook procedure lives for the whole program.
        unsafe {
            let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(hook_proc), std::ptr::null_mut(), 0);
            let _ = ready_tx.send(!hook.is_null());
            if hook.is_null() {
                return;
            }
            // Low-level hooks are called through this thread's message loop.
            let mut msg: MSG = std::mem::zeroed();
            while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    })?;
    if !ready_rx.recv().unwrap_or(false) {
        bail!("could not install the keyboard hook");
    }
    Ok(())
}

unsafe extern "system" fn hook_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code == HC_ACTION as i32 {
        // SAFETY: for WH_KEYBOARD_LL, lparam points to a KBDLLHOOKSTRUCT.
        let info = unsafe { &*(lparam as *const KBDLLHOOKSTRUCT) };
        if info.dwExtraInfo != OUR_INPUT {
            let vk = info.vkCode as u16;
            let is_down = matches!(wparam as u32, WM_KEYDOWN | WM_SYSKEYDOWN);
            let outcome = HOOK.with(|h| {
                h.borrow_mut().as_mut().map(|s| {
                    s.machine.retain_down(|k| k == vk || is_pressed(k));
                    let out = s.machine.on_key(vk, is_down, Instant::now());
                    if let Some(action) = out.action {
                        let _ = s.tx.send(action);
                    }
                    out
                })
            });
            if let Some(out) = outcome {
                if out.mask {
                    send_keys(&[(VK_MASK, true), (VK_MASK, false)]);
                }
                if out.suppress {
                    return 1;
                }
            }
        }
    }
    unsafe { CallNextHookEx(std::ptr::null_mut(), code, wparam, lparam) }
}

fn is_pressed(vk: u16) -> bool {
    // SAFETY: simple query of global key state.
    unsafe { GetAsyncKeyState(vk as i32) < 0 }
}

fn key_input(vk: u16, scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT { wVk: vk, wScan: scan, dwFlags: flags, time: 0, dwExtraInfo: OUR_INPUT },
        },
    }
}

fn send(inputs: &[INPUT]) {
    // SAFETY: `inputs` is a valid slice of INPUT structs.
    unsafe { SendInput(inputs.len() as u32, inputs.as_ptr(), size_of::<INPUT>() as i32) };
}

fn send_keys(keys: &[(u16, bool)]) {
    let inputs: Vec<INPUT> = keys
        .iter()
        .map(|&(vk, down)| key_input(vk, 0, if down { 0 } else { KEYEVENTF_KEYUP }))
        .collect();
    send(&inputs);
}

/// Waits (briefly) until the user lets go of modifier keys, so that our Ctrl+V
/// isn't turned into e.g. Win+Ctrl+V.
fn wait_for_modifiers_released() {
    let deadline = Instant::now() + Duration::from_millis(1500);
    let mods = [VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN];
    while Instant::now() < deadline && mods.iter().any(|&k| is_pressed(k)) {
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// Inserts text into the focused app by pasting it, then restores the clipboard.
pub fn paste_text(text: &str) -> Result<()> {
    use arboard::SetExtWindows;
    let mut clipboard = arboard::Clipboard::new()?;
    let previous = clipboard.get_text().ok();
    clipboard.set().exclude_from_history().exclude_from_cloud().text(text)?;
    wait_for_modifiers_released();
    send_keys(&[(VK_CONTROL, true), (0x56, true), (0x56, false), (VK_CONTROL, false)]);
    // Give the target app time to read the clipboard before restoring it.
    std::thread::sleep(Duration::from_millis(250));
    if let Some(prev) = previous {
        let _ = clipboard.set().exclude_from_history().exclude_from_cloud().text(prev);
    }
    Ok(())
}

/// Inserts text by synthesizing Unicode keystrokes (leaves the clipboard alone).
pub fn type_text(text: &str) -> Result<()> {
    wait_for_modifiers_released();
    let mut inputs = Vec::with_capacity(text.len() * 2);
    for ch in text.chars() {
        if ch == '\n' {
            inputs.push(key_input(VK_RETURN, 0, 0));
            inputs.push(key_input(VK_RETURN, 0, KEYEVENTF_KEYUP));
            continue;
        }
        let mut units = [0u16; 2];
        for &unit in ch.encode_utf16(&mut units).iter() {
            inputs.push(key_input(0, unit, KEYEVENTF_UNICODE));
            inputs.push(key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
        }
    }
    send(&inputs);
    Ok(())
}

#[derive(Clone, Copy)]
pub enum Cue {
    Start,
    Stop,
    Cancel,
}

/// Plays a short, quiet beep without blocking.
pub fn play_cue(cue: Cue) {
    static SOUNDS: OnceLock<[Vec<u8>; 3]> = OnceLock::new();
    let sounds = SOUNDS.get_or_init(|| {
        [tone_wav(&[880.0, 1320.0]), tone_wav(&[1320.0, 880.0]), tone_wav(&[440.0, 330.0])]
    });
    let wav = &sounds[cue as usize];
    // SAFETY: the WAV buffer is 'static, so it outlives asynchronous playback.
    unsafe { PlaySoundW(wav.as_ptr() as *const u16, std::ptr::null_mut(), SND_MEMORY | SND_ASYNC | SND_NODEFAULT) };
}

/// Builds an in-memory 16-bit mono WAV of consecutive short tones.
fn tone_wav(freqs: &[f32]) -> Vec<u8> {
    const RATE: u32 = 22_050;
    let per_tone = RATE as usize * 45 / 1000;
    let mut pcm = Vec::with_capacity(per_tone * freqs.len() * 2);
    for &f in freqs {
        for i in 0..per_tone {
            let t = i as f32 / RATE as f32;
            let env = (i.min(per_tone - i) as f32 / (RATE as f32 * 0.005)).min(1.0); // 5 ms fades
            let s = (t * f * std::f32::consts::TAU).sin() * env * 0.18;
            pcm.extend_from_slice(&((s * i16::MAX as f32) as i16).to_le_bytes());
        }
    }
    let mut wav = Vec::with_capacity(44 + pcm.len());
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + pcm.len() as u32).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16u32.to_le_bytes());
    wav.extend_from_slice(&1u16.to_le_bytes()); // PCM
    wav.extend_from_slice(&1u16.to_le_bytes()); // mono
    wav.extend_from_slice(&RATE.to_le_bytes());
    wav.extend_from_slice(&(RATE * 2).to_le_bytes());
    wav.extend_from_slice(&2u16.to_le_bytes());
    wav.extend_from_slice(&16u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
    wav.extend_from_slice(&pcm);
    wav
}

#[cfg(test)]
mod tests {
    use super::tone_wav;

    #[test]
    fn cue_is_valid_wav() {
        let wav = tone_wav(&[880.0, 1320.0]);
        let reader = hound::WavReader::new(std::io::Cursor::new(wav)).unwrap();
        assert_eq!(reader.spec().channels, 1);
        assert_eq!(reader.spec().sample_rate, 22_050);
        assert_eq!(reader.len(), 22_050 * 45 / 1000 * 2);
    }
}
