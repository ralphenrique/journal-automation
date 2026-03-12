#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time
from ctypes import wintypes

try:
	import tkinter as tk
except ImportError:
	tk = None


RETURN_KEY_CODE = 36
TAB_KEY_CODE = 48
VK_RETURN = 0x0D
VK_TAB = 0x09
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"


if IS_WINDOWS:
	ULONG_PTR = wintypes.WPARAM


	class MOUSEINPUT(ctypes.Structure):
		_fields_ = [
			("dx", wintypes.LONG),
			("dy", wintypes.LONG),
			("mouseData", wintypes.DWORD),
			("dwFlags", wintypes.DWORD),
			("time", wintypes.DWORD),
			("dwExtraInfo", ULONG_PTR),
		]


	class KEYBDINPUT(ctypes.Structure):
		_fields_ = [
			("wVk", wintypes.WORD),
			("wScan", wintypes.WORD),
			("dwFlags", wintypes.DWORD),
			("time", wintypes.DWORD),
			("dwExtraInfo", ULONG_PTR),
		]


	class HARDWAREINPUT(ctypes.Structure):
		_fields_ = [
			("uMsg", wintypes.DWORD),
			("wParamL", wintypes.WORD),
			("wParamH", wintypes.WORD),
		]


	class _INPUTUNION(ctypes.Union):
		_fields_ = [
			("mi", MOUSEINPUT),
			("ki", KEYBDINPUT),
			("hi", HARDWAREINPUT),
		]


	class INPUT(ctypes.Structure):
		_fields_ = [
			("type", wintypes.DWORD),
			("union", _INPUTUNION),
		]


	USER32 = ctypes.WinDLL("user32", use_last_error=True)
	SEND_INPUT = USER32.SendInput
	SEND_INPUT.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
	SEND_INPUT.restype = wintypes.UINT


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description=(
			"Paste journal text into stdin, switch to your target app during the "
			"countdown, and let the script retype it as keyboard input."
		)
	)
	parser.add_argument(
		"--text-file",
		type=Path,
		help="Read journal text from a file instead of stdin.",
	)
	parser.add_argument(
		"--gui",
		action="store_true",
		help="Open a small paste window instead of reading journal text from stdin.",
	)
	parser.add_argument(
		"--countdown",
		type=float,
		default=5.0,
		help="Seconds to wait before typing starts.",
	)
	parser.add_argument(
		"--chunk-size",
		type=int,
		default=35,
		help="Maximum number of plain characters to send per event on AppleScript backends.",
	)
	parser.add_argument(
		"--event-delay",
		type=float,
		default=0.03,
		help="Delay between synthetic typing events.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Print what would happen without sending keystrokes.",
	)
	return parser.parse_args()


def run_osascript(lines: list[str]) -> subprocess.CompletedProcess[str]:
	command = ["osascript"]
	for line in lines:
		command.extend(["-e", line])
	return subprocess.run(command, capture_output=True, text=True, check=False)


def windows_send_input(*inputs: INPUT) -> int:
	input_array = (INPUT * len(inputs))(*inputs)
	return SEND_INPUT(len(inputs), input_array, ctypes.sizeof(INPUT))


def windows_key_input(*, virtual_key: int = 0, scan_code: int = 0, flags: int = 0) -> INPUT:
	return INPUT(
		type=INPUT_KEYBOARD,
		union=_INPUTUNION(
			ki=KEYBDINPUT(
				wVk=virtual_key,
				wScan=scan_code,
				dwFlags=flags,
				time=0,
				dwExtraInfo=0,
			)
		),
	)


def windows_send_virtual_key(virtual_key: int) -> None:
	sent = windows_send_input(
		windows_key_input(virtual_key=virtual_key),
		windows_key_input(virtual_key=virtual_key, flags=KEYEVENTF_KEYUP),
	)
	if sent != 2:
		raise_windows_send_input_error("virtual key event")


def raise_windows_send_input_error(event_name: str) -> None:
	error_code = ctypes.get_last_error()
	if error_code:
		raise OSError(error_code, f"SendInput failed for {event_name}.")
	raise OSError(
		f"SendInput failed for {event_name}. Windows may be blocking synthetic input to the target app. "
		"Try focusing the target window or running the script with the same privilege level as the target app."
	)


