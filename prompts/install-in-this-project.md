Install encode-the-correction into this project and use it from now on.

The tool: https://github.com/somethingwentwell/encode-the-correction

1. Choose how you will run the tool. Use the first option that works here:
   a. `encode-the-correction --version` already works: use `encode-the-correction`.
   b. You may install command-line tools on this machine: run
      `pipx install git+https://github.com/somethingwentwell/encode-the-correction.git`
      (for a local clone: `pipx install /path/to/encode-the-correction`; without pipx,
      `python3 -m pip install` the same thing inside a virtual environment), then use
      `encode-the-correction`.
   c. Otherwise clone the repository into a temporary directory outside this project
      (or use a local clone as is) and run `python3 <clone>/encode_the_correction.py`.
      Do not copy the repository into this project.
2. From the project root run `encode-the-correction adopt .` (option c:
   `python3 <clone>/encode_the_correction.py adopt .`). If you are running in Claude Code,
   Cursor, OpenAI Codex or GitHub Copilot, add --hooks claude-code, --hooks cursor,
   --hooks codex or --hooks copilot for your own runtime only. adopt merges into existing
   files, preserves their content, and installs a project skill at
   `.agents/skills/encode-the-correction/` (and `.claude/skills/encode-the-correction/`
   for Claude Code) so you load these instructions automatically next time.
3. Run `encode-the-correction check` and fix any failure at its root cause. If the
   `encode-the-correction` command is not installed, run the copy that adopt put in the
   project instead: `python3 .encode-the-correction/cli.py check`. Both take the same arguments.
4. From now on follow the encode-the-correction skill and the "Encode the correction"
   section in AGENTS.md: run `check` at the start of each session and before finishing a
   task; whenever I correct you, encode the correction with `encode-the-correction encode ...`
   (or `python3 .encode-the-correction/cli.py encode ...`) and show me `check` failing on
   the mistake and passing after the fix, instead of adding a rule or comment.
5. Tell me which files were created or changed. Do not commit or push unless I ask.
