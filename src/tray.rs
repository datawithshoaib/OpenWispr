//! Visual indicator for OpenWispr:
//! A sleek, minimal floating wave pill (inspired by Wispr Flow).
//! Displays only when recording, with smooth, subtle, flicker-free animated waves.

use std::sync::mpsc::{Receiver, Sender};
use std::sync::{Arc, atomic::{AtomicBool, AtomicU32, Ordering}};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::{
    BeginPaint, BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, CreateDIBSection, CreatePen,
    CreateSolidBrush, DeleteDC, DeleteObject, EndPaint, FillRect, InvalidateRect, PAINTSTRUCT,
    SelectObject, DIB_RGB_COLORS, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, PS_SOLID, SRCCOPY, RoundRect,
};
use windows_sys::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE,
    NIM_MODIFY, NOTIFYICONDATAW,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CreateIconIndirect, CreateWindowExW, DefWindowProcW, DestroyIcon, DispatchMessageW,
    GetMessageW, GetSystemMetrics, ICONINFO, MSG, RegisterClassExW, ShowWindow, TranslateMessage,
    WNDCLASSEXW, WM_ERASEBKGND, WM_PAINT, SM_CXSCREEN, SM_CYSCREEN,
    WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE,
    WS_POPUP, SW_HIDE, SW_SHOWNOACTIVATE, SetLayeredWindowAttributes,
    SetWindowPos, HWND_TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE,
};

type HIcon = *mut std::ffi::c_void;

const WM_TRAY: u32 = 0x0400 + 1;
const ICON_SIZE: i32 = 32;

// Ultra-compact, clean Wispr Flow capsule dimensions
const OVERLAY_W: i32 = 56;
const OVERLAY_H: i32 = 26;
const BOTTOM_OFFSET: i32 = 60; // Sits neatly above the Windows taskbar

#[derive(Debug)]
pub enum TrayCommand {
    StartListening,
    StopListening,
    Quit,
}

pub fn spawn_tray() -> Sender<TrayCommand> {
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::Builder::new()
        .name("ui-indicator".into())
        .spawn(move || ui_thread(rx))
        .expect("failed to spawn UI indicator thread");
    tx
}

// ── Colors (Win32 COLORREF format: 0x00BBGGRR) ──────────────────────────

const fn rgb(r: u8, g: u8, b: u8) -> u32 {
    (b as u32) << 16 | (g as u32) << 8 | r as u32
}

const CLR_TRANSPARENT: u32   = rgb(0x01, 0x01, 0x01); // Color-key for rounded transparency
const CLR_PILL_BG: u32       = rgb(15, 15, 20);       // Deep obsidian dark glass
const CLR_BORDER_ACTIVE: u32 = rgb(14, 165, 233);     // Electric cyan/sky glow
const CLR_WAVE_BAR: u32      = rgb(56, 189, 248);     // Vibrant cyan-blue wave bars

static OVERLAY_FRAME: AtomicU32 = AtomicU32::new(0);