def windows_send_unicode_character(character: str) -> None:
	code_point = ord(character)
	if code_point > 0xFFFF:
		raise ValueError("Characters outside the Basic Multilingual Plane are not supported on Windows.")

	sent = windows_send_input(
		windows_key_input(scan_code=code_point, flags=KEYEVENTF_UNICODE),
		windows_key_input(scan_code=code_point, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
	)
	if sent != 2:
		raise_windows_send_input_error("Unicode character event")


def windows_type_text(text: str, event_delay: float) -> None:
	normalized_text = normalize_input_text(text)
	for character in normalized_text:
		if character == "\n":
			windows_send_virtual_key(VK_RETURN)
		elif character == "\t":
			windows_send_virtual_key(VK_TAB)
		else:
			windows_send_unicode_character(character)
		if event_delay > 0:
			time.sleep(event_delay)


def accessibility_enabled() -> bool:
	result = run_osascript([
		'tell application "System Events" to return UI elements enabled'
	])
	return result.returncode == 0 and result.stdout.strip().lower() == "true"


def windows_input_desktop_ready() -> bool:
	return bool(ctypes.windll.user32.GetForegroundWindow())


def escape_applescript_text(value: str) -> str:
	return value.replace("\\", "\\\\").replace('"', '\\"')


def build_typing_commands(text: str, chunk_size: int, event_delay: float) -> list[str]:
	normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
	commands = ['tell application "System Events"']
	buffer: list[str] = []

	def flush_buffer() -> None:
		if not buffer:
			return
		chunk = "".join(buffer)
		commands.append(f'keystroke "{escape_applescript_text(chunk)}"')
		commands.append(f"delay {event_delay}")
		buffer.clear()

	for character in normalized_text:
		if character == "\n":
			flush_buffer()
			commands.append(f"key code {RETURN_KEY_CODE}")
			commands.append(f"delay {event_delay}")
			continue
		if character == "\t":
			flush_buffer()
			commands.append(f"key code {TAB_KEY_CODE}")
			commands.append(f"delay {event_delay}")
			continue

		buffer.append(character)
		if len(buffer) >= chunk_size:
			flush_buffer()

	flush_buffer()
	commands.append("end tell")
	return commands


def read_stdin_text() -> str:
	eof_hint = "Ctrl-Z then Enter" if IS_WINDOWS else "Ctrl-D"
	print(f"Paste your journal text below. Press {eof_hint} when finished.\n", file=sys.stderr)
	try:
		return normalize_input_text(sys.stdin.read())
	except KeyboardInterrupt:
		print("\nCancelled.", file=sys.stderr)
		raise SystemExit(130) from None


def normalize_input_text(text: str) -> str:
	normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
	if IS_WINDOWS:
		normalized_text = normalized_text.replace("\x1a", "")

	filtered_characters: list[str] = []
	for character in normalized_text:
		if character in {"\n", "\t"} or ord(character) >= 32:
			filtered_characters.append(character)
	return "".join(filtered_characters)


def read_text_file(path: Path) -> str:
	try:
		return normalize_input_text(path.read_text(encoding="utf-8"))
	except FileNotFoundError:
		print(f"Text file not found: {path}", file=sys.stderr)
		raise SystemExit(1) from None
	except OSError as exc:
		print(f"Unable to read text file {path}: {exc}", file=sys.stderr)
		raise SystemExit(1) from None


def read_gui_text() -> str:
	if tk is None:
		print(
			"The GUI mode requires tkinter, but it is not available in this Python installation.",
			file=sys.stderr,
		)
		raise SystemExit(1) from None

	state = {"submitted": False, "text": ""}
	root = tk.Tk()
	root.title("Journal Automation")
	root.geometry("760x520")
	root.minsize(520, 360)

	instructions = tk.Label(
		root,
		text="Paste your journal text below, then click Start Typing.",
		anchor="w",
		justify="left",
		padx=14,
		pady=12,
	)
	instructions.pack(fill="x")

	text_widget = tk.Text(root, wrap="word", undo=True)
	text_widget.pack(fill="both", expand=True, padx=14, pady=(0, 12))
	text_widget.focus_set()

	button_frame = tk.Frame(root)
	button_frame.pack(fill="x", padx=14, pady=(0, 14))

	def submit() -> None:
		state["submitted"] = True
		state["text"] = normalize_input_text(text_widget.get("1.0", "end-1c"))
		root.destroy()

	def cancel() -> None:
		root.destroy()

	start_button = tk.Button(button_frame, text="Start Typing", command=submit)
	start_button.pack(side="right")

	cancel_button = tk.Button(button_frame, text="Cancel", command=cancel)
	cancel_button.pack(side="right", padx=(0, 8))

	root.bind("<Command-Return>", lambda _event: submit())
	root.bind("<Control-Return>", lambda _event: submit())
	root.protocol("WM_DELETE_WINDOW", cancel)
	root.mainloop()

	if not state["submitted"]:
		print("Cancelled.", file=sys.stderr)
		raise SystemExit(130) from None
	return state["text"]


def get_input_text(args: argparse.Namespace) -> str:
	if args.text_file is not None and args.gui:
		print("Use either --text-file or --gui, not both.", file=sys.stderr)
		raise SystemExit(2) from None
	if args.text_file is not None:
		return read_text_file(args.text_file)
	if args.gui:
		return read_gui_text()
	return read_stdin_text()


def type_text(text: str, chunk_size: int, event_delay: float) -> tuple[int, str | None]:
	if IS_MACOS:
		commands = build_typing_commands(text, chunk_size=chunk_size, event_delay=event_delay)
		result = run_osascript(commands)
		if result.returncode != 0:
			error_output = result.stderr.strip() or result.stdout.strip() or "Unknown AppleScript error."
			return len(commands) - 2, error_output
		return len(commands) - 2, None

	if IS_WINDOWS:
		normalized_text = normalize_input_text(text)
		event_count = len(normalized_text)
		try:
			windows_type_text(text, event_delay=event_delay)
		except (OSError, ValueError) as exc:
			return event_count, str(exc)
		return event_count, None

	raise RuntimeError(f"Unsupported platform: {sys.platform}")


def environment_ready() -> tuple[bool, str | None]:
	if IS_MACOS:
		if accessibility_enabled():
			return True, None
		return (
			False,
			"macOS Accessibility access is not enabled for the app running this script.\n"
			"Grant access in System Settings > Privacy & Security > Accessibility, then try again.",
		)

	if IS_WINDOWS:
		if windows_input_desktop_ready():
			return True, None
		return False, "Windows did not report an active foreground window to receive keyboard input."

	return False, f"Unsupported platform: {sys.platform}. This script currently supports macOS and Windows."


def countdown(seconds: float) -> None:
	if seconds <= 0:
		return

	whole_seconds = int(seconds)
	remainder = seconds - whole_seconds
	for seconds_left in range(whole_seconds, 0, -1):
		print(f"Typing starts in {seconds_left}...", file=sys.stderr)
		time.sleep(1)
	if remainder > 0:
		time.sleep(remainder)


def main() -> int:
	args = parse_args()

	if args.chunk_size <= 0:
		print("--chunk-size must be greater than 0.", file=sys.stderr)
		return 2
	if args.event_delay < 0:
		print("--event-delay cannot be negative.", file=sys.stderr)
		return 2
	if args.countdown < 0:
		print("--countdown cannot be negative.", file=sys.stderr)
		return 2

	text = get_input_text(args)
	if not text:
		if args.text_file is not None:
			print("No text received from --text-file.", file=sys.stderr)
		elif args.gui:
			print("No text received from the GUI window.", file=sys.stderr)
		else:
			eof_hint = "Ctrl-Z then Enter" if IS_WINDOWS else "Ctrl-D"
			print(f"No text received. Paste text into stdin before pressing {eof_hint}.", file=sys.stderr)
		return 1

	printable_length = len(text.replace("\r", ""))
	if IS_MACOS:
		event_count = len(build_typing_commands(text, chunk_size=args.chunk_size, event_delay=args.event_delay)) - 2
	elif IS_WINDOWS:
		event_count = len(normalize_input_text(text))
	else:
		print(f"Unsupported platform: {sys.platform}. This script currently supports macOS and Windows.", file=sys.stderr)
		return 1
	print(
		f"Prepared {printable_length} characters for typing across {event_count} events.",
		file=sys.stderr,
	)

	if args.dry_run:
		print("Dry run enabled. No keystrokes were sent.", file=sys.stderr)
		return 0

	ready, message = environment_ready()
	if not ready:
		print(message, file=sys.stderr)
		return 1

	print("Switch to your journal app now.", file=sys.stderr)
	countdown(args.countdown)
	_, error_output = type_text(text, chunk_size=args.chunk_size, event_delay=args.event_delay)
	if error_output:
		print(f"Typing failed: {error_output}", file=sys.stderr)
		return 1

	print("Typing complete.", file=sys.stderr)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
