# Demo: mutate_check on the toy example in examples/ (five mutants, one per outcome).
# Build with vhs from the repository root of a clean checkout:
#   vhs docs/demo.tape
Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1100
Set Height 660
Set Padding 16
Set Framerate 10
Set TypingSpeed 10ms

# the first keystroke of a session can be lost: spend it on a throwaway key, then clear
Hide
Sleep 1s
Type "x"
Backspace
Ctrl+L
Sleep 500ms
Show

Type "python mutate_check.py examples/textstats.py examples/test_textstats.py examples/mutations.json"
Sleep 500ms
Enter
Sleep 6s
