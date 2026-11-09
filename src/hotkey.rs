//! Push-to-talk hotkey logic, independent of the OS keyboard hook that feeds it.
//!
//! Behaviour (modelled on Wispr Flow):
//! - Hold the chord to record; release it to transcribe and insert the text.
//! - Tap it quickly to go hands-free; press it again to stop. `Esc` cancels.
//! - Pressing any other key while holding it (e.g. Ctrl+Win+D) cancels, so the
//!   chord's modifiers still work for normal shortcuts.
//!
//! Key codes are Windows virtual-key codes.

use anyhow::{Result, bail};
use std::collections::HashSet;
use std::time::{Duration, Instant};

pub const VK_ESCAPE: u16 = 0x1B;
const VK_LWIN: u16 = 0x5B;
const VK_RWIN: u16 = 0x5C;
const VK_LMENU: u16 = 0xA4;
const VK_RMENU: u16 = 0xA5;

/// A press shorter than this is a "tap" and starts hands-free mode.
pub const TAP_THRESHOLD: Duration = Duration::from_millis(300);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chord {
    /// Every group must have at least one key down; keys in a group are
    /// alternatives (e.g. left or right Ctrl).
    groups: Vec<Vec<u16>>,
    label: String,
}

impl Chord {
    pub fn parse(spec: &str) -> Result<Self> {
        let mut groups = Vec::new();
        for part in spec.split('+').map(|p| p.trim().to_lowercase()) {
            let keys: Vec<u16> = match part.as_str() {
                "ctrl" | "control" => vec![0xA2, 0xA3],
                "lctrl" | "left-ctrl" => vec![0xA2],
                "rctrl" | "right-ctrl" => vec![0xA3],
                "shift" => vec![0xA0, 0xA1],
                "lshift" | "left-shift" => vec![0xA0],
                "rshift" | "right-shift" => vec![0xA1],
                "alt" => vec![VK_LMENU, VK_RMENU],
                "lalt" | "left-alt" => vec![VK_LMENU],
                "ralt" | "right-alt" | "altgr" => vec![VK_RMENU],
                "win" | "super" | "meta" => vec![VK_LWIN, VK_RWIN],
                "space" => vec![0x20],
                "capslock" => vec![0x14],
                "insert" => vec![0x2D],
                "pause" => vec![0x13],
                "scrolllock" => vec![0x91],
                k if k.len() == 1 && k.chars().all(|c| c.is_ascii_alphanumeric()) => {
                    vec![k.to_ascii_uppercase().as_bytes()[0] as u16]
                }
                k if k.starts_with('f') && k[1..].parse::<u16>().is_ok_and(|n| (1..=24).contains(&n)) => {
                    vec![0x70 + k[1..].parse::<u16>().unwrap() - 1]
                }
                "" => bail!("empty key in hotkey '{spec}'"),
                other => bail!("unknown key '{other}' in hotkey '{spec}'"),
            };
            groups.push(keys);
        }
        Ok(Self { groups, label: spec.to_string() })
    }

    pub fn label(&self) -> &str {
        &self.label
    }

    fn contains(&self, vk: u16) -> bool {
        self.groups.iter().any(|g| g.contains(&vk))
    }

    fn held(&self, down: &HashSet<u16>) -> bool {
        self.groups.iter().all(|g| g.iter().any(|k| down.contains(k)))
    }
}