/// Overlay window paint handler: double-buffered for 100% flicker-free rendering
unsafe extern "system" fn overlay_wndproc(hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
        match msg {
            WM_ERASEBKGND => 1, // Suppress default erase to prevent flicker/glitching
            WM_PAINT => {
                let mut ps: PAINTSTRUCT = std::mem::zeroed();
                let hdc = BeginPaint(hwnd, &mut ps);

                // ── Double-buffer in memory to eliminate all glitches & tearing ──
                let mem_dc = CreateCompatibleDC(hdc);
                let mem_bmp = CreateCompatibleBitmap(hdc, OVERLAY_W, OVERLAY_H);
                let old_bmp = SelectObject(mem_dc, mem_bmp);

                // 1. Transparency key fill
                let bg_brush = CreateSolidBrush(CLR_TRANSPARENT);
                let rc = RECT { left: 0, top: 0, right: OVERLAY_W, bottom: OVERLAY_H };
                FillRect(mem_dc, &rc, bg_brush);
                DeleteObject(bg_brush);

                let frame = OVERLAY_FRAME.load(Ordering::Relaxed);

                // 2. Rounded capsule body (13px radius = perfectly semicircular ends for 26px height)
                let pill_brush = CreateSolidBrush(CLR_PILL_BG);
                let pill_pen = CreatePen(PS_SOLID, 1, CLR_BORDER_ACTIVE);
                let ob = SelectObject(mem_dc, pill_brush);
                let op = SelectObject(mem_dc, pill_pen);
                RoundRect(mem_dc, 0, 0, OVERLAY_W, OVERLAY_H, 13, 13);
                SelectObject(mem_dc, ob);
                SelectObject(mem_dc, op);
                DeleteObject(pill_brush);
                DeleteObject(pill_pen);

                // 3. Relaxed, slow-speed sound wave bars (5 bars, calm & fluid breathing motion)
                let bar_brush = CreateSolidBrush(CLR_WAVE_BAR);
                let bar_pen = CreatePen(PS_SOLID, 1, CLR_WAVE_BAR);
                let ob = SelectObject(mem_dc, bar_brush);
                let op = SelectObject(mem_dc, bar_pen);

                let bar_w = 3;
                let bar_gap = 3;
                let start_x = 14;
                let center_y = OVERLAY_H / 2;

                for i in 0..5 {
                    // Gentle, relaxed pacing (0.075 speed is more than 2x slower and calmer)
                    let t = (frame as f32 * 0.075) + (i as f32 * 0.65);
                    let amp = 0.35 + 0.65 * t.sin().abs();
                    let half_h = (2.0 + amp * 4.5) as i32;
                    let bx = start_x + i * (bar_w + bar_gap);
                    RoundRect(mem_dc, bx, center_y - half_h, bx + bar_w, center_y + half_h, 2, 2);
                }

                SelectObject(mem_dc, ob);
                SelectObject(mem_dc, op);
                DeleteObject(bar_brush);
                DeleteObject(bar_pen);

                // 4. Blit the rendered buffer to screen in a single instant operation
                BitBlt(hdc, 0, 0, OVERLAY_W, OVERLAY_H, mem_dc, 0, 0, SRCCOPY);

                SelectObject(mem_dc, old_bmp);
                DeleteObject(mem_bmp);
                DeleteDC(mem_dc);

                EndPaint(hwnd, &ps);
                0
            }
            _ => DefWindowProcW(hwnd, msg, wparam, lparam),
        }
    }
}

// ── Tray Icon Software Rendering ──────────────────────────────────────────

fn render_idle_icon() -> Vec<u8> {
    let mut buf = vec![0u8; (ICON_SIZE * ICON_SIZE * 4) as usize];
    let w = ICON_SIZE as usize;
    let cx = w / 2;

    for y in 0..w {
        for x in 0..w {
            let idx = (y * w + x) * 4;
            let in_mic = x >= cx - 2 && x <= cx + 2 && y >= 7 && y <= 16;
            let in_stand = x >= cx - 1 && x <= cx + 1 && y > 19 && y <= 23;
            let in_base = x >= cx - 4 && x <= cx + 4 && y >= 23 && y <= 24;
            let dx = (x as f32) - cx as f32;
            let dy = (y as f32) - 7.0;
            let in_top = dx * dx + dy * dy <= 8.0 && y <= 8;

            if in_mic || in_top || in_stand || in_base {
                buf[idx] = 0x34; buf[idx + 1] = 0xD3; buf[idx + 2] = 0x99; buf[idx + 3] = 0xFF; // Emerald green
            }
        }
    }
    buf
}

fn render_wave_icon(phase: f32) -> Vec<u8> {
    let mut buf = vec![0u8; (ICON_SIZE * ICON_SIZE * 4) as usize];
    let w = ICON_SIZE as usize;
    let cx = w / 2;

    for y in 0..w {
        for x in 0..w {
            let idx = (y * w + x) * 4;
            let in_mic = x >= cx - 2 && x <= cx + 2 && y >= 7 && y <= 16;
            let in_stand = x >= cx - 1 && x <= cx + 1 && y > 19 && y <= 23;
            let in_base = x >= cx - 4 && x <= cx + 4 && y >= 23 && y <= 24;

            if in_mic || in_stand || in_base {
                buf[idx] = 0x0E; buf[idx + 1] = 0xA5; buf[idx + 2] = 0xE9; buf[idx + 3] = 0xFF; // Sky blue
            }
        }
    }

    let cx_f = cx as f32;
    let cy_f = 12.0;
    for wave_i in 0..3u32 {
        let r = 6.0 + wave_i as f32 * 3.5 + phase * 2.5;
        let alpha = ((1.0 - (phase + wave_i as f32 * 0.3) % 1.0) * 255.0).clamp(50.0, 255.0) as u8;
        for y in 0..w {
            for x in 0..w {
                let dx = x as f32 - cx_f;
                let dy = y as f32 - cy_f;
                let dist = (dx * dx + dy * dy).sqrt();
                if (dist - r).abs() <= 1.2 && dx.abs() > 2.5 {
                    let idx = (y * w + x) * 4;
                    if buf[idx + 3] == 0 {
                        buf[idx] = 0x38; buf[idx + 1] = 0xBD; buf[idx + 2] = 0xF8; buf[idx + 3] = alpha;
                    }
                }
            }
        }
    }
    buf
}

