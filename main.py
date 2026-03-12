#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time


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
	ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


	class KEYBDINPUT(ctypes.Structure):
		_fields_ = [
			("wVk", ctypes.c_ushort),
			("wScan", ctypes.c_ushort),
			("dwFlags", ctypes.c_ulong),
			("time", ctypes.c_ulong),
			("dwExtraInfo", ULONG_PTR),
		]


	class _INPUTUNION(ctypes.Union):
		_fields_ = [("ki", KEYBDINPUT)]


	class INPUT(ctypes.Structure):
		_fields_ = [
			("type", ctypes.c_ulong),
			("union", _INPUTUNION),
		]


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description=(
			"Paste journal text into stdin, switch to your target app during the "
			"countdown, and let the script retype it as keyboard input."
		)
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
	return ctypes.windll.user32.SendInput(len(inputs), input_array, ctypes.sizeof(INPUT))


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
		raise OSError("SendInput failed for virtual key event.")


def windows_send_unicode_character(character: str) -> None:
	code_point = ord(character)
	if code_point > 0xFFFF:
		raise ValueError("Characters outside the Basic Multilingual Plane are not supported on Windows.")

	sent = windows_send_input(
		windows_key_input(scan_code=code_point, flags=KEYEVENTF_UNICODE),
		windows_key_input(scan_code=code_point, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
	)
	if sent != 2:
		raise OSError("SendInput failed for Unicode character event.")


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

	text = read_stdin_text()
	if not text:
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
