# Filanex — Claude Code instructions

## Giving me commands to run (hard rule, every command, every session)
Assume I am NOT a developer and may paste from any shell, any window, any current
directory. Treat every command as if it will be pasted blind:
- **Paste-once.** Give ONE block I paste a single time and press Enter — never a
  sequence of "run this, then that, then check where you are."
- **Location-independent.** Never assume my current directory or which window is open.
  Use absolute paths, or make the command find things itself. Never tell me "make sure
  you're in folder X" — put the full path in the command.
- **No fragile multi-line pastes.** Multi-line pastes into PowerShell get mangled by the
  `>>` continuation. Use one physical line (joined with `;`), or have me save a script
  file and run it. Never hand me a loose multi-line block to paste line-by-line.
- **Self-checking.** Detect what you depend on instead of assuming it, and stop at the
  FIRST real failure with a clear message instead of cascading.
- **Tell me what to send back.** End with what success looks like and exactly which
  output to copy if it fails.
- **Assume non-expert.** No unstated setup steps, no jargon-only instructions.

If a command can't meet this, fix the command — don't hand me something that needs me to
already be in the right place.
