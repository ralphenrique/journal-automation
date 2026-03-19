# Journal Automation

This script retypes journal text into the currently focused app by simulating keyboard input. It is intended for apps that do not allow normal copy and paste.

The script supports:

- macOS via `osascript` and System Events
- Windows via native `SendInput`
- text input from stdin, a text file, or a small GUI paste window

## Quick start

### macOS

Download and run:

```bash
curl -fsSL https://raw.githubusercontent.com/ralphenrique/journal-automation/main/main.py -o journal-automation.py
python3 journal-automation.py
```

Or use the GUI input window:

```bash
curl -fsSL https://raw.githubusercontent.com/ralphenrique/journal-automation/main/main.py -o journal-automation.py
python3 journal-automation.py --gui
```

Before the first real run, allow Terminal, iTerm, or the app running Python in:

`System Settings > Privacy & Security > Accessibility`

### Windows PowerShell

Download and run:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ralphenrique/journal-automation/main/main.py -OutFile journal-automation.py
py journal-automation.py
```

Or use the GUI input window:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ralphenrique/journal-automation/main/main.py -OutFile journal-automation.py
py journal-automation.py --gui
```

## Input modes

### 1. Standard stdin mode

Run the script, paste your journal text into the terminal, then finish input.

- macOS: press `Ctrl-D`
- Windows: press `Ctrl-Z`, then `Enter`

After that, switch to the target app during the countdown.

### 2. Text file mode

This avoids terminal EOF quirks entirely.

```bash
python3 main.py --text-file journal.txt
```

```powershell
py main.py --text-file journal.txt
```

### 3. GUI mode

This opens a small text window. Paste your text there, click `Start Typing`, then switch to the target app during the countdown.

```bash
python3 main.py --gui
```

```powershell
py main.py --gui
```

## Useful flags

- `--countdown 8`: give yourself more time to focus the target app
- `--event-delay 0.05`: slow typing down if the app misses characters
- `--chunk-size 20`: macOS only, reduce AppleScript chunk size if needed
- `--dry-run`: do not type anything, just validate the flow

Example:

```bash
python3 main.py --gui --countdown 8 --event-delay 0.05
```

## Standalone binaries (PyInstaller)

If you want a single executable that does not rely on a pre-installed Python interpreter, build with PyInstaller on each target platform (PyInstaller cannot cross-compile).

1. Install PyInstaller in any virtual environment or user site:

	```bash
	python3 -m pip install --upgrade pyinstaller
	```

	```powershell
	py -m pip install --upgrade pyinstaller
	```

2. From the repository root, run:

	```bash
	pyinstaller --clean --noconfirm packaging/journal-automation.spec
	```

	```powershell
	pyinstaller --clean --noconfirm packaging/journal-automation.spec
	```

3. Grab the bundled app from `dist/journal-automation/`:
	- macOS: `dist/journal-automation/journal-automation` (CLI binary). Codesign or notarize it if Gatekeeper blocks the first run.
	- Windows: `dist/journal-automation/journal-automation.exe` (console app).

4. Distribute the binary for that platform. Users can run the executable directly (e.g., `./journal-automation --gui` on macOS or `journal-automation.exe --gui` on Windows).

Each platform build embeds Python plus `tkinter`, so the countdown/GUI prompts still appear in a terminal window.

## Windows notes

Windows normally does not require a separate keyboard permission screen.

If typing does not work:

- make sure the target app is the active foreground window
- if the target app is running as Administrator, run the script as Administrator too
- some apps block synthetic input entirely, especially games, security-sensitive apps, password fields, and elevated system prompts

If needed, launch PowerShell as Administrator and run:

```powershell
py journal-automation.py
```

## macOS notes

If typing fails immediately on macOS, the usual cause is missing Accessibility permission for the host app running Python.

Grant access to Terminal, iTerm, VS Code, or whichever app is launching the script.