fn rgba_to_hicon(rgba: &[u8]) -> HIcon {
    unsafe {
        let hdc = CreateCompatibleDC(std::ptr::null_mut());
        let mut bmi: BITMAPINFO = std::mem::zeroed();
        bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bmi.bmiHeader.biWidth = ICON_SIZE;
        bmi.bmiHeader.biHeight = -ICON_SIZE;
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB;

        let mut bits: *mut std::ffi::c_void = std::ptr::null_mut();
        let color_bmp = CreateDIBSection(hdc, &bmi, DIB_RGB_COLORS, &mut bits, std::ptr::null_mut(), 0);

        if !bits.is_null() {
            let dst = std::slice::from_raw_parts_mut(bits as *mut u8, (ICON_SIZE * ICON_SIZE * 4) as usize);
            for i in (0..dst.len()).step_by(4) {
                let (r, g, b, a) = (rgba[i], rgba[i+1], rgba[i+2], rgba[i+3]);
                dst[i]   = (b as u16 * a as u16 / 255) as u8;
                dst[i+1] = (g as u16 * a as u16 / 255) as u8;
                dst[i+2] = (r as u16 * a as u16 / 255) as u8;
                dst[i+3] = a;
            }
        }

        let mask_size = ((ICON_SIZE + 15) / 16 * 2 * ICON_SIZE) as usize;
        let mut mask_bmi: BITMAPINFO = std::mem::zeroed();
        mask_bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        mask_bmi.bmiHeader.biWidth = ICON_SIZE;
        mask_bmi.bmiHeader.biHeight = -ICON_SIZE;
        mask_bmi.bmiHeader.biPlanes = 1;
        mask_bmi.bmiHeader.biBitCount = 1;
        mask_bmi.bmiHeader.biCompression = BI_RGB;

        let mut mask_bits: *mut std::ffi::c_void = std::ptr::null_mut();
        let mask_bmp = CreateDIBSection(hdc, &mask_bmi, DIB_RGB_COLORS, &mut mask_bits, std::ptr::null_mut(), 0);
        if !mask_bits.is_null() {
            std::ptr::write_bytes(mask_bits as *mut u8, 0, mask_size);
        }

        let icon_info = ICONINFO {
            fIcon: 1,
            xHotspot: 0,
            yHotspot: 0,
            hbmMask: mask_bmp,
            hbmColor: color_bmp,
        };
        let hicon = CreateIconIndirect(&icon_info);
        DeleteObject(color_bmp);
        DeleteObject(mask_bmp);
        DeleteDC(hdc);
        hicon
    }
}

// ── UI Indicator Thread (Overlay + Tray) ──────────────────────────────────