pub fn is_modifier(vk: u16) -> bool {
    matches!(vk, 0x10..=0x12 | 0xA0..=0xA5 | VK_LWIN | VK_RWIN)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Start,
    Stop,
    Cancel,
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Outcome {
    pub action: Option<Action>,
    /// Swallow this key event so it doesn't reach the focused app.
    pub suppress: bool,
    /// Send a harmless dummy key before this event, so that releasing Win or Alt
    /// doesn't open the Start menu or an app's menu bar.
    pub mask: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Mode {
    Idle,
    Holding(Instant),
    HandsFree,
    /// Stopped or cancelled; waiting for the chord to be fully released.
    WaitRelease,
}

pub struct Machine {
    chord: Chord,
    down: HashSet<u16>,
    mode: Mode,
    /// The chord fired during the current press, so releasing Win/Alt needs masking.
    fired: bool,
}

impl Machine {
    pub fn new(chord: Chord) -> Self {
        Self { chord, down: HashSet::new(), mode: Mode::Idle, fired: false }
    }

    #[cfg(test)]
    fn is_active(&self) -> bool {
        matches!(self.mode, Mode::Holding(_) | Mode::HandsFree)
    }

    /// Forgets keys the OS says are no longer pressed (guards against key-up
    /// events that never arrived, e.g. across the UAC secure desktop).
    pub fn retain_down(&mut self, mut still_down: impl FnMut(u16) -> bool) {
        self.down.retain(|&vk| still_down(vk));
    }

    pub fn on_key(&mut self, vk: u16, is_down: bool, now: Instant) -> Outcome {
        let repeat = is_down && self.down.contains(&vk);
        if is_down {
            self.down.insert(vk);
        } else {
            self.down.remove(&vk);
        }
        let in_chord = self.chord.contains(vk);
        let held = self.chord.held(&self.down);
        let mut out = Outcome::default();

        match self.mode {
            Mode::Idle => {
                if is_down && in_chord && held {
                    self.mode = Mode::Holding(now);
                    self.fired = true;
                    out.action = Some(Action::Start);
                }
            }
            Mode::Holding(since) => {
                if is_down && !in_chord && !repeat {
                    self.mode = Mode::WaitRelease;
                    out.action = Some(Action::Cancel);
                } else if !held {
                    if now.duration_since(since) < TAP_THRESHOLD {
                        self.mode = Mode::HandsFree;
                    } else {
                        self.mode = Mode::Idle;
                        out.action = Some(Action::Stop);
                    }
                }
            }
            Mode::HandsFree => {
                if is_down && vk == VK_ESCAPE {
                    self.mode = Mode::WaitRelease;
                    out.action = Some(Action::Cancel);
                    out.suppress = true;
                } else if is_down && in_chord && held {
                    self.mode = Mode::WaitRelease;
                    self.fired = true;
                    out.action = Some(Action::Stop);
                }
            }
            Mode::WaitRelease => {
                if !held && !self.chord.groups.iter().flatten().any(|k| self.down.contains(k)) {
                    self.mode = Mode::Idle;
                }
            }
        }

        if in_chord && !is_modifier(vk) && (self.mode != Mode::Idle || out.action.is_some()) {
            out.suppress = true;
        }
        if !is_down && in_chord && self.fired && matches!(vk, VK_LWIN | VK_RWIN | VK_LMENU | VK_RMENU) {
            out.mask = true;
        }
        if !self.chord.groups.iter().flatten().any(|k| self.down.contains(k)) {
            self.fired = false;
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LCTRL: u16 = 0xA2;
    const LWIN: u16 = 0x5B;
    const KEY_D: u16 = 0x44;
    const SPACE: u16 = 0x20;

    struct Sim {
        m: Machine,
        t: Instant,
    }

    impl Sim {
        fn new(spec: &str) -> Self {
            Self { m: Machine::new(Chord::parse(spec).unwrap()), t: Instant::now() }
        }
        fn wait(&mut self, ms: u64) {
            self.t += Duration::from_millis(ms);
        }
        fn down(&mut self, vk: u16) -> Outcome {
            self.m.on_key(vk, true, self.t)
        }
        fn up(&mut self, vk: u16) -> Outcome {
            self.m.on_key(vk, false, self.t)
        }
    }

    #[test]
    fn parse_chords() {
        let c = Chord::parse("ctrl+win").unwrap();
        assert_eq!(c.groups, vec![vec![0xA2, 0xA3], vec![0x5B, 0x5C]]);
        assert_eq!(Chord::parse("F9").unwrap().groups, vec![vec![0x78]]);
        assert_eq!(Chord::parse("f1").unwrap().groups, vec![vec![0x70]]);
        assert_eq!(Chord::parse("right-alt").unwrap().groups, vec![vec![0xA5]]);
        assert_eq!(Chord::parse("ctrl + shift + d").unwrap().groups[2], vec![0x44]);
        assert!(Chord::parse("ctrl+banana").is_err());
        assert!(Chord::parse("f25").is_err());
        assert!(Chord::parse("ctrl+").is_err());
    }

    #[test]
    fn hold_to_talk() {
        let mut s = Sim::new("ctrl+win");
        assert_eq!(s.down(LCTRL).action, None);
        assert_eq!(s.down(LWIN).action, Some(Action::Start));
        s.wait(50);
        assert_eq!(s.down(LWIN).action, None); // auto-repeat is ignored
        s.wait(1500);
        let o = s.up(LWIN);
        assert_eq!(o.action, Some(Action::Stop));
        assert!(o.mask, "releasing Win must be masked to keep the Start menu closed");
        assert_eq!(s.up(LCTRL), Outcome::default());
        // works again afterwards, pressing in the other order
        assert_eq!(s.down(LWIN).action, None);
        assert_eq!(s.down(LCTRL).action, Some(Action::Start));
    }

    #[test]
    fn plain_win_press_is_not_masked() {
        let mut s = Sim::new("ctrl+win");
        s.down(LWIN);
        assert_eq!(s.up(LWIN), Outcome::default());
    }

    #[test]
    fn other_key_cancels() {
        let mut s = Sim::new("ctrl+win");
        s.down(LCTRL);
        s.down(LWIN);
        s.wait(100);
        let o = s.down(KEY_D);
        assert_eq!(o.action, Some(Action::Cancel));
        assert!(!o.suppress, "the user's shortcut must still reach Windows");
        s.wait(500);
        assert_eq!(s.up(KEY_D).action, None);
        assert_eq!(s.up(LWIN).action, None);
        assert_eq!(s.up(LCTRL).action, None);
        assert_eq!(s.down(LCTRL).action, None);
        assert_eq!(s.down(LWIN).action, Some(Action::Start));
    }

    #[test]
    fn tap_for_hands_free_then_tap_to_stop() {
        let mut s = Sim::new("ctrl+win");
        s.down(LCTRL);
        assert_eq!(s.down(LWIN).action, Some(Action::Start));
        s.wait(120);
        assert_eq!(s.up(LWIN).action, None); // quick tap: keep recording
        s.up(LCTRL);
        assert!(s.m.is_active());
        s.wait(3000);
        assert_eq!(s.down(0x41).action, None); // typing doesn't stop hands-free
        s.up(0x41);
        s.down(LCTRL);
        assert_eq!(s.down(LWIN).action, Some(Action::Stop));
        s.wait(80);
        assert_eq!(s.up(LWIN).action, None);
        s.up(LCTRL);
        assert!(!s.m.is_active());
        s.down(LCTRL);
        assert_eq!(s.down(LWIN).action, Some(Action::Start));
    }

    #[test]
    fn escape_cancels_hands_free() {
        let mut s = Sim::new("f9");
        s.down(0x78);
        s.wait(100);
        s.up(0x78);
        let o = s.down(VK_ESCAPE);
        assert_eq!(o.action, Some(Action::Cancel));
        assert!(o.suppress);
        s.up(VK_ESCAPE);
        assert_eq!(s.down(0x78).action, Some(Action::Start));
    }

    #[test]
    fn non_modifier_chord_keys_are_suppressed() {
        let mut s = Sim::new("ctrl+space");
        assert!(!s.down(LCTRL).suppress);
        let o = s.down(SPACE);
        assert_eq!(o.action, Some(Action::Start));
        assert!(o.suppress);
        assert!(s.down(SPACE).suppress); // repeats too
        s.wait(1000);
        let o = s.up(SPACE);
        assert_eq!(o.action, Some(Action::Stop));
        assert!(o.suppress);
        assert!(!s.up(LCTRL).suppress);
        // a lone space is typed normally
        assert!(!s.down(SPACE).suppress);
        assert!(!s.up(SPACE).suppress);
    }

    #[test]
    fn stale_keys_are_forgotten() {
        let mut s = Sim::new("ctrl+win");
        s.down(LCTRL); // its key-up gets lost
        s.m.retain_down(|_| false);
        assert_eq!(s.down(LWIN).action, None);
    }
}
