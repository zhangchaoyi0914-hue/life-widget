' Life Widget launcher - runs the widget without a console window.
' Tries the Python launcher (pyw.exe) first, falls back to pythonw.exe on PATH.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("Wscript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
script = here & "\life_widget.py"
q = Chr(34)
shell.CurrentDirectory = here

On Error Resume Next
shell.Run "pyw.exe " & q & script & q, 0, False
If Err.Number <> 0 Then
    Err.Clear
    shell.Run "pythonw.exe " & q & script & q, 0, False
End If
On Error GoTo 0