fn ui_thread(rx: Receiver<TrayCommand>) {
    unsafe {
        // 1. Message window for Tray
        let tray_class: Vec<u16> = "OpenWisprTray\0".encode_utf16().collect();
        let mut wc: WNDCLASSEXW = std::mem::zeroed();
        wc.cbSize = std::mem::size_of::<WNDCLASSEXW>() as u32;
        wc.lpfnWndProc = Some(DefWindowProcW);
        wc.lpszClassName = tray_class.as_ptr();
        RegisterClassExW(&wc);

        let tray_hwnd = CreateWindowExW(
            WS_EX_TOOLWINDOW,
            tray_class.as_ptr(),
            std::ptr::null(), 0,
            0, 0, 0, 0,
            std::ptr::null_mut(), std::ptr::null_mut(),
            std::ptr::null_mut(), std::ptr::null_mut(),
        );

        // 2. Compact floating Wispr Flow wave capsule
        let overlay_class: Vec<u16> = "OpenWisprOverlay\0".encode_utf16().collect();
        let mut oc: WNDCLASSEXW = std::mem::zeroed();
        oc.cbSize = std::mem::size_of::<WNDCLASSEXW>() as u32;
        oc.lpfnWndProc = Some(overlay_wndproc);
        oc.lpszClassName = overlay_class.as_ptr();
        RegisterClassExW(&oc);

        let scr_w = GetSystemMetrics(SM_CXSCREEN);
        let scr_h = GetSystemMetrics(SM_CYSCREEN);
        let ox = (scr_w - OVERLAY_W) / 2;
        let oy = scr_h - OVERLAY_H - BOTTOM_OFFSET;

        let overlay_hwnd = CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_NOACTIVATE,
            overlay_class.as_ptr(),
            std::ptr::null(),
            WS_POPUP,
            ox, oy, OVERLAY_W, OVERLAY_H,
            std::ptr::null_mut(), std::ptr::null_mut(),
            std::ptr::null_mut(), std::ptr::null_mut(),
        );

        // Clean alpha-keyed transparency
        SetLayeredWindowAttributes(overlay_hwnd, CLR_TRANSPARENT, 250, 0x01 | 0x02);
        SetWindowPos(overlay_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);

        // 3. System Tray Icon
        let idle_hicon = rgba_to_hicon(&render_idle_icon());
        let tooltip: Vec<u16> = "OpenWispr \u{2013} Ready (Ctrl+Win to speak)\0".encode_utf16().collect();
        let mut nid: NOTIFYICONDATAW = std::mem::zeroed();
        nid.cbSize = std::mem::size_of::<NOTIFYICONDATAW>() as u32;
        nid.hWnd = tray_hwnd;
        nid.uID = 1;
        nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
        nid.uCallbackMessage = WM_TRAY;
        nid.hIcon = idle_hicon;
        for (i, &ch) in tooltip.iter().enumerate().take(127) {
            nid.szTip[i] = ch;
        }
        Shell_NotifyIconW(NIM_ADD, &nid);

        // Hidden when idle: pops up only when recording
        ShowWindow(overlay_hwnd, SW_HIDE);

        let listening = Arc::new(AtomicBool::new(false));
        let listening2 = listening.clone();
        let quit = Arc::new(AtomicBool::new(false));
        let quit2 = quit.clone();

        let tray_hwnd_val = tray_hwnd as usize;
        std::thread::spawn(move || {
            for cmd in rx {
                match cmd {
                    TrayCommand::StartListening => listening2.store(true, Ordering::SeqCst),
                    TrayCommand::StopListening => listening2.store(false, Ordering::SeqCst),
                    TrayCommand::Quit => {
                        quit2.store(true, Ordering::SeqCst);
                        break;
                    }
                }
                windows_sys::Win32::UI::WindowsAndMessaging::PostMessageW(
                    tray_hwnd_val as HWND, 0x0400 + 2, 0, 0,
                );
            }
        });

        let mut frame: u32 = 0;
        let mut current_hicon = idle_hicon;
        let mut was_listening = false;

        // 30 fps smooth timer
        windows_sys::Win32::UI::WindowsAndMessaging::SetTimer(tray_hwnd, 1, 33, None);

        let mut msg: MSG = std::mem::zeroed();
        loop {
            if quit.load(Ordering::SeqCst) {
                break;
            }
            let ret = GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0);
            if ret <= 0 { break; }
            TranslateMessage(&msg);
            DispatchMessageW(&msg);

            let is_listening = listening.load(Ordering::SeqCst);

            // ── Show / Hide floating capsule on state change ──
            if is_listening && !was_listening {
                frame = 0;
                OVERLAY_FRAME.store(0, Ordering::Relaxed);
                ShowWindow(overlay_hwnd, SW_SHOWNOACTIVATE);
            } else if !is_listening && was_listening {
                ShowWindow(overlay_hwnd, SW_HIDE);
            }

            if is_listening {
                frame = frame.wrapping_add(1);
                OVERLAY_FRAME.store(frame, Ordering::Relaxed);
                InvalidateRect(overlay_hwnd, std::ptr::null(), 0);
            }
            was_listening = is_listening;

            // ── Update System Tray Icon ──
            if is_listening && frame % 4 == 0 {
                let phase = (frame % 30) as f32 / 30.0;
                let new_icon = rgba_to_hicon(&render_wave_icon(phase));
                let tip: Vec<u16> = "OpenWispr \u{2013} Listening...\0".encode_utf16().collect();
                nid.hIcon = new_icon;
                for i in 0..128 { nid.szTip[i] = 0; }
                for (i, &ch) in tip.iter().enumerate().take(127) { nid.szTip[i] = ch; }
                Shell_NotifyIconW(NIM_MODIFY, &nid);
                if current_hicon != idle_hicon { DestroyIcon(current_hicon); }
                current_hicon = new_icon;
            } else if !is_listening && current_hicon != idle_hicon {
                nid.hIcon = idle_hicon;
                let tip: Vec<u16> = "OpenWispr \u{2013} Ready (Ctrl+Win to speak)\0".encode_utf16().collect();
                for i in 0..128 { nid.szTip[i] = 0; }
                for (i, &ch) in tip.iter().enumerate().take(127) { nid.szTip[i] = ch; }
                Shell_NotifyIconW(NIM_MODIFY, &nid);
                DestroyIcon(current_hicon);
                current_hicon = idle_hicon;
            }
        }

        // Cleanup
        ShowWindow(overlay_hwnd, SW_HIDE);
        Shell_NotifyIconW(NIM_DELETE, &nid);
        if current_hicon != idle_hicon { DestroyIcon(current_hicon); }
        DestroyIcon(idle_hicon);
    }
}
